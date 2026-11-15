"""Shared, rate-limited access to data.gov.sg for every coordinator.

Measured 2026-11-15 without an API key: ``api-open.data.gov.sg`` (the v2
real-time APIs) allows about 6 requests per ~10 s per client, across *all*
its endpoints, then answers HTTP 429 to everything for ~10 s. The 429 has no
``Retry-After`` header; the body says ``"Rate limit exceeded. Please try
again in 10 seconds ..."``. (A 7th request 10.3 s after a burst still got a
429.) The v1 host (``api.data.gov.sg``) took 40 back-to-back requests
without one.

The integration's own sources used to trip that limit: at startup about ten
v2 requests went out at once, and the 2-minute pollers (flood, lightning) and
the radar keep landing on the same tick. So every data.gov.sg request goes
through one limiter per host, shared by all coordinators of a Home Assistant
instance:

- v2 requests are paced: a burst of ``_BURST`` and then one every
  ``_INTERVAL`` seconds, at most 5 in any 10 s (GCRA / virtual scheduling,
  so a waiter sleeps once and never polls the clock).
- A 429 (any data.gov.sg host) pauses the *whole host* for the time the
  response asks for (``Retry-After``, else the body's "try again in N
  seconds", else 10 s, capped at 60 s), so the other coordinators wait instead
  of extending the block, and the request is retried up to ``MAX_RETRIES``
  times. The last 429 is returned to the caller, which treats it as a
  transient failure (last-known data, per the coordinator conventions).

An API key (options flow) raises the limits further, but isn't needed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import HomeAssistant

from .const import DATA_GOV_API_KEY_HEADER

_LOGGER = logging.getLogger(__name__)

PACED_HOST = "api-open.data.gov.sg"
_BURST = 2
_INTERVAL = 3.0  # seconds between paced requests after the burst

MAX_RETRIES = 2
DEFAULT_RETRY_SECONDS = 10.0
MAX_RETRY_SECONDS = 60.0
_RETRY_IN = re.compile(r"try again in (\d+(?:\.\d+)?) seconds?", re.IGNORECASE)

_DATA_KEY = "singapore_datagov_limiters"

# Indirection so tests can skip real waiting.
_sleep: Callable[[float], Any] = asyncio.sleep
_clock: Callable[[], float] = time.monotonic


class HostLimiter:
    """Paces requests to one host and pauses it after a 429."""

    def __init__(self, *, burst: int | None, interval: float) -> None:
        # burst None: no pacing, only the shared pause after a 429.
        self._burst = burst
        self._interval = interval
        self._tat = 0.0  # theoretical arrival time of the next request
        self._paused_until = 0.0

    def _reserve(self) -> float:
        """Book the next slot; return how long to wait for it (no awaits)."""
        now = _clock()
        start = max(now, self._paused_until)
        if self._burst is None:
            return start - now
        tat = max(self._tat, start)
        allowed_at = max(start, tat - (self._burst - 1) * self._interval)
        self._tat = tat + self._interval
        return allowed_at - now

    async def async_wait_turn(self) -> None:
        """Wait until this request may go out."""
        if (wait := self._reserve()) > 0:
            await _sleep(wait)

    def pause(self, seconds: float) -> None:
        """Hold every request to this host for ``seconds`` (after a 429)."""
        now = _clock()
        self._paused_until = max(self._paused_until, now + seconds)
        # Resume paced, not with a fresh burst that could re-trigger the block.
        if self._burst is not None:
            self._tat = max(
                self._tat, self._paused_until + (self._burst - 1) * self._interval
            )


def is_data_gov(url: str) -> bool:
    """Whether ``url`` is on data.gov.sg (the only host that gets the API key)."""
    host = (urlsplit(url).hostname or "").lower()
    return host == "data.gov.sg" or host.endswith(".data.gov.sg")


def limiter_for(hass: HomeAssistant, url: str) -> HostLimiter:
    """The shared limiter for ``url``'s host (one per Home Assistant)."""
    host = urlsplit(url).hostname or ""
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):  # a bare test double: nothing to share
        return _new_limiter(host)
    limiters: dict[str, HostLimiter] = data.setdefault(_DATA_KEY, {})
    if host not in limiters:
        limiters[host] = _new_limiter(host)
    return limiters[host]


def _new_limiter(host: str) -> HostLimiter:
    if host == PACED_HOST:
        return HostLimiter(burst=_BURST, interval=_INTERVAL)
    return HostLimiter(burst=None, interval=0.0)


async def retry_seconds(response: aiohttp.ClientResponse) -> float:
    """How long a 429 asks us to wait: header, then body text, then default."""
    seconds: float | None = None
    try:
        header = response.headers.get("Retry-After")
    except AttributeError:
        header = None
    if isinstance(header, (str, int, float)):
        try:
            seconds = float(header)
        except ValueError:  # an HTTP date: not worth parsing, use the default
            seconds = None
    if seconds is None:
        text = ""
        if callable(getattr(response, "text", None)):
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - unreadable body: use the default
                text = ""
        if isinstance(text, str) and (match := _RETRY_IN.search(text)):
            seconds = float(match.group(1))
    if seconds is None or seconds < 0:
        seconds = DEFAULT_RETRY_SECONDS
    return min(seconds, MAX_RETRY_SECONDS)


@asynccontextmanager
async def data_gov_get(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    url: str,
    **kwargs: Any,
) -> AsyncIterator[aiohttp.ClientResponse]:
    """``async with session.get(url, **kwargs)``, rate-limited and 429-retried.

    Yields the response like ``session.get`` does; after ``MAX_RETRIES``
    retries a 429 is yielded as-is for the caller's usual status handling.
    """
    limiter = limiter_for(hass, url)
    if not is_data_gov(url) and kwargs.get("headers"):
        # The API key is for data.gov.sg only (not, e.g., presigned S3 links).
        kwargs["headers"] = {
            k: v
            for k, v in kwargs["headers"].items()
            if k.lower() != DATA_GOV_API_KEY_HEADER
        }
    for attempt in range(MAX_RETRIES + 1):
        await limiter.async_wait_turn()
        async with session.get(url, **kwargs) as response:
            if response.status == 429 and attempt < MAX_RETRIES:
                wait = await retry_seconds(response)
                limiter.pause(wait)
                _LOGGER.debug(
                    "HTTP 429 from %s; pausing data.gov.sg requests %.0fs (retry %d/%d)",
                    urlsplit(url).hostname,
                    wait,
                    attempt + 1,
                    MAX_RETRIES,
                )
                continue
            yield response
            return
