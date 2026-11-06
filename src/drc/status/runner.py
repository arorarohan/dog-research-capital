"""Where status probes run: locally (bash) or on the VM (ssh). Both return a `Result`.

Every probe is one shell command string. Checks only ever see a `Runner` (any callable taking the
command and a timeout), so tests substitute canned outputs and never touch the network.
A runner never raises: timeouts and a missing ssh binary become a non-zero `Result`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Result:
    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0


Runner = Callable[[str, float], Result]

SSH_UNREACHABLE = 255  # ssh's own exit code for connection/auth failures
TIMEOUT_RC = 124  # same code as coreutils `timeout`


def _run(argv: list[str], timeout: float) -> Result:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(TIMEOUT_RC, "", f"timed out after {timeout:.0f}s")
    except OSError as e:
        return Result(127, "", str(e))
    return Result(p.returncode, p.stdout, p.stderr)


def local_runner(cwd: Path | None = None) -> Runner:
    def run(cmd: str, timeout: float) -> Result:
        prefix = f"cd {cwd} && " if cwd else ""
        return _run(["bash", "-c", prefix + cmd], timeout)

    return run


def ssh_runner(target: str, key: str | None = None, connect_timeout: int = 10) -> Runner:
    """Run each probe as one ssh command. BatchMode: never prompt for a password or passphrase,
    fail instead (exit 255), so an unattended run cannot hang."""
    base = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
    if key:
        base += ["-i", str(Path(key).expanduser())]

    def run(cmd: str, timeout: float) -> Result:
        return _run([*base, target, "--", cmd], timeout + connect_timeout)

    return run
