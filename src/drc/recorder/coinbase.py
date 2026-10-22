"""Coinbase Advanced Trade public websocket: full L2 book plus market trades."""

from __future__ import annotations

from drc.recorder.base import WsSource, json_channel


class CoinbaseSource(WsSource):
    venue = "coinbase"
    url = "wss://advanced-trade-ws.coinbase.com"

    def __init__(self, products: list[str], channels: list[str] | None = None) -> None:
        self.products = products
        self.channels = channels or ["level2", "market_trades"]

    async def subscriptions(self) -> list[dict]:
        return [
            {"type": "subscribe", "product_ids": self.products, "channel": ch}
            for ch in self.channels
        ]

    def channel_of(self, raw: str | bytes) -> str | None:
        return json_channel(raw)
