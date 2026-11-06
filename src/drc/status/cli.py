"""`drc-status`: is the recorder healthy, is the archive up to date, is the Drive login alive?

    drc-status                    check the VM over ssh (target from config/status.toml)
    drc-status --local            check this machine (laptop): auth age, disk, opt-in Drive probe
    drc-status --json             machine-readable output
    drc-status --ssh user@host --ssh-key ~/.ssh/key    override the ssh target

Strictly read-only. Exit codes: 0 all ok, 1 a warning, 2 an error (stale, failing or
unreachable), 64 bad usage/config. Thresholds live in config/status.toml.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from pathlib import Path

from drc.status.checks import ERROR, RANK, WARN, Check, Target, Thresholds, collect, worst
from drc.status.runner import Runner, local_runner, ssh_runner

EXIT = {"ok": 0, WARN: 1, ERROR: 2}
USAGE = 64
TARGET_KEYS = set(Target.__dataclass_fields__) - {"name"}


def load_target(cfg: dict, name: str) -> tuple[Target, dict]:
    """Build a Target from a [vm]/[local] table. Keys that are not Target fields (ssh, ssh_key,
    recorder_config) are returned separately."""
    sec = dict(cfg.get(name, {}))
    extra = {k: sec.pop(k) for k in ("ssh", "ssh_key", "recorder_config") if k in sec}
    unknown = set(sec) - TARGET_KEYS
    if unknown:
        raise ValueError(f"unknown keys in [{name}]: {sorted(unknown)}")
    sec = {k: (v if v != "" else None) for k, v in sec.items()}
    t = Target(name=name, **sec)
    rc = extra.get("recorder_config")
    if rc and t.raw_cap_gb is None and Path(rc).exists():
        # the recorder's own size valve: past it, it deletes the oldest days (synced or not)
        t.raw_cap_gb = tomllib.loads(Path(rc).read_text()).get("recorder", {}).get("max_total_gb")
    return t, extra


MARK = {"ok": "ok   ", WARN: "WARN ", ERROR: "ERROR", "skip": "skip "}


def render(target: str, where: str, checks: list[Check], now: float | None) -> str:
    overall = worst(c.status for c in checks)
    when = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(now)) if now else "?"
    lines = [f"drc-status {target} ({where}) at {when}: {overall.upper()}"]
    width = max((len(c.name) for c in checks), default=0)
    for c in sorted(checks, key=lambda c: -RANK[c.status]) if overall != "ok" else checks:
        lines.append(f"  {MARK[c.status]} {c.name:<{width}}  {c.summary}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drc-status", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="config/status.toml", type=Path)
    ap.add_argument("--local", action="store_true", help="check this machine instead of the VM")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the summary")
    ap.add_argument("--ssh", help="ssh target (default: [vm].ssh or $DRC_VM_SSH)")
    ap.add_argument("--ssh-key", help="ssh identity file (default: [vm].ssh_key or $DRC_VM_KEY)")
    ap.add_argument(
        "--probe-drive",
        action="store_true",
        help="also call `rclone about` (may refresh and rewrite the rclone token)",
    )
    ap.add_argument(
        "--verify",
        metavar="HOST",
        help="local only: also run `drc-sync verify` as HOST (slow: hashes files)",
    )
    args = ap.parse_args(argv)

    try:
        cfg = tomllib.loads(args.config.read_text())
        th = Thresholds.from_dict(cfg.get("thresholds", {}))
        name = "local" if args.local else "vm"
        t, extra = load_target(cfg, name)
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as e:
        print(f"drc-status: bad config {args.config}: {e}", file=sys.stderr)
        return USAGE
    if args.probe_drive:
        t.probe_drive = True
    if args.verify:
        if not args.local:
            ap.error("--verify is for --local (the VM's verify result comes from its sync log)")
        t.verify_host = args.verify

    if args.local:
        where = "this machine"
        run = runner or local_runner()
    else:
        target = args.ssh or os.environ.get("DRC_VM_SSH") or extra.get("ssh")
        if not target:
            ap.error("no ssh target: set [vm].ssh in the config, $DRC_VM_SSH, or --ssh")
        key = args.ssh_key or os.environ.get("DRC_VM_KEY") or extra.get("ssh_key")
        where = f"ssh {target}"
        run = runner or ssh_runner(target, key)

    checks, now, _ = collect(run, t, th)
    overall = worst(c.status for c in checks)
    if args.json:
        print(
            json.dumps(
                {
                    "target": t.name,
                    "where": where,
                    "checked_at": now,
                    "status": overall,
                    "checks": [c.to_dict() for c in checks],
                },
                indent=2,
            )
        )
    else:
        print(render(t.name, where, checks, now))
    return EXIT.get(overall, 0)


if __name__ == "__main__":
    raise SystemExit(main())
