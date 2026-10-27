"""Claude Code PreToolUse hook: keep agents off `main`.

All work goes through a branch and a PR (see docs/workflow.md). This blocks Bash commands that
would create commits on a local `main` or push to the remote `main`, and plain force-pushes.
Exit code 2 blocks the tool call; stderr is shown to the agent.

Best effort, not security: it parses common command shapes (`cd x && git ...`, `git -C x ...`).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED = "main"
# subcommands that create commits on (or rewrite) the current branch
COMMITTING = {"commit", "merge", "cherry-pick", "revert", "rebase", "am"}
SEPARATORS = re.compile(r"&&|\|\||;|\||\n")
# git global options that take a value
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def current_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def targets_protected(refspec: str) -> bool:
    dst = refspec.lstrip("+").split(":")[-1]
    return dst in (PROTECTED, f"refs/heads/{PROTECTED}")


def check_git(args: list[str], cwd: str) -> str | None:
    """Return a block reason for one git invocation (args after `git`), or None."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            cwd = os.path.join(cwd, args[i + 1])
        i += 2 if args[i] in GIT_VALUE_OPTS else 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1 :]

    if sub in COMMITTING:
        if current_branch(cwd) == PROTECTED:
            return (
                f"`git {sub}` on `{PROTECTED}` is blocked. Work happens on a branch: "
                f"`git switch -c <type>/<issue#>-<slug>` first, then open a PR. "
                f"See docs/workflow.md."
            )
        return None

    if sub == "push":
        opts = [a for a in rest if a.startswith("-")]
        if "--force" in opts or "-f" in opts:
            return (
                "Plain force-push is blocked. Use `git push --force-with-lease` on your own branch."
            )
        if "--all" in opts or "--mirror" in opts:
            return f"`git push {' '.join(opts)}` could push `{PROTECTED}`; push one branch instead."
        positional = [a for a in rest if not a.startswith("-")]
        refspecs = positional[1:]  # first positional is the remote
        if any(targets_protected(r) for r in refspecs):
            return f"Pushing to `{PROTECTED}` is blocked. Push your branch and open a PR."
        if not refspecs and current_branch(cwd) == PROTECTED:
            return f"You are on `{PROTECTED}`; pushing it is blocked. Push your branch instead."
    return None


def check_command(command: str, cwd: str) -> str | None:
    for segment in SEPARATORS.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens = tokens[1:]  # env assignments like FOO=1 git ...
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) > 1:
            cwd = os.path.join(cwd, os.path.expanduser(tokens[1]))
        elif tokens[0] == "git":
            reason = check_git(tokens[1:], cwd)
            if reason:
                return reason
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    reason = check_command(command, payload.get("cwd") or os.getcwd())
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
