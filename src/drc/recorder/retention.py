"""Retention: age-based pruning per (venue, channel), plus a total-size safety valve."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    venue: str
    channel: str
    max_age_days: float


def _date_dirs(root: Path, venue: str, channel: str) -> list[Path]:
    base = root / venue / channel
    return sorted(p for p in base.glob("*-*-*") if p.is_dir()) if base.is_dir() else []


def _dir_age_days(day_dir: Path, now: float) -> float:
    day_start = time.mktime(time.strptime(day_dir.name, "%Y-%m-%d")) - time.timezone
    return (now - day_start) / 86400  # age of the *start* of that UTC day


def _size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def apply_retention(
    root: Path, rules: list[Rule], max_total_gb: float | None = None, now: float | None = None
) -> list[Path]:
    """Delete expired day-directories. Returns what was removed.

    A day is removed once the *end* of that UTC day is older than `max_age_days`. If the data
    directory is still over `max_total_gb`, the oldest days of channels that have a rule are
    removed until it fits. Channels without a rule (trades, catalogs, meta) are never pruned.
    """
    now = time.time() if now is None else now
    removed: list[Path] = []
    for rule in rules:
        for day_dir in _date_dirs(root, rule.venue, rule.channel):
            if _dir_age_days(day_dir, now) - 1 > rule.max_age_days:
                shutil.rmtree(day_dir)
                removed.append(day_dir)

    if max_total_gb is not None:
        limit = max_total_gb * 1e9
        total = _size(root) if root.is_dir() else 0
        prunable = sorted(
            (d for r in rules for d in _date_dirs(root, r.venue, r.channel)), key=lambda d: d.name
        )
        for day_dir in prunable:
            if total <= limit:
                break
            total -= _size(day_dir)
            shutil.rmtree(day_dir)
            removed.append(day_dir)
            log.warning("size cap exceeded; removed %s", day_dir)
    for p in removed:
        log.info("retention removed %s", p)
    return removed
