"""Thin, deliberately narrow wrapper around the rclone binary.

Only verbs that cannot silently rewrite or remove remote data are allowed. `sync`, `move`,
`purge`, `bisync` and `mount` are refused outright. `delete` is allowed because expiry needs it,
but only the expiry code in plan.py/cli.py calls it, always from an explicit file list.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

ALLOWED = frozenset({"copy", "check", "lsjson", "about", "mkdir", "delete"})


class RcloneError(RuntimeError):
    pass


class AuthError(RcloneError):
    """The Drive token is no longer valid. While the Google OAuth app is in "Testing" status,
    refresh tokens expire 7 days after each authorization, so this will recur weekly."""


_AUTH_MARKERS = ("invalid_grant", "expired or revoked", "couldn't fetch token", "oauth2:")


class Rclone:
    def __init__(self, binary: str | None = None, runner: Callable | None = None):
        self.binary = binary or os.environ.get("RCLONE", "rclone")
        self._run = runner or subprocess.run
        self.calls: list[list[str]] = []

    def run(self, verb: str, *args: object, ok_codes: tuple[int, ...] = (0,)):
        if verb not in ALLOWED:
            raise ValueError(f"rclone verb {verb!r} is not allowed here")
        cmd = [self.binary, verb, *(str(a) for a in args)]
        self.calls.append(cmd)
        p = self._run(cmd, capture_output=True, text=True)
        if p.returncode not in ok_codes:
            err = p.stderr or ""
            tail = "\n".join(err.splitlines()[-4:])
            if any(m in err.lower() for m in _AUTH_MARKERS):
                raise AuthError(
                    "Google Drive login expired or was revoked. Re-authorize the rclone remote "
                    "(docs/sync.md, 'Re-authorizing'), then re-run.\n" + tail
                )
            raise RcloneError(f"rclone {verb} failed (exit {p.returncode}):\n{tail}")
        return p
