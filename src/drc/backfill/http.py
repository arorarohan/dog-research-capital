"""Polite HTTP: minimum spacing between requests, retry with backoff on 429/5xx."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class Client:
    def __init__(self, base: str = "", min_interval: float = 0.25, retries: int = 8) -> None:
        self.base, self.min_interval, self.retries = base, min_interval, retries
        self._http = httpx.Client(timeout=30, follow_redirects=True)
        self._last = 0.0

    def _wait(self) -> None:
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def get(self, path: str, **params) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.base}{path}"
        for attempt in range(self.retries):
            self._wait()
            try:
                r = self._http.get(url, params=params or None)
            except httpx.TransportError as e:
                log.warning("transport error %r (attempt %d)", e, attempt + 1)
                time.sleep(min(2**attempt, 30))
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2**attempt, 30))
                continue
            r.raise_for_status()
        raise RuntimeError(f"gave up on {url} after {self.retries} attempts")

    def json(self, path: str, **params):
        return self.get(path, **params).json()

    def exists(self, url: str) -> bool:
        self._wait()
        return self._http.head(url).status_code == 200
