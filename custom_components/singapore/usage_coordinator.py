"""Data coordinator for SP Services household electricity and water usage."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .sp_usage_client import (
    SPUsageApiError,
    SPUsageAuthError,
    SPUsageSessionExpired,
    TokenSet,
    UsageData,
    async_fetch_usage,
    async_refresh_token,
    async_renew_with_session_cookie,
)

_LOGGER = logging.getLogger(__name__)

CONF_SP_REFRESH_TOKEN = "sp_refresh_token"
CONF_SP_ACCESS_TOKEN = "sp_access_token"
CONF_SP_ACCESS_TOKEN_EXPIRES_AT = "sp_access_token_expires_at"
CONF_SP_SESSION_COOKIE = "sp_session_cookie"
CONF_SP_ACCOUNT_NO = "sp_account_no"

# One fetch makes up to six requests against private SP endpoints, and SP
# refreshes this data slowly — polling faster gains nothing.
UPDATE_INTERVAL = timedelta(minutes=30)


def token_entry_data(token: TokenSet) -> dict[str, Any]:
    """Render a token set into the fields persisted on the config entry.

    Every field is always written, including ``None``: these are merged over
    existing entry data, and a re-link that returns no refresh token must clear
    the previous one rather than leave a dead token behind.
    """
    return {
        CONF_SP_ACCESS_TOKEN: token.access_token,
        CONF_SP_ACCESS_TOKEN_EXPIRES_AT: (
            token.expires_at.isoformat() if token.expires_at else None
        ),
        CONF_SP_REFRESH_TOKEN: token.refresh_token,
    }


def stored_token(data: Mapping[str, Any]) -> TokenSet | None:
    """Rebuild the token set persisted on the config entry, if any."""
    access_token = data.get(CONF_SP_ACCESS_TOKEN)
    if not access_token:
        return None

    expires_at: datetime | None = None
    raw_expiry = data.get(CONF_SP_ACCESS_TOKEN_EXPIRES_AT)
    if raw_expiry:
        try:
            expires_at = datetime.fromisoformat(str(raw_expiry))
        except ValueError:
            _LOGGER.debug("Ignoring unparsable stored token expiry %s", raw_expiry)

    return TokenSet(
        access_token=str(access_token),
        refresh_token=data.get(CONF_SP_REFRESH_TOKEN),
        expires_at=expires_at,
    )


def has_sp_credentials(data: Mapping[str, Any]) -> bool:
    """Whether the entry carries anything usable to talk to SP Services."""
    return bool(
        data.get(CONF_SP_REFRESH_TOKEN)
        or data.get(CONF_SP_ACCESS_TOKEN)
        or data.get(CONF_SP_SESSION_COOKIE)
    )


class SPUsageCoordinator(DataUpdateCoordinator[UsageData]):
    """Fetches household usage from SP Services, refreshing tokens as needed.

    SP's Auth0 tenant does not always honour ``offline_access``. When it issues
    a refresh token the integration keeps itself logged in; when it does not,
    the stored access token is used until it expires and HA's reauth flow then
    asks the user for another browser login.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="SP Services Usage",
            update_interval=UPDATE_INTERVAL,
            # Lets the coordinator start HA's reauth flow when the SP session
            # dies and only a fresh browser login can restore it.
            config_entry=entry,
        )
        self._entry = entry
        self._account_no: str | None = entry.data.get(CONF_SP_ACCOUNT_NO)
        self._refresh_token: str | None = entry.data.get(CONF_SP_REFRESH_TOKEN)
        self._session_cookie: str | None = entry.data.get(CONF_SP_SESSION_COOKIE)
        self._token: TokenSet | None = stored_token(entry.data)

    @property
    def account_no(self) -> str | None:
        """Account number in use, once known."""
        if self.data is not None and self.data.account_no:
            return self.data.account_no
        return self._account_no

    async def _async_update_data(self) -> UsageData:
        session = async_get_clientsession(self.hass)

        try:
            token = await self._async_valid_token(session)
            try:
                return await self._async_fetch(session, token)
            except SPUsageSessionExpired:
                # SP rejected an access token we believed was still valid;
                # refresh once and retry before giving up on the session.
                _LOGGER.debug("SP access token rejected mid-fetch; refreshing")
                token = await self._async_renew_token(session)
                return await self._async_fetch(session, token)
        except SPUsageAuthError as err:
            # Only a new browser login can fix this — trigger HA's reauth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SPUsageApiError as err:
            raise UpdateFailed(f"SP Services returned unusable data: {err}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with SP Services: {err}") from err

    async def _async_fetch(
        self, session: aiohttp.ClientSession, token: TokenSet
    ) -> UsageData:
        data = await async_fetch_usage(session, token.access_token, self._account_no)
        # SP legitimately publishes nothing for some fields, so a successful
        # update can still leave every sensor unknown. Say which ones came back
        # so "no data" can be told apart from "not running".
        _LOGGER.debug(
            "SP usage for account %s: today=%s kWh, this month=%s kWh, last month=%s "
            "kWh, water this month=%s m³, water last month=%s m³ "
            "(%s hourly, %s daily, %s monthly points)",
            data.account_no,
            data.electricity_today_kwh,
            data.electricity_month_kwh,
            data.electricity_last_month_kwh,
            data.water_month_m3,
            data.water_last_month_m3,
            len(data.electricity_hourly_history),
            len(data.electricity_daily_history),
            len(data.electricity_monthly_history),
        )
        return data

    async def _async_valid_token(self, session: aiohttp.ClientSession) -> TokenSet:
        if self._token is not None and not self._token.is_expired():
            return self._token
        return await self._async_renew_token(session)

    async def _async_renew_token(self, session: aiohttp.ClientSession) -> TokenSet:
        if self._refresh_token:
            token = await async_refresh_token(session, self._refresh_token)
            self._refresh_token = token.refresh_token or self._refresh_token
            self._token = token
            # Persist the new access token too: without it a restart before the
            # token expires would burn a refresh round trip, and in accounts
            # where SP issues no refresh token it is the only thing keeping the
            # entry alive across restarts.
            self._async_persist(token)
            return token

        if self._session_cookie:
            token, cookie = await async_renew_with_session_cookie(
                session, self._session_cookie
            )
            self._session_cookie = cookie
            self._token = token
            self._async_persist(token, cookie)
            return token

        raise SPUsageAuthError(
            "the SP Services session has expired and SP did not issue a "
            "refresh token; sign in again to restore usage data"
        )

    def _async_persist(self, token: TokenSet, cookie: str | None = None) -> None:
        data = {**self._entry.data, **token_entry_data(token)}
        if cookie is not None:
            data[CONF_SP_SESSION_COOKIE] = cookie
        if data != dict(self._entry.data):
            self.hass.config_entries.async_update_entry(self._entry, data=data)
