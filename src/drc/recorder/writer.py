"""Raw message writer: append-only JSONL, one file per (venue, channel, UTC hour).

Each line is `{"t": <recv_ns>, "m": <original message>}` or, for recorder events such as
connects and disconnects, `{"t": <recv_ns>, "ev": <name>, ...}` in the `_meta` channel.
Closed hours are compressed to `.jsonl.zst`; the live hour stays plain so a crash loses at
most the unflushed buffer.
"""

from __future__ import annotations

import calendar
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
        ts_ns = time.time_ns() if ts_ns is None else ts_ns
        body = raw.encode() if isinstance(raw, str) else raw
        if body[:1] not in (b"{", b"[", b'"') and not _is_json_scalar(body):
            body = orjson.dumps(body.decode(errors="replace"))
        elif b"\n" in body:  # keep one record per line
            body = orjson.dumps(orjson.loads(body))
        self._handle(venue, channel, ts_ns).write(b'{"t":%d,"m":%s}\n' % (ts_ns, body))

    def event(self, venue: str, name: str, ts_ns: int | None = None, **fields: object) -> None:
        """Record a recorder-side event (connect, disconnect, ...) so gaps are explicit."""
        ts_ns = time.time_ns() if ts_ns is None else ts_ns
        line = orjson.dumps({"t": ts_ns, "ev": name, **fields})
        self._handle(venue, META_CHANNEL, ts_ns).write(line + b"\n")

    def flush(self) -> None:
        for _, _, fh in self._files.values():
            fh.flush()

    def close(self) -> None:
        for _, _, fh in self._files.values():
            fh.close()
        self._files.clear()

    def compress_closed(self) -> int:
        """Compress every plain .jsonl that is not currently open. Returns files compressed."""
        open_paths = {p for _, p, _ in self._files.values()}
        current_hour = time.time_ns() // NS_PER_HOUR
        cctx = zstandard.ZstdCompressor(level=6)
        n = 0
        for path in self.root.rglob("*.jsonl"):
            # never touch the current hour: a restart would append to it
            if path in open_paths or _path_hour(path) >= current_hour:
                continue
            tmp = path.with_name(path.name + ".zst.tmp")
            with path.open("rb") as src, tmp.open("wb") as dst:
                cctx.copy_stream(src, dst)
            tmp.replace(path.with_name(path.name + ".zst"))
            path.unlink()
            n += 1
        return n


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
