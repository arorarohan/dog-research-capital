"""Websocket source runner: connect, subscribe, store every message, reconnect on failure.

Gap semantics: every `connect` event in a venue's `_meta` channel starts a new epoch. Consumers
must treat it as a hard resync boundary (rebuild books from the next snapshot) and must never
interpolate across the span between a `disconnect` and the following `connect`.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import orjson
import websockets

from drc.recorder.writer import RawWriter

log = logging.getLogger(__name__)

MAX_BACKOFF = 60.0


class WsSource(ABC):
    venue: str
    url: str
    stale_timeout: float = 120.0  # reconnect if nothing arrives for this long
    keepalive_interval: float | None = None

    writer: RawWriter

    @abstractmethod
    async def subscriptions(self) -> list[str | dict]:
        """Messages to send right after connecting."""

    @abstractmethod
    def channel_of(self, raw: str | bytes) -> str | None:
        """Channel name used to file this message, or None to drop it."""

    def keepalive_message(self) -> str | None:
        return None

    async def on_tick(self, ws) -> None:  # noqa: B027 - optional hook
        """Called every `tick_interval` seconds on a live connection."""

    tick_interval: float | None = None

    async def run(self, writer: RawWriter) -> None:
        self.writer = writer
        backoff = 1.0
        while True:
            try:
                subs = await self.subscriptions()
                async with websockets.connect(
                    self.url, max_size=None, open_timeout=15, ping_interval=20, ping_timeout=20
                ) as ws:
                    for sub in subs:
                        await ws.send(sub if isinstance(sub, str) else orjson.dumps(sub).decode())
                    writer.event(self.venue, "connect", url=self.url)
                    log.info("%s connected", self.venue)
                    backoff = 1.0
                    await self._pump(ws, writer)
            except asyncio.CancelledError:
                writer.event(self.venue, "shutdown")
                raise
            except Exception as e:  # noqa: BLE001 - any failure means reconnect
                writer.event(self.venue, "disconnect", reason=repr(e))
                log.warning("%s disconnected: %r; retry in %.0fs", self.venue, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    async def _pump(self, ws, writer: RawWriter) -> None:
        helpers = []
        if self.keepalive_interval and (msg := self.keepalive_message()):
            helpers.append(asyncio.create_task(self._every(self.keepalive_interval, ws.send, msg)))
        if self.tick_interval:
            helpers.append(asyncio.create_task(self._every(self.tick_interval, self.on_tick, ws)))
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), self.stale_timeout)
                channel = self.channel_of(raw)
                if channel is not None:
                    writer.write(self.venue, channel, raw)
                for t in helpers:  # surface helper failures instead of hanging silently
                    if t.done() and (exc := t.exception()):
                        raise exc
        finally:
            for t in helpers:
                t.cancel()

    @staticmethod
    async def _every(interval: float, fn, *args) -> None:
        while True:
            await asyncio.sleep(interval)
            await fn(*args)


def json_channel(raw: str | bytes, key: str = "channel") -> str | None:
    """Channel of a JSON object message, or None if it is not one."""
    try:
        msg = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    return msg.get(key) if isinstance(msg, dict) else None
