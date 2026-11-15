"""The shared data.gov.sg limiter: pacing, 429 back-off, API key scoping."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.singapore.datagov import (
    DEFAULT_RETRY_SECONDS,
    MAX_RETRIES,
    HostLimiter,
    data_gov_get,
    is_data_gov,
    limiter_for,
    retry_seconds,
)

V2 = "https://api-open.data.gov.sg/v2/real-time/api/psi"
V1 = "https://api.data.gov.sg/v1/environment/rainfall"


def _response(status=200, headers=None, text=""):
    response = MagicMock()
    response.status = status
    response.headers = headers or {}
    response.text = AsyncMock(return_value=text)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _hass():
    hass = MagicMock()
    hass.data = {}
    return hass


# -- retry_seconds ------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_seconds_reads_the_body_message():
    """data.gov.sg's 429 has no Retry-After; the wait is in the body."""
    body = (
        '{"code":24,"name":"TOO_MANY_REQUESTS","data":null,"errorMsg":"Rate '
        'limit exceeded. Please try again in 10 seconds and sign up for an API key"}'
    )
    assert await retry_seconds(_response(429, text=body.replace("10", "7"))) == 7.0
    assert await retry_seconds(_response(429, {"Retry-After": "3"})) == 3.0
    assert await retry_seconds(_response(429, {"Retry-After": "9999"})) == 60.0
    assert (
        await retry_seconds(_response(429, {"Retry-After": "Wed, 21 Oct 2026"}))
        == DEFAULT_RETRY_SECONDS
    )
    assert await retry_seconds(_response(429)) == DEFAULT_RETRY_SECONDS


# -- pacing -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_requests_burst_then_pace(datagov_time):
    limiter = limiter_for(_hass(), V2)
    starts = []
    for _ in range(7):
        await limiter.async_wait_turn()
        starts.append(datagov_time["now"] - 1000.0)
    assert starts == [0, 0, 3, 6, 9, 12, 15]


@pytest.mark.asyncio
async def test_other_hosts_are_not_paced(datagov_time):
    limiter = limiter_for(_hass(), V1)
    for _ in range(20):
        await limiter.async_wait_turn()
    assert datagov_time["sleeps"] == []


def test_limiter_is_shared_per_host_and_per_hass():
    hass = _hass()
    assert limiter_for(hass, V2) is limiter_for(hass, V2 + "?x=1")
    assert limiter_for(hass, V2) is not limiter_for(hass, V1)
    assert limiter_for(_hass(), V2) is not limiter_for(hass, V2)


@pytest.mark.asyncio
async def test_startup_burst_stays_under_the_measured_limit(datagov_time):
    """Replay startup + 30 min of polling against a model of data.gov.sg.

    Measured 2026-11-15: about 6 requests per ~10 s per client, on every
    endpoint together (a 7th request 10.3 s after a burst was refused).
    Modelled strictly: a request is refused if 6 others started within the
    previous 10.5 s. Every request must get through (no 429).
    """
    limiter = limiter_for(_hass(), V2)
    starts: list[float] = []
    rejected = 0

    async def request():
        nonlocal rejected
        await limiter.async_wait_turn()
        now = datagov_time["now"]
        if sum(1 for t in starts if now - t < 10.5) >= 6:
            rejected += 1
        starts.append(now)

    # Startup: weather (3), PSI, UV, flood, lightning, radar (3), dengue = 11.
    for _ in range(11):
        await request()
    # Then every 2 minutes flood + lightning, every 5 minutes 3 radar ranges,
    # every 10 minutes 3 weather forecasts, all landing on the same tick.
    for minute in range(1, 31):
        burst = 2 + (3 if minute % 5 == 0 else 0) + (3 if minute % 10 == 0 else 0)
        datagov_time["now"] += 60
        for _ in range(burst if minute % 2 == 0 else 3 if minute % 5 == 0 else 0):
            await request()
    assert rejected == 0


# -- 429 handling -------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_pauses_the_host_retries_and_succeeds(datagov_time):
    body = "Rate limit exceeded. Please try again in 10 seconds"
    session = MagicMock()
    session.get = MagicMock(side_effect=[_response(429, text=body), _response(200)])
    async with data_gov_get(_hass(), session, V2, timeout=5) as response:
        assert response.status == 200
    assert session.get.call_count == 2
    assert datagov_time["sleeps"] == [10.0]


@pytest.mark.asyncio
async def test_a_pause_holds_every_coordinator(datagov_time):
    hass = _hass()
    limiter = limiter_for(hass, V2)
    limiter.pause(10)
    # Another coordinator's request waits out the block instead of extending it.
    await limiter_for(hass, V2).async_wait_turn()
    assert datagov_time["sleeps"] == [10.0]


@pytest.mark.asyncio
async def test_429_after_retries_is_returned(datagov_time):
    session = MagicMock()
    session.get = MagicMock(return_value=_response(429))
    async with data_gov_get(_hass(), session, V2) as response:
        assert response.status == 429
    assert session.get.call_count == MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_api_key_is_only_sent_to_data_gov():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(200))
    headers = {"x-api-key": "k", "Accept": "application/json"}
    async with data_gov_get(
        _hass(), session, "https://s3.amazonaws.com/f", headers=headers
    ):
        pass
    assert session.get.call_args.kwargs["headers"] == {"Accept": "application/json"}
    async with data_gov_get(_hass(), session, V2, headers=headers):
        pass
    assert session.get.call_args.kwargs["headers"] == headers


def test_is_data_gov():
    assert is_data_gov(V1) and is_data_gov(V2)
    assert is_data_gov("https://data.gov.sg/api/action/datastore_search")
    assert not is_data_gov("https://notdata.gov.sg/x")
    assert not is_data_gov("https://s3.ap-southeast-1.amazonaws.com/x")


@pytest.mark.asyncio
async def test_concurrent_waiters_are_spaced(datagov_time):
    """Reservations are booked without awaiting, so gather() can't race."""
    limiter = HostLimiter(burst=2, interval=2.0)
    await asyncio.gather(*(limiter.async_wait_turn() for _ in range(4)))
    # Two go at once, then one every 2 s: the last starts 4 s in.
    assert datagov_time["now"] == 1004.0
    assert len(datagov_time["sleeps"]) == 2
