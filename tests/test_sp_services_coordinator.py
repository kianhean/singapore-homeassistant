"""Tests for SpServicesCoordinator."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.sp_services_coordinator import (
    CONF_SP_TOKEN,
    SpServicesCoordinator,
)


def _make_entry(token: str | None = "tok123") -> MagicMock:
    entry = MagicMock()
    entry.data = {CONF_SP_TOKEN: token} if token else {}
    return entry


def _make_usage_data(
    elec_today=5.0, elec_month=120.0, water_today=0.3, water_month=8.5
):
    from sp_services import UsageData

    return UsageData(
        electricity_today_kwh=elec_today,
        electricity_month_kwh=elec_month,
        water_today_m3=water_today,
        water_month_m3=water_month,
        account_no="9999999",
        last_updated=datetime(2026, 4, 12, 10, 0),
        electricity_last_month_kwh=310.0,
        water_last_month_m3=15.2,
    )


@pytest.mark.asyncio
async def test_fetch_usage_returns_data():
    """Coordinator returns UsageData on successful fetch."""
    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    usage = _make_usage_data()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.fetch_usage = AsyncMock(return_value=usage)

    with patch(
        "custom_components.singapore.sp_services_coordinator.SpServicesClient",
        return_value=mock_client,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.electricity_today_kwh == 5.0
    assert coordinator.data.water_month_m3 == 8.5


@pytest.mark.asyncio
async def test_missing_token_raises_update_failed():
    """Coordinator raises UpdateFailed when no token is stored."""
    hass = MagicMock()
    entry = _make_entry(token=None)
    coordinator = SpServicesCoordinator(hass, entry)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success


@pytest.mark.asyncio
async def test_session_expired_raises_auth_failed():
    """SessionExpiredError is surfaced as ConfigEntryAuthFailed."""
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from sp_services import SessionExpiredError

    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.fetch_usage = AsyncMock(side_effect=SessionExpiredError("expired"))

    with patch(
        "custom_components.singapore.sp_services_coordinator.SpServicesClient",
        return_value=mock_client,
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_api_error_raises_update_failed():
    """ApiError is wrapped in UpdateFailed."""
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from sp_services import ApiError

    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.fetch_usage = AsyncMock(side_effect=ApiError("bad response"))

    with patch(
        "custom_components.singapore.sp_services_coordinator.SpServicesClient",
        return_value=mock_client,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
