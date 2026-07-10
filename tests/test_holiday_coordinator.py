"""Tests for MOM public holiday parsing and coordinator fetch."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.singapore.holiday_coordinator import (
    PublicHoliday,
    PublicHolidayCoordinator,
    _parse_public_holidays,
)

_THIS_YEAR = datetime.now().year

_HTML_HOLIDAYS = f"""
<html><body>
<table>
  <thead><tr><th>Holiday</th><th>Date</th></tr></thead>
  <tbody>
    <tr><td>New Year's Day</td><td>1 January {_THIS_YEAR}, Wednesday</td></tr>
    <tr><td>Good Friday</td><td>18 April {_THIS_YEAR}, Friday</td></tr>
    <tr><td>National Day</td><td>9 August {_THIS_YEAR + 1}, Sunday</td></tr>
    <tr><td>Old Holiday</td><td>1 January {_THIS_YEAR - 1}, Monday</td></tr>
  </tbody>
</table>
</body></html>
"""


def test_parse_public_holidays_filters_past_years():
    holidays = _parse_public_holidays(_HTML_HOLIDAYS)

    assert [h.name for h in holidays] == [
        "New Year's Day",
        "Good Friday",
        "National Day",
    ]
    assert all(h.day.year >= _THIS_YEAR for h in holidays)


def test_parse_public_holidays_raises_when_missing_rows():
    with pytest.raises(Exception, match="Could not find public holiday rows"):
        _parse_public_holidays("<html><body><p>No table</p></body></html>")


@pytest.mark.asyncio
async def test_holiday_coordinator_success():
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *a: func(*a))

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value=_HTML_HOLIDAYS)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = PublicHolidayCoordinator(hass)

    with patch(
        "custom_components.singapore.holiday_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert isinstance(coordinator.data[0], PublicHoliday)


@pytest.mark.asyncio
async def test_holiday_coordinator_http_error():
    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 403
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = PublicHolidayCoordinator(hass)

    with patch(
        "custom_components.singapore.holiday_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_holiday_coordinator_http_error_uses_last_known_data():
    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = PublicHolidayCoordinator(hass)
    coordinator.data = [
        PublicHoliday(name="New Year's Day", day=datetime(2026, 1, 1).date())
    ]

    with patch(
        "custom_components.singapore.holiday_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data[0].name == "New Year's Day"


def test_parse_public_holidays_uses_ha_timezone_not_server_local():
    """current_year must come from homeassistant.util.dt.now(), not datetime.now()."""
    with (
        patch(
            "custom_components.singapore.holiday_coordinator.dt_util.now",
            return_value=datetime(2030, 1, 1),
        ),
        pytest.raises(Exception, match="Could not find public holiday rows"),
    ):
        # With current_year patched to 2030, every holiday in the fixture
        # (built for _THIS_YEAR..+1) is in the past and gets filtered out.
        _parse_public_holidays(_HTML_HOLIDAYS)


@pytest.mark.asyncio
async def test_holiday_coordinator_client_error_uses_last_known_data():
    """Transient network errors (aiohttp.ClientError) fall back to stale data."""
    hass = MagicMock()
    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=aiohttp.ClientConnectionError("boom"))

    coordinator = PublicHolidayCoordinator(hass)
    cached = [PublicHoliday(name="New Year's Day", day=datetime(2026, 1, 1).date())]
    coordinator.data = cached

    with patch(
        "custom_components.singapore.holiday_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data is cached


@pytest.mark.asyncio
async def test_holiday_coordinator_parse_failure_with_cached_data_fails_update():
    """A page with no holiday rows must fail the update, not silently mask it."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *a: func(*a))

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(
        return_value="<html><body><p>No table</p></body></html>"
    )
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = PublicHolidayCoordinator(hass)
    cached = [PublicHoliday(name="New Year's Day", day=datetime(2026, 1, 1).date())]
    coordinator.data = cached

    with patch(
        "custom_components.singapore.holiday_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.data is cached
