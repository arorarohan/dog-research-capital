"""Fund charters: the pre-registered rules a fund runs under (design doc section 2).

A charter is a TOML file, `funds/<fund_id>.toml`, committed to git before the first session.
Its SHA-256 (of the exact file bytes) goes into the `fund_created` event, so the rules a fund
ran under are provable, and a fund refuses to run under a changed file: changed rules mean a
new fund (or version), never an edited one.

`status = "draft"` charters can only be dry-run. A real session needs `status = "frozen"`, set
by a person after review, and a committed, unmodified file.
"""

from __future__ import annotations

import hashlib
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from drc.forward.broker import BrokerConfig
from drc.forward.risk import RiskLimits


class CharterError(Exception):
    pass


# Charter fields that state a rule the code implements in exactly one way, so the code never
# reads them. They must be present and say what the code does: a charter claiming another rule
# (say `exit.rule = "stop_loss"`) would otherwise run under rules it does not describe.
FIXED_RULES: dict[tuple[str, str], object] = {
    ("universe", "venue"): "polymarket",
    ("universe", "binary_yes_no_only"): True,
    ("entry", "side"): "buy_yes",
    ("entry", "limit"): "decision_best_ask",
    ("entry", "once_per_token"): True,
    ("entry", "log_out_of_band"): True,
    ("exit", "rule"): "hold_to_resolution",
    ("schedule", "cadence"): "daily",
}

Z_95_TWO_SIDED = 1.959964
Z_80_POWER = 0.841621


def min_detectable_edge(n: int, sd: float, z_alpha: float = Z_95_TWO_SIDED) -> float:
    """Smallest true mean per-trade edge a sample of `n` independent trades detects with 80%
    power at the given two-sided significance (normal approximation): (z_a + z_b) x sd / sqrt(n).
    Below it, "inconclusive" is the honest verdict."""
    return (z_alpha + Z_80_POWER) * sd / math.sqrt(n)


@dataclass(frozen=True)
class Charter:
    path: Path
    sha256: str
    raw: dict

    @classmethod
    def load(cls, path: str | Path) -> Charter:
        path = Path(path)
        data = path.read_bytes()
        return cls(path, hashlib.sha256(data).hexdigest(), tomllib.loads(data.decode()))

    def __getitem__(self, key: str) -> dict:
        return self.raw[key]

    @property
    def fund_id(self) -> str:
        return self.raw["fund_id"]

    @property
    def name(self) -> str:
        return self.raw.get("name", self.fund_id)

    @property
    def status(self) -> str:
        return self.raw.get("status", "draft")

    @property
    def frozen(self) -> bool:
        return self.status == "frozen"

    def problems(self) -> list[str]:
        """Fixed-rule fields that are missing or name a rule the code does not implement."""
        out = []
        for (table, key), want in FIXED_RULES.items():
            have = self.raw.get(table, {}).get(key, "<missing>")
            if have != want or type(have) is not type(want):
                out.append(f"{table}.{key} = {have!r} (only {want!r} is implemented)")
        return out

    def validate(self) -> None:
        if p := self.problems():
            raise CharterError(f"{self.path}: unsupported charter rules: " + "; ".join(p))

    def risk_limits(self) -> RiskLimits:
        r = self.raw["risk"]
        return RiskLimits(
            max_order_notional=float(r["max_order_notional"]),
            max_event_exposure=float(r["max_event_exposure"]),
            max_fund_exposure=float(r["max_fund_exposure"]),
            max_drawdown=float(r["max_drawdown"]),
        )

    def broker_config(self) -> BrokerConfig:
        ex, sz = self.raw["execution"], self.raw["sizing"]
        return BrokerConfig(
            latency_ns=int(float(ex["latency_secs"]) * 1e9),
            max_book_age_ns=int(float(ex["max_book_age_secs"]) * 1e9),
            depth_share=float(sz["depth_share"]),
        )

    def computed_mde(self) -> float:
        ev = self.raw["evaluation"]
        return min_detectable_edge(int(ev["planned_fills"]), float(ev["per_contract_sd"]))
