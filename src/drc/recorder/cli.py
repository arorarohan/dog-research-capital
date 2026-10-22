"""`drc-record`: run all enabled sources plus maintenance until interrupted."""

from __future__ import annotations

import argparse
import asyncio
import logging
import tomllib
from pathlib import Path

from drc.recorder.coinbase import CoinbaseSource
from drc.recorder.kalshi import KalshiPoller
from drc.recorder.kraken import KrakenSource
from drc.recorder.polymarket import PolymarketSource
from drc.recorder.retention import Rule, apply_retention
from drc.recorder.writer import RawWriter

log = logging.getLogger("drc.recorder")


def build_sources(cfg: dict, only: set[str] | None = None) -> list:
    def on(name: str) -> bool:
        return cfg.get(name, {}).get("enabled", False) and (only is None or name in only)

    sources = []
    if on("coinbase"):
        sources.append(CoinbaseSource(cfg["coinbase"]["products"]))
    if on("kraken"):
        k = cfg["kraken"]
        sources.append(KrakenSource(k["symbols"], k.get("book_depth", 100)))
    if on("polymarket"):
        p = cfg["polymarket"]
        sources.append(PolymarketSource(p.get("top_n", 50), p.get("refresh_secs", 3600)))
    if on("kalshi"):
        k = cfg["kalshi"]
        sources.append(
            KalshiPoller(k.get("top_n", 50), k.get("poll_secs", 15), k.get("catalog_secs", 3600))
        )
    return sources


async def maintenance(writer: RawWriter, cfg: dict) -> None:
    rec = cfg.get("recorder", {})
    rules = [Rule(r["venue"], r["channel"], r["max_age_days"]) for r in cfg.get("retention", [])]
    flush_secs = rec.get("flush_secs", 5)
    every = rec.get("maintenance_secs", 600)
    last = 0.0
    loop = asyncio.get_running_loop()
    while True:
        writer.flush()
        if loop.time() - last >= every:
            last = loop.time()
            n = await asyncio.to_thread(writer.compress_closed)
            removed = await asyncio.to_thread(
                apply_retention, writer.root, rules, rec.get("max_total_gb")
            )
            if n or removed:
                log.info("maintenance: compressed %d files, pruned %d day dirs", n, len(removed))
        await asyncio.sleep(flush_secs)


async def run(cfg: dict, only: set[str] | None) -> None:
    writer = RawWriter(Path(cfg.get("recorder", {}).get("data_dir", "data/raw")))
    sources = build_sources(cfg, only)
    if not sources:
        raise SystemExit("no sources enabled")
    log.info("recording: %s", ", ".join(getattr(s, "venue", "?") for s in sources))
    tasks = [asyncio.create_task(s.run(writer), name=s.venue) for s in sources]
    tasks.append(asyncio.create_task(maintenance(writer, cfg), name="maintenance"))
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        log.info("recorder stopped; files flushed")


def main() -> None:
    ap = argparse.ArgumentParser(prog="drc-record")
    ap.add_argument("--config", default="config/recorder.toml", type=Path)
    ap.add_argument("--only", help="comma-separated venues to run, e.g. kraken,polymarket")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = tomllib.loads(args.config.read_text())
    only = set(args.only.split(",")) if args.only else None
    try:
        asyncio.run(run(cfg, only))
    except KeyboardInterrupt:
        pass
