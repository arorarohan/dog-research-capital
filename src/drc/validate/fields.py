"""Field accounting check: every field and channel in raw must be declared in `fieldmap`.

    uv run python -m drc.validate.fields data/raw [data/raw_forward ...]

Exit code 1 if raw contains an undeclared field, an undeclared channel, or a value that a
declared "always" note says never happens (a Kalshi block trade). Prints a table of what we
keep and drop per (venue, channel) with message counts.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from drc.normalize.fieldmap import DROPPED, FIELD_MAP, IGNORED_CHANNELS, META_CHANNEL_NAME
from drc.recorder.writer import read_lines

MAX_MSGS_PER_FILE = 200_000  # fields do not vary with volume; bound the scan time


def leaf_paths(x, prefix: str, out: Counter) -> None:
    """Count the leaf paths of a JSON value. An empty array counts as its own `path[]` leaf."""
    if isinstance(x, dict):
        for k, v in x.items():
            leaf_paths(v, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(x, list):
        if not x:
            out[prefix + "[]"] += 1
        for v in x:
            leaf_paths(v, prefix + "[]", out)
    else:
        out[prefix] += 1


def scan(raw_roots: list[Path], max_msgs: int = MAX_MSGS_PER_FILE):
    """-> ({(venue, channel): Counter(path)}, {(venue, channel): message count}, odd values)."""
    paths: dict[tuple[str, str], Counter] = {}
    msgs: Counter = Counter()
    odd: Counter = Counter()
    for root in raw_roots:
        for chan_dir in sorted(root.glob("*/*")):
            if not chan_dir.is_dir() or chan_dir.name == META_CHANNEL_NAME:
                continue
            key = (chan_dir.parent.name, chan_dir.name)
            counter = paths.setdefault(key, Counter())
            for f in sorted(chan_dir.glob("*/*.jsonl*")):
                for i, rec in enumerate(read_lines(f)):
                    if i >= max_msgs:
                        break
                    msgs[key] += 1
                    m = rec.get("m", rec)
                    leaf_paths(m, "", counter)
                    if key == ("kalshi", "trades") and m.get("is_block_trade"):
                        odd["kalshi block trade"] += 1
    return paths, msgs, odd


def _declared(declared: dict, path: str) -> bool:
    if path in declared:
        return True
    # an empty array of a declared container ("events[]") has no children to match
    return path.endswith("[]") and any(d.startswith(path) for d in declared)


def check(raw_roots: list[Path]) -> tuple[list[str], list[str]]:
    """-> (problems, report lines)."""
    paths, msgs, odd = scan(raw_roots)
    problems: list[str] = []
    report: list[str] = []
    for key in sorted(paths):
        venue, channel = key
        n = msgs[key]
        if key in IGNORED_CHANNELS:
            report.append(f"{venue}/{channel}: {n} msgs, IGNORED CHANNEL: {IGNORED_CHANNELS[key]}")
            continue
        declared = FIELD_MAP.get(key)
        if declared is None:
            problems.append(
                f"{venue}/{channel}: {n} msgs in a channel no parser reads and no one declared"
            )
            continue
        seen = paths[key]
        dropped = [p for p in seen if declared.get(p, ("",))[0] == DROPPED]
        report.append(
            f"{venue}/{channel}: {n} msgs, {len(seen)} field paths, "
            f"{len(dropped)} dropped ({', '.join(sorted(dropped)) or '-'})"
        )
        problems += [
            f"{venue}/{channel}: undeclared field {p!r} ({c} times)"
            for p, c in sorted(seen.items())
            if not _declared(declared, p)
        ]
        problems += [
            f"{venue}/{channel}: declared field {p!r} never seen (stale declaration?)"
            for p in sorted(declared)
            if p not in seen and not any(s.startswith(p) for s in seen)
        ]
    problems += [f"{what}: {c} messages" for what, c in odd.items()]
    return problems, report


def main() -> None:
    roots = [Path(a) for a in sys.argv[1:]] or [Path("data/raw")]
    problems, report = check(roots)
    print("\n".join(report))
    if problems:
        print("\nPROBLEMS:\n" + "\n".join(problems))
        sys.exit(1)
    print("\nall raw fields and channels are accounted for")


if __name__ == "__main__":
    main()
