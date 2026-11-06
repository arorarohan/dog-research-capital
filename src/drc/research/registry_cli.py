"""`drc-registry`: the hypothesis registry (lab notebook) and multiple-testing correction.

    drc-registry list [--status S]         one line per hypothesis
    drc-registry validate                  schema-check every hypotheses/*.toml (exit 1 on errors)
    drc-registry count                     tests run, looks, primaries, by status
    drc-registry table [--alpha 0.05]      multiple-testing-corrected significance table (markdown)
    drc-registry summary [--write | --check]
                                           generated overview; --write updates
                                           hypotheses/SUMMARY.md, --check fails if it is stale

All commands take --dir (default: hypotheses). Linked findings paths are resolved against the
directory's parent (the repo root).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from drc.research import registry

SUMMARY_NAME = "SUMMARY.md"


def _load(directory: Path) -> list[registry.Hypothesis]:
    return registry.load(directory, root=directory.resolve().parent)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drc-registry", description=__doc__.split("\n")[0])
    ap.add_argument("--dir", type=Path, default=Path("hypotheses"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="one line per hypothesis")
    ls.add_argument("--status", choices=registry.STATUSES)
    sub.add_parser("validate", help="schema-check the registry")
    sub.add_parser("count", help="how many tests and looks")
    tb = sub.add_parser("table", help="multiple-testing-corrected significance table")
    tb.add_argument("--alpha", type=float, default=0.05)
    sm = sub.add_parser("summary", help="generated markdown overview")
    g = sm.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help=f"write <dir>/{SUMMARY_NAME}")
    g.add_argument("--check", action="store_true", help=f"fail if <dir>/{SUMMARY_NAME} is stale")
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        errs = registry.validate_dir(args.dir, root=args.dir.resolve().parent)
        for e in errs:
            print(e, file=sys.stderr)
        n = len(list(args.dir.glob("*.toml")))
        print(f"{n} files, {len(errs)} errors" if errs else f"{n} files OK")
        return 1 if errs else 0

    try:
        hyps = _load(args.dir)
    except registry.RegistryError as e:
        print(f"registry invalid (run `drc-registry validate`):\n{e}", file=sys.stderr)
        return 1

    if args.cmd == "list":
        for h in hyps:
            if args.status and h.status != args.status:
                continue
            alias = f" [{', '.join(h.aliases)}]" if h.aliases else ""
            print(
                f"{h.status:<12} {h.id:<34} tests={len(h.tests):<3} looks={h.looks:<4} "
                f"{h.title}{alias}"
            )
    elif args.cmd == "count":
        print(registry.counts_text(registry.counts(hyps)))
    elif args.cmd == "table":
        rows, c = registry.corrected_table(hyps)
        print(registry.counts_text(c) + "\n")
        print(registry.table_markdown(rows, c, args.alpha))
    elif args.cmd == "summary":
        text = registry.summary_markdown(hyps)
        target = args.dir / SUMMARY_NAME
        if args.write:
            target.write_text(text)
            print(f"wrote {target}")
        elif args.check:
            if not target.exists() or target.read_text() != text:
                print(f"{target} is stale: run `drc-registry summary --write`", file=sys.stderr)
                return 1
            print(f"{target} is current")
        else:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
