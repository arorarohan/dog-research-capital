"""Per-hour accounting of what normalization did with every raw message (#20).

Every raw line of every channel file ends up in exactly one bucket:
- `parsed`:      the parser produced at least one row
- `empty`:       parsed fine but the message legitimately carries no rows (e.g. `events: []`);
                 an explained, counted ignore
- `unparseable`: the parser raised (unknown message shape, unknown enum value, missing field)
- `bad_lines`:   the line was not valid JSON

`unparseable`, `bad_lines`, and raw channels nobody declared are *unexplained loss*: they make
the hour fail loudly. The accounting is written next to the Parquet as
`<norm>/_accounting/venue=V/date=D/hour=HH.json` so it survives, and is re-reported on later runs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_EXAMPLES = 3


@dataclass
class ChannelStats:
    raw: int = 0
    parsed: int = 0
    empty: int = 0
    unparseable: int = 0
    bad_lines: int = 0
    rows: int = 0
    examples: list[str] = field(default_factory=list)  # first few unparseable messages

    def balanced(self) -> bool:
        return self.raw == self.parsed + self.empty + self.unparseable


@dataclass
class HourStats:
    channels: dict[str, ChannelStats] = field(default_factory=dict)
    unread_channels: list[str] = field(default_factory=list)  # raw present, no parser, undeclared
    rows: dict[str, int] = field(default_factory=dict)  # table -> rows written

    def channel(self, name: str) -> ChannelStats:
        return self.channels.setdefault(name, ChannelStats())

    def problems(self) -> list[str]:
        out = []
        for name, c in sorted(self.channels.items()):
            if c.unparseable:
                out.append(f"{name}: {c.unparseable} unparseable of {c.raw} (e.g. {c.examples})")
            if c.bad_lines:
                out.append(f"{name}: {c.bad_lines} lines are not valid JSON")
            if not c.balanced():
                out.append(f"{name}: accounting does not balance ({c})")
        table_rows = sum(self.rows.values())
        channel_rows = sum(c.rows for c in self.channels.values())
        if self.rows and table_rows != channel_rows:
            out.append(f"rows written ({table_rows}) != rows parsed ({channel_rows})")
        out += [
            f"{ch}: raw channel with no parser and no declaration" for ch in self.unread_channels
        ]
        return out

    def to_json(self) -> dict:
        d = asdict(self)
        d["ok"] = not self.problems()
        d["problems"] = self.problems()
        return d


def sidecar_path(norm_root: Path, venue: str, date: str, hour: int) -> Path:
    return norm_root / "_accounting" / f"venue={venue}" / f"date={date}" / f"hour={hour:02d}.json"


def write_sidecar(norm_root: Path, venue: str, date: str, hour: int, stats: HourStats) -> None:
    path = sidecar_path(norm_root, venue, date, hour)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stats.to_json(), indent=1, sort_keys=True))
    tmp.replace(path)


def read_problems(norm_root: Path, venue: str, date: str, hour: int) -> list[str]:
    """Problems recorded for an hour by an earlier run ([] if clean or never accounted)."""
    path = sidecar_path(norm_root, venue, date, hour)
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("problems", [])
