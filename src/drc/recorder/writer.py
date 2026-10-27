"""Raw message writer: append-only JSONL, one file per (venue, channel, UTC hour).

Each line is `{"t": <recv_ns>, "m": <original message>}` or, for recorder events such as
connects and disconnects, `{"t": <recv_ns>, "ev": <name>, ...}` in the `_meta` channel.
Closed hours are compressed to `.jsonl.zst`; the live hour stays plain so a crash loses at
most the unflushed buffer.
"""

from __future__ import annotations

import calendar
import hashlib
import logging
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


class RawWriter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._files: dict[tuple[str, str], tuple[int, Path, IO[bytes]]] = {}
        self._last_ts = 0

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
        self._files[key] = (hour, path, fh)
        return fh

    def write(self, venue: str, channel: str, raw: str | bytes, ts_ns: int | None = None) -> None:
        """Store one exchange message verbatim. Non-JSON payloads are stored as strings."""
        ts_ns = self.now_ns() if ts_ns is None else ts_ns
        body = raw.encode() if isinstance(raw, str) else raw
        if body[:1] not in (b"{", b"[", b'"') and not _is_json_scalar(body):
            body = orjson.dumps(body.decode(errors="replace"))
        elif b"\n" in body:  # keep one record per line
            body = orjson.dumps(orjson.loads(body))
        self._handle(venue, channel, ts_ns).write(b'{"t":%d,"m":%s}\n' % (ts_ns, body))

    def event(self, venue: str, name: str, ts_ns: int | None = None, **fields: object) -> None:
        """Record a recorder-side event (connect, disconnect, ...) so gaps are explicit."""
        ts_ns = self.now_ns() if ts_ns is None else ts_ns
        line = orjson.dumps({"t": ts_ns, "ev": name, **fields})
        self._handle(venue, META_CHANNEL, ts_ns).write(line + b"\n")

    def flush(self) -> None:
        for _, _, fh in self._files.values():
            fh.flush()

    def close(self) -> None:
        for _, _, fh in self._files.values():
            fh.close()
        self._files.clear()

    def compress_closed(self, min_idle_secs: float = 0.0) -> int:
        """Compress every plain .jsonl that is not currently open. Returns files compressed.

        `min_idle_secs` additionally skips files modified more recently than that, for callers
        outside the recorder process (drc-sync) that cannot see which files are still open."""
        open_paths = {p for _, p, _ in self._files.values()}
        cctx = zstandard.ZstdCompressor(level=6)
        n = 0
        for path in stale_plain_files(self.root, min_idle_secs=min_idle_secs):
            if path in open_paths:
                continue
            tmp = path.with_name(path.name + ".zst.tmp")
            with path.open("rb") as src, tmp.open("wb") as dst:
                cctx.copy_stream(src, dst)
            # the plain file is the only copy until proven otherwise: read the compressed
            # copy back and compare content hashes before deleting anything
            if _sha256(path, compressed=False) != _sha256(tmp, compressed=True):
                tmp.unlink()
                continue
            tmp.replace(path.with_name(path.name + ".zst"))
            path.unlink()
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


def read_lines(path: Path):
    """Iterate decoded records from a .jsonl or .jsonl.zst file, skipping undecodable lines."""
    if path.suffix == ".zst":
        dctx = zstandard.ZstdDecompressor()
        with path.open("rb") as fh, dctx.stream_reader(fh) as reader:
            lines = reader.read().splitlines()
    else:
        with path.open("rb") as fh:
            lines = fh.readlines()
    for i, line in enumerate(lines):
        try:
            yield orjson.loads(line)
        except orjson.JSONDecodeError:
            log.warning("%s line %d is not valid JSON; skipped", path, i + 1)
