"""Tests for the SP Services usage coordinator."""

from datetime import datetime, timedelta, timezone
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
    CONF_SP_ACCESS_TOKEN,
    CONF_SP_ACCESS_TOKEN_EXPIRES_AT,
    CONF_SP_ACCOUNT_NO,
    CONF_SP_REFRESH_TOKEN,
    CONF_SP_SESSION_COOKIE,
    SPUsageCoordinator,
    stored_token,
)

_USAGE = UsageData(
    account_no="8949049293",
    last_updated=datetime(2026, 4, 12, 16, 42, tzinfo=SP_TIMEZONE),
    electricity_today_kwh=19.967,
    electricity_month_kwh=120.5,
)


def _coordinator(data=None):
    hass = MagicMock()
    entry = ConfigEntry(
        data=data
        if data is not None
        else {
            CONF_SP_REFRESH_TOKEN: "stored-refresh",
            CONF_SP_ACCOUNT_NO: "8949049293",
        }
    )
    return SPUsageCoordinator(hass, entry), hass, entry


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
async def test_access_token_is_persisted_for_restarts():
    coordinator, hass, _entry = _coordinator()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    refresh = AsyncMock(
        return_value=TokenSet(
            access_token="at", refresh_token="stored-refresh", expires_at=expires_at
        )
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

    stored = hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert stored[CONF_SP_ACCESS_TOKEN] == "at"
    assert stored[CONF_SP_ACCESS_TOKEN_EXPIRES_AT] == expires_at.isoformat()
    assert stored[CONF_SP_REFRESH_TOKEN] == "stored-refresh"


@pytest.mark.asyncio
async def test_stored_access_token_is_used_without_refreshing():
    """SP does not always issue a refresh token; the access token must carry."""
    coordinator, _hass, _entry = _coordinator(
        {
            CONF_SP_ACCESS_TOKEN: "still-valid",
            CONF_SP_ACCESS_TOKEN_EXPIRES_AT: (
                datetime.now(timezone.utc) + timedelta(hours=2)
            ).isoformat(),
            CONF_SP_ACCOUNT_NO: "8949049293",
        }
    )
    refresh = AsyncMock()
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
    refresh.assert_not_awaited()
    assert fetch.await_args.args[1] == "still-valid"


@pytest.mark.asyncio
async def test_expired_access_token_without_refresh_token_asks_for_reauth():
    coordinator, _hass, _entry = _coordinator(
        {
            CONF_SP_ACCESS_TOKEN: "expired",
            CONF_SP_ACCESS_TOKEN_EXPIRES_AT: (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        }
    )

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        pytest.raises(ConfigEntryAuthFailed, match="did not issue a refresh token"),
    ):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_rejected_access_token_without_refresh_token_asks_for_reauth():
    coordinator, _hass, _entry = _coordinator({CONF_SP_ACCESS_TOKEN: "revoked"})

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_fetch_usage",
            AsyncMock(side_effect=SPUsageSessionExpired("401")),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await coordinator._async_update_data()


def test_stored_token_parsing():
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    token = stored_token(
        {
            CONF_SP_ACCESS_TOKEN: "at",
            CONF_SP_ACCESS_TOKEN_EXPIRES_AT: expires_at.isoformat(),
            CONF_SP_REFRESH_TOKEN: "rt",
        }
    )
    assert token.access_token == "at"
    assert token.refresh_token == "rt"
    assert token.expires_at == expires_at
    assert stored_token({CONF_SP_REFRESH_TOKEN: "rt"}) is None


def test_stored_token_survives_unparsable_expiry():
    token = stored_token(
        {CONF_SP_ACCESS_TOKEN: "at", CONF_SP_ACCESS_TOKEN_EXPIRES_AT: "not-a-date"}
    )
    # Unknown expiry: use it and let a 401 drive reauth instead of discarding it.
    assert token.expires_at is None
    assert token.is_expired() is False


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


@pytest.mark.asyncio
async def test_expired_token_renews_with_session_cookie():
    """No refresh token: the Auth0 session cookie keeps the entry alive."""
    coordinator, _hass, _entry = _coordinator(
        {
            CONF_SP_ACCESS_TOKEN: "expired",
            CONF_SP_ACCESS_TOKEN_EXPIRES_AT: (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
            CONF_SP_SESSION_COOKIE: "auth0=stored",
            CONF_SP_ACCOUNT_NO: "8949049293",
        }
    )
    renew = AsyncMock(return_value=(TokenSet(access_token="renewed"), "auth0=rotated"))
    fetch = AsyncMock(return_value=_USAGE)

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator."
            "async_renew_with_session_cookie",
            renew,
        ),
        patch("custom_components.singapore.usage_coordinator.async_fetch_usage", fetch),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert renew.await_args.args[1] == "auth0=stored"
    assert fetch.await_args.args[1] == "renewed"


@pytest.mark.asyncio
async def test_rotated_session_cookie_is_persisted():
    coordinator, hass, _entry = _coordinator(
        {CONF_SP_SESSION_COOKIE: "auth0=stored", CONF_SP_ACCOUNT_NO: "8949049293"}
    )

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator."
            "async_renew_with_session_cookie",
            AsyncMock(return_value=(TokenSet(access_token="renewed"), "auth0=rotated")),
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_fetch_usage",
            AsyncMock(return_value=_USAGE),
        ),
    ):
        await coordinator.async_refresh()

    stored = hass.config_entries.async_update_entry.call_args.kwargs["data"]
    # Auth0 rolls the session cookie; dropping the new one would end the session.
    assert stored[CONF_SP_SESSION_COOKIE] == "auth0=rotated"
    assert stored[CONF_SP_ACCESS_TOKEN] == "renewed"


@pytest.mark.asyncio
async def test_refresh_token_is_preferred_over_session_cookie():
    coordinator, _hass, _entry = _coordinator(
        {
            CONF_SP_REFRESH_TOKEN: "stored-refresh",
            CONF_SP_SESSION_COOKIE: "auth0=stored",
        }
    )
    renew = AsyncMock()

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
            "custom_components.singapore.usage_coordinator."
            "async_renew_with_session_cookie",
            renew,
        ),
        patch(
            "custom_components.singapore.usage_coordinator.async_fetch_usage",
            AsyncMock(return_value=_USAGE),
        ),
    ):
        await coordinator.async_refresh()

    renew.assert_not_awaited()


@pytest.mark.asyncio
async def test_dead_session_cookie_asks_for_reauth():
    coordinator, _hass, _entry = _coordinator({CONF_SP_SESSION_COOKIE: "auth0=stale"})

    with (
        patch(
            "custom_components.singapore.usage_coordinator.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.usage_coordinator."
            "async_renew_with_session_cookie",
            AsyncMock(side_effect=SPUsageSessionExpired("login_required")),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await coordinator._async_update_data()
