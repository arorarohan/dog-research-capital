"""Raw message writer: append-only JSONL, one file per (venue, channel, UTC hour).

Each line is `{"t": <recv_ns>, "m": <original message>}` or, for recorder events such as
connects and disconnects, `{"t": <recv_ns>, "ev": <name>, ...}` in the `_meta` channel.
Closed hours are compressed to `.jsonl.zst`; the live hour stays plain so a crash loses at
most the unflushed buffer.

With `threaded=True` (what `drc-record` uses) the file I/O runs on a background thread: `write`
and `event` stamp the receive time and enqueue, so a slow disk can never stall a websocket
reader on the event loop (a stalled reader is how Polymarket's 1013 "slow consumer" closes
happen, #23). A failure on the writer thread is re-raised by the next `write`/`event`/`close`.
"""

from __future__ import annotations

import calendar
import hashlib
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import IO

import orjson
import zstandard

log = logging.getLogger(__name__)

NS_PER_HOUR = 3_600_000_000_000
META_CHANNEL = "_meta"


def hour_path(root: Path, venue: str, channel: str, ts_ns: int) -> Path:
    t = time.gmtime(ts_ns // 1_000_000_000)
    return root / venue / channel / time.strftime("%Y-%m-%d", t) / f"{t.tm_hour:02d}.jsonl"


_FLUSH = object()  # queue markers for the writer thread
_STOP = object()


class RawWriter:
    def __init__(self, root: Path, threaded: bool = False) -> None:
        self.root = Path(root)
        self._files: dict[tuple[str, str], tuple[int, Path, IO[bytes]]] = {}
        self._files_lock = threading.Lock()  # compress_closed reads _files from another thread
        self._last_ts = 0
        self._queue: queue.SimpleQueue | None = None
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self.max_backlog = 0  # deepest writer queue seen since the last `pop_max_backlog`
        if threaded:
            self._queue = queue.SimpleQueue()
            self._thread = threading.Thread(target=self._drain, name="raw-writer", daemon=True)
            self._thread.start()

    def now_ns(self) -> int:
        """Receive timestamp, strictly increasing across everything this writer records.

        The wall clock can step backwards (WSL2 does, by over a second, every minute or so).
        Downstream code sorts by this value to recover arrival order, so it must never go back.
        Absolute accuracy is only as good as the machine clock; use exchange timestamps for
        cross-venue alignment and latency.
        """
        self._last_ts = max(time.time_ns(), self._last_ts + 1)
        return self._last_ts

    def _handle(self, venue: str, channel: str, ts_ns: int) -> IO[bytes]:
        key = (venue, channel)
        hour = ts_ns // NS_PER_HOUR
        cur = self._files.get(key)
        if cur is not None and cur[0] == hour:
            return cur[2]
        if cur is not None:
            cur[2].close()
        path = hour_path(self.root, venue, channel, ts_ns)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = path.open("ab", buffering=1 << 16)
        with self._files_lock:
            self._files[key] = (hour, path, fh)
        return fh

    def write(self, venue: str, channel: str, raw: str | bytes, ts_ns: int | None = None) -> None:
        """Store one exchange message verbatim. Non-JSON payloads are stored as strings."""
        ts_ns = self.now_ns() if ts_ns is None else ts_ns
        if self._queue is None:
            self._write_msg(venue, channel, raw, ts_ns)
        else:
            self._enqueue((self._write_msg, venue, channel, raw, ts_ns))

    def event(self, venue: str, name: str, ts_ns: int | None = None, **fields: object) -> None:
        """Record a recorder-side event (connect, disconnect, ...) so gaps are explicit."""
        ts_ns = self.now_ns() if ts_ns is None else ts_ns
        line = orjson.dumps({"t": ts_ns, "ev": name, **fields})
        if self._queue is None:
            self._write_line(venue, META_CHANNEL, ts_ns, line)
        else:
            self._enqueue((self._write_line, venue, META_CHANNEL, ts_ns, line))

    def _write_msg(self, venue: str, channel: str, raw: str | bytes, ts_ns: int) -> None:
        body = raw.encode() if isinstance(raw, str) else raw
        if body[:1] not in (b"{", b"[", b'"') and not _is_json_scalar(body):
            body = orjson.dumps(body.decode(errors="replace"))
        elif b"\n" in body:  # keep one record per line
            body = orjson.dumps(orjson.loads(body))
        self._handle(venue, channel, ts_ns).write(b'{"t":%d,"m":%s}\n' % (ts_ns, body))

    def _write_line(self, venue: str, channel: str, ts_ns: int, line: bytes) -> None:
        self._handle(venue, channel, ts_ns).write(line + b"\n")

    def _enqueue(self, item) -> None:
        self._raise_if_failed()
        self._queue.put(item)
        self.max_backlog = max(self.max_backlog, self._queue.qsize())

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise RuntimeError("raw writer thread failed; nothing is being recorded") from (
                self._error
            )

    def _drain(self) -> None:
        """Writer thread: apply queued writes in order until _STOP."""
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            if self._error is not None:
                continue  # keep draining so memory does not grow; the error is re-raised
            try:
                if item is _FLUSH:
                    self._flush_files()
                else:
                    fn, *args = item
                    fn(*args)
            except BaseException as e:  # noqa: BLE001 - re-raised on the caller's thread
                log.exception("raw writer thread failed")
                self._error = e

    def pop_max_backlog(self) -> int:
        """Deepest writer-queue depth since the last call (always 0 when not threaded)."""
        n, self.max_backlog = self.max_backlog, 0
        return n

    def _flush_files(self) -> None:
        for _, _, fh in list(self._files.values()):
            fh.flush()

    def flush(self) -> None:
        """Push buffered lines to the OS. Threaded: queued behind pending writes, non-blocking."""
        if self._queue is None:
            self._flush_files()
        else:
            self._enqueue(_FLUSH)

    def close(self) -> None:
        """Write everything still queued, then close every file."""
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(_STOP)
            self._thread.join()
        for _, _, fh in list(self._files.values()):
            fh.close()
        with self._files_lock:
            self._files.clear()
        self._raise_if_failed()

    def compress_closed(self, min_idle_secs: float = 0.0) -> int:
        """Compress every plain .jsonl that is not currently open. Returns files compressed.

        `min_idle_secs` additionally skips files modified more recently than that, for callers
        outside the recorder process (drc-sync) that cannot see which files are still open."""
        with self._files_lock:
            open_paths = {p for _, p, _ in self._files.values()}
        cctx = zstandard.ZstdCompressor(level=6)
        n = 0
        for path in stale_plain_files(self.root, min_idle_secs=min_idle_secs):
            if path in open_paths:
                continue
            final = path.with_name(path.name + ".zst")
            # unique per process: drc-sync and the recorder's maintenance may race on one file
            tmp = path.with_name(f"{path.name}.zst.{os.getpid()}.tmp")
            try:
                with path.open("rb") as src, tmp.open("wb") as dst:
                    cctx.copy_stream(src, dst)
                # the plain file is the only copy until proven otherwise: read the compressed
                # copy back and compare content hashes before deleting anything
                if _sha256(path, compressed=False) != _sha256(tmp, compressed=True):
                    tmp.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                # the other process finished this file first; nothing left to do
                tmp.unlink(missing_ok=True)
                continue
            if final.exists():
                tmp.unlink(missing_ok=True)
                continue
            tmp.replace(final)
            path.unlink(missing_ok=True)
            n += 1
        return n


def stale_plain_files(
    root: Path, min_idle_secs: float = 0.0, now: float | None = None
) -> list[Path]:
    """Plain `.jsonl` files that are finished: their hour is over, and (optionally) nothing has
    written to them for `min_idle_secs`. Never the current hour: a restart would append to it."""
    now = time.time() if now is None else now
    current_hour = int(now * 1e9) // NS_PER_HOUR
    return [
        p
        for p in sorted(root.rglob("*.jsonl"))
        if _path_hour(p) < current_hour and now - p.stat().st_mtime >= min_idle_secs
    ]


def _sha256(path: Path, compressed: bool) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        stream = zstandard.ZstdDecompressor().stream_reader(fh) if compressed else fh
        while chunk := stream.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _path_hour(path: Path) -> int:
    """Hours-since-epoch of a `<date>/<HH>.jsonl` file."""
    day = calendar.timegm(time.strptime(path.parent.name, "%Y-%m-%d"))
    return day // 3600 + int(path.stem)


def _is_json_scalar(body: bytes) -> bool:
    try:
        orjson.loads(body)
    except orjson.JSONDecodeError:
        return False
    return True


def _iter_lines(fh, chunk_size: int = 1 << 20):
    """Lines of a binary stream, one at a time, holding about one chunk in memory."""
    tail = b""
    while chunk := fh.read(chunk_size):
        parts = (tail + chunk).split(b"\n")
        tail = parts.pop()
        yield from parts
    if tail:
        yield tail


def read_lines(path: Path, bad: list[int] | None = None):
    """Iterate decoded records from a .jsonl or .jsonl.zst file, skipping undecodable lines
    (their 1-based line numbers are appended to `bad` when given, so callers can count them).

    Lazy: an hour of Polymarket price changes is several GB decompressed (a 600 MB .zst on
    2026-10-30), so the file is read a chunk at a time, never whole."""
    with path.open("rb") as fh:
        if path.suffix == ".zst":
            with zstandard.ZstdDecompressor().stream_reader(fh) as reader:
                yield from _decode(_iter_lines(reader), path, bad)
        else:
            yield from _decode(_iter_lines(fh), path, bad)


def _decode(lines, path: Path, bad: list[int] | None = None):
    for i, line in enumerate(lines):
        try:
            yield orjson.loads(line)
        except orjson.JSONDecodeError:
            log.warning("%s line %d is not valid JSON; skipped", path, i + 1)
            if bad is not None:
                bad.append(i + 1)
