"""Tests for MOM public holiday parsing and coordinator fetch."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

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
