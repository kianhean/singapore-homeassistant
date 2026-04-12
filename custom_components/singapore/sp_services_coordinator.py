"""Coordinator for SP Services household energy and water usage data."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from sp_services import (
    ApiError,
    AuthenticationError,
    SessionExpiredError,
    SpServicesClient,
    SpServicesError,
    UsageData,
)

_LOGGER = logging.getLogger(__name__)

_UPDATE_INTERVAL = timedelta(hours=1)

CONF_SP_TOKEN = "sp_token"


class SpServicesCoordinator(DataUpdateCoordinator[UsageData]):
    """Fetches household electricity and water usage from SP Services portal."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name="SP Services Usage",
            update_interval=_UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> UsageData:
        token = self._entry.data.get(CONF_SP_TOKEN)
        if not token:
            raise UpdateFailed("No SP Services token in entry data")

        try:
            async with SpServicesClient() as client:
                return await client.fetch_usage(token)
        except SessionExpiredError as err:
            raise ConfigEntryAuthFailed("SP Services session expired") from err
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (ApiError, SpServicesError) as err:
            raise UpdateFailed(f"SP Services error: {err}") from err
