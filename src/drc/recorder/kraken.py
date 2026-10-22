"""Kraken v2 public websocket: L2 book (with checksums) plus trades."""

from __future__ import annotations

import orjson

from drc.recorder.base import WsSource


class KrakenSource(WsSource):
    venue = "kraken"
    url = "wss://ws.kraken.com/v2"

    def __init__(self, symbols: list[str], book_depth: int = 100) -> None:
        self.symbols = symbols
        self.book_depth = book_depth

    async def subscriptions(self) -> list[dict]:
        return [
            {
                "method": "subscribe",
                "params": {"channel": "book", "symbol": self.symbols, "depth": self.book_depth},
            },
            {"method": "subscribe", "params": {"channel": "trade", "symbol": self.symbols}},
        ]

    def channel_of(self, raw: str | bytes) -> str | None:
        try:
            msg = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None
        if not isinstance(msg, dict) or msg.get("channel") == "heartbeat":
            return None
        if "method" in msg:  # subscribe acks
            return "subscriptions"
        return msg.get("channel")
