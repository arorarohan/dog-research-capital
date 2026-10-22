from __future__ import annotations

import calendar
import time


def iso_to_ns(s: str) -> int:
    """Parse `2026-10-22T18:15:46.380546Z` (0-9 fractional digits) to epoch nanoseconds."""
    s = s.rstrip("Z")
    whole, _, frac = s.partition(".")
    secs = calendar.timegm(time.strptime(whole, "%Y-%m-%dT%H:%M:%S"))
    return secs * 1_000_000_000 + int((frac + "000000000")[:9])


def ms_to_ns(ms: str | int) -> int:
    return int(ms) * 1_000_000
