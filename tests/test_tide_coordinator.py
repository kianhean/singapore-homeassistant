"""Tests for the NEA tide timings coordinator and sensors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.sensor import (
    SingaporeNextTideLevelSensor,
    SingaporeNextTideSensor,
)
from custom_components.singapore.tide_coordinator import (
    TideCoordinator,
    _parse_tide_html,
)

_PATCH_SESSION = "custom_components.singapore.tide_coordinator.async_get_clientsession"
_SGT = timezone(timedelta(hours=8))

_SAMPLE_HTML = """
<html><body>
<div class="tab__nav forecast-widget__tab">
  <button type="button" id="NovBtn" data-box="Nov" class="tab__nav-item">Nov 2026</button>
  <button type="button" id="DecBtn" data-box="Dec" class="tab__nav-item">Dec 2026</button>
</div>
<div id="Nov" data-box="Nov" class="forecast-widget__content tab__item">
  <table class="table surveillance-table two-row-head">
    <tbody>
      <tr><th rowspan="2">Nov</th><th colspan="3">Tide</th></tr>
      <tr><th>Time</th><th>Height</th><th>H/L</th></tr>
      <tr><td rowspan="4">21</td><td>0418</td><td>2.6</td><td>H</td></tr>
      <tr><td>0905</td><td>1.1</td><td>L</td></tr>
      <tr><td>1439</td><td>2.9</td><td>H</td></tr>
      <tr><td>2158</td><td>0.5</td><td>L</td></tr>
    </tbody>
  </table>
</div>
<div id="Dec" data-box="Dec" class="forecast-widget__content tab__item">
  <table class="table surveillance-table two-row-head">
    <tbody>
      <tr><td rowspan="2">1</td><td>0500</td><td>2.7</td><td>H</td></tr>
      <tr><td>1003</td><td>1.8</td><td>L</td></tr>
    </tbody>
  </table>
</div>
</body></html>
"""


def test_parse_tide_html():
    data = _parse_tide_html(_SAMPLE_HTML)
    assert len(data.tides) == 6
    assert data.tides[0].time == datetime(2026, 11, 21, 4, 18, tzinfo=_SGT)
    assert data.tides[0].height == 2.6
    assert data.tides[0].state == "high"
    assert data.tides[1].state == "low"
    assert data.tides[1].height == 1.1

    now = datetime(2026, 11, 21, 10, 0, tzinfo=_SGT)
    nxt = data.next_tide(now)
    assert nxt is not None
    assert nxt.time == datetime(2026, 11, 21, 14, 39, tzinfo=_SGT)
    assert nxt.state == "high"
    assert nxt.height == 2.9

    next_low = data.next_of_state(now, "low")
    assert next_low is not None
    assert next_low.time == datetime(2026, 11, 21, 21, 58, tzinfo=_SGT)
    assert next_low.height == 0.5


def test_parse_empty_tide_html_raises():
    with pytest.raises(UpdateFailed):
        _parse_tide_html("<html><body><p>No tables</p></body></html>")


def _response(status: int = 200, text: str = _SAMPLE_HTML):
    response = AsyncMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _hass():
    hass = MagicMock()

    async def _exec(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = _exec
    return hass


@pytest.mark.asyncio
async def test_coordinator_fetch_and_fallback():
    session = MagicMock()
    session.get = MagicMock(return_value=_response())
    coordinator = TideCoordinator(_hass())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert len(coordinator.data.tides) == 6
        cached = coordinator.data

        session.get = MagicMock(return_value=_response(status=503))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        session.get = MagicMock(side_effect=aiohttp.ClientError("down"))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        session.get = MagicMock(return_value=_response(text="<html></html>"))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_error_without_cache_fails():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(status=502))
    coordinator = TideCoordinator(_hass())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

    session.get = MagicMock(side_effect=TimeoutError("timeout"))
    coordinator2 = TideCoordinator(_hass())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator2.async_refresh()
        assert coordinator2.last_update_success is False


def test_tide_sensors():
    data = _parse_tide_html(_SAMPLE_HTML)
    coord = MagicMock()
    coord.data = data

    next_sensor = SingaporeNextTideSensor(coord, "e1")
    level_sensor = SingaporeNextTideLevelSensor(coord, "e1")
    assert next_sensor.unique_id == "e1_next_tide"
    assert level_sensor.unique_id == "e1_next_tide_level"

    with patch(
        "custom_components.singapore.sensor.dt_util.now",
        return_value=datetime(2026, 11, 21, 10, 0, tzinfo=_SGT),
    ):
        assert next_sensor.native_value == "high"
        assert level_sensor.native_value == 2.9
        attrs = next_sensor.extra_state_attributes
        assert attrs["height_m"] == 2.9
        assert attrs["next_low_height_m"] == 0.5
        assert len(attrs["upcoming"]) == 4
        assert level_sensor.extra_state_attributes["tide"] == "high"

    coord.data = None
    assert next_sensor.native_value is None
    assert next_sensor.extra_state_attributes == {}
    assert level_sensor.native_value is None
    assert level_sensor.extra_state_attributes == {}
