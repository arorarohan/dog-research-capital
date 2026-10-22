import tomllib
from pathlib import Path

from drc.recorder.cli import build_sources


def test_default_config_builds_all_sources():
    cfg = tomllib.loads(Path("config/recorder.toml").read_text())
    assert [s.venue for s in build_sources(cfg)] == ["coinbase", "kraken", "polymarket", "kalshi"]
    assert [s.venue for s in build_sources(cfg, {"kraken"})] == ["kraken"]
