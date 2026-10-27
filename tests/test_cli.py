import tomllib
from pathlib import Path

from drc.recorder.cli import build_sources


def test_default_config_builds_all_sources():
    cfg = tomllib.loads(Path("config/recorder.toml").read_text())
    assert [s.venue for s in build_sources(cfg)] == ["coinbase", "kraken", "polymarket", "kalshi"]
    assert [s.venue for s in build_sources(cfg, {"kraken"})] == ["kraken"]


def test_forward_config_is_what_the_vm_service_runs():
    """The systemd unit points at the forward config; it must build only the forward sources
    and carry retention for the two channels that would otherwise grow without bound."""
    unit = Path("deploy/systemd/drc-record.service").read_text()
    assert "--config config/recorder-forward.toml" in unit and "KillSignal=SIGINT" in unit
    cfg = tomllib.loads(Path("config/recorder-forward.toml").read_text())
    assert [s.venue for s in build_sources(cfg)] == ["polymarket", "kalshi"]  # no crypto books
    rules = {(r["venue"], r["channel"]): r["max_age_days"] for r in cfg["retention"]}
    assert rules == {("polymarket", "price_change"): 30, ("kalshi", "trades"): 30}
    assert cfg["recorder"]["max_total_gb"] <= 30
