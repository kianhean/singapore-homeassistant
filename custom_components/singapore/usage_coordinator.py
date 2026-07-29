"""Data coordinator for SP Services household electricity and water usage."""

from __future__ import annotations

import logging
from datetime import timedelta

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
)

_LOGGER = logging.getLogger(__name__)

CONF_SP_REFRESH_TOKEN = "sp_refresh_token"
CONF_SP_ACCOUNT_NO = "sp_account_no"

# One fetch makes up to six requests against private SP endpoints, and SP
# refreshes this data slowly — polling faster gains nothing.
UPDATE_INTERVAL = timedelta(minutes=30)


class SPUsageCoordinator(DataUpdateCoordinator[UsageData]):
    """Fetches household usage from SP Services, refreshing tokens as needed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        refresh_token: str,
        account_no: str | None = None,
    ) -> None:
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
        self._refresh_token = refresh_token
        self._account_no = account_no
        self._token: TokenSet | None = None

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
                return await async_fetch_usage(
                    session, token.access_token, self._account_no
                )
            except SPUsageSessionExpired:
                # SP rejected an access token we believed was still valid;
                # refresh once and retry before giving up on the session.
                _LOGGER.debug("SP access token rejected mid-fetch; refreshing")
                token = await self._async_refresh(session)
                return await async_fetch_usage(
                    session, token.access_token, self._account_no
                )
        except SPUsageAuthError as err:
            # Only a new browser login can fix this — trigger HA's reauth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SPUsageApiError as err:
            raise UpdateFailed(f"SP Services returned unusable data: {err}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with SP Services: {err}") from err

    async def _async_valid_token(self, session: aiohttp.ClientSession) -> TokenSet:
        if self._token is not None and not self._token.is_expired():
            return self._token
        return await self._async_refresh(session)

    async def _async_refresh(self, session: aiohttp.ClientSession) -> TokenSet:
        token = await async_refresh_token(session, self._refresh_token)
        self._token = token
        if token.refresh_token and token.refresh_token != self._refresh_token:
            # Auth0 rotated the refresh token; persist it or the next restart
            # would authenticate with a token SP has already invalidated.
            self._refresh_token = token.refresh_token
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_SP_REFRESH_TOKEN: token.refresh_token},
            )
        return token
