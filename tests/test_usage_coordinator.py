"""Tests for the SP Services usage coordinator."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.sp_usage_client import (
    SP_TIMEZONE,
    SPUsageApiError,
    SPUsageAuthError,
    SPUsageSessionExpired,
    TokenSet,
    UsageData,
)
from custom_components.singapore.usage_coordinator import (
    CONF_SP_REFRESH_TOKEN,
    SPUsageCoordinator,
)

_USAGE = UsageData(
    account_no="8949049293",
    last_updated=datetime(2026, 4, 12, 16, 42, tzinfo=SP_TIMEZONE),
    electricity_today_kwh=19.967,
    electricity_month_kwh=120.5,
)


def _coordinator(entry=None):
    hass = MagicMock()
    entry = entry or ConfigEntry(data={CONF_SP_REFRESH_TOKEN: "stored-refresh"})
    coordinator = SPUsageCoordinator(hass, entry, "stored-refresh", "8949049293")
    return coordinator, hass, entry


@pytest.mark.asyncio
async def test_update_refreshes_token_then_fetches():
    coordinator, _hass, _entry = _coordinator()
    refresh = AsyncMock(return_value=TokenSet(access_token="at", refresh_token="rt"))
    fetch = AsyncMock(return_value=_USAGE)

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token", refresh
        ),
        patch("custom_components.singapore.usage_coordinator.async_fetch_usage", fetch),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.electricity_today_kwh == 19.967
    assert coordinator.account_no == "8949049293"
    assert fetch.await_args.args[1] == "at"
    assert fetch.await_args.args[2] == "8949049293"


@pytest.mark.asyncio
async def test_valid_token_is_reused_across_updates():
    coordinator, _hass, _entry = _coordinator()
    refresh = AsyncMock(return_value=TokenSet(access_token="at", refresh_token="rt"))

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token", refresh
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_fetch_usage",
            AsyncMock(return_value=_USAGE),
        ),
    ):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert refresh.await_count == 1


@pytest.mark.asyncio
async def test_rotated_refresh_token_is_persisted():
    coordinator, hass, entry = _coordinator()
    refresh = AsyncMock(
        return_value=TokenSet(access_token="at", refresh_token="rotated")
    )

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token", refresh
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_fetch_usage",
            AsyncMock(return_value=_USAGE),
        ),
    ):
        await coordinator.async_refresh()

    hass.config_entries.async_update_entry.assert_called_once()
    kwargs = hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"][CONF_SP_REFRESH_TOKEN] == "rotated"


@pytest.mark.asyncio
async def test_unrotated_refresh_token_is_not_rewritten():
    coordinator, hass, _entry = _coordinator()
    refresh = AsyncMock(
        return_value=TokenSet(access_token="at", refresh_token="stored-refresh")
    )

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token", refresh
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_fetch_usage",
            AsyncMock(return_value=_USAGE),
        ),
    ):
        await coordinator.async_refresh()

    hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_session_expired_mid_fetch_refreshes_and_retries():
    coordinator, _hass, _entry = _coordinator()
    refresh = AsyncMock(
        side_effect=[
            TokenSet(access_token="stale", refresh_token="stored-refresh"),
            TokenSet(access_token="fresh", refresh_token="stored-refresh"),
        ]
    )
    fetch = AsyncMock(side_effect=[SPUsageSessionExpired("401"), _USAGE])

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token", refresh
        ),
        patch("custom_components.singapore.usage_coordinator.async_fetch_usage", fetch),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert refresh.await_count == 2
    assert fetch.await_args.args[1] == "fresh"


@pytest.mark.asyncio
async def test_dead_refresh_token_raises_auth_failed():
    coordinator, _hass, _entry = _coordinator()

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token",
            AsyncMock(side_effect=SPUsageAuthError("refresh token revoked")),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_api_error_raises_update_failed():
    coordinator, _hass, _entry = _coordinator()

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token",
            AsyncMock(return_value=TokenSet(access_token="at")),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_fetch_usage",
            AsyncMock(side_effect=SPUsageApiError("markup changed")),
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_network_error_raises_update_failed():
    coordinator, _hass, _entry = _coordinator()

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_refresh_token",
            AsyncMock(side_effect=aiohttp.ClientError("connection reset")),
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()
