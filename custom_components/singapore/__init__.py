"""Singapore electricity tariff integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .coe_coordinator import CoeCoordinator
from .coordinator import SPGroupCoordinator
from .holiday_coordinator import PublicHolidayCoordinator
from .train_coordinator import TrainStatusCoordinator
from .weather_coordinator import SingaporeWeatherCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "singapore"
PLATFORMS = [Platform.SENSOR, Platform.WEATHER, Platform.CALENDAR]

# COE results are published after each bidding exercise; refresh daily at 19:30.
_COE_REFRESH_HOUR = 19
_COE_REFRESH_MINUTE = 30


@dataclass
class SingaporeData:
    """Runtime data stored on the config entry."""

    tariff: SPGroupCoordinator
    coe: CoeCoordinator
    weather: SingaporeWeatherCoordinator
    holiday: PublicHolidayCoordinator
    train: TrainStatusCoordinator


SingaporeConfigEntry: TypeAlias = ConfigEntry[SingaporeData]


async def async_setup_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Set up the integration and kick off the first data fetch."""
    tariff_coordinator = SPGroupCoordinator(hass)
    weather_coordinator = SingaporeWeatherCoordinator(hass)
    holiday_coordinator = PublicHolidayCoordinator(hass)
    train_coordinator = TrainStatusCoordinator(hass)
    coe_coordinator = CoeCoordinator(hass)

    # Fetch independent data sources concurrently to speed up setup.
    # return_exceptions=True lets every coordinator finish its first refresh
    # even if a sibling fails, instead of orphaning them mid-flight when
    # gather() propagates the first exception.
    results = await asyncio.gather(
        tariff_coordinator.async_config_entry_first_refresh(),
        weather_coordinator.async_config_entry_first_refresh(),
        holiday_coordinator.async_config_entry_first_refresh(),
        train_coordinator.async_config_entry_first_refresh(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result

    async def _initial_refresh_coe() -> None:
        await coe_coordinator.async_refresh()
        if not coe_coordinator.last_update_success:
            _LOGGER.warning(
                "Initial COE refresh failed; continuing setup and retrying later"
            )

    # Don't block setup on COE API availability/rate limits.
    entry.async_create_background_task(
        hass, _initial_refresh_coe(), "singapore_coe_initial_refresh"
    )

    async def _refresh_coe(_now) -> None:
        await coe_coordinator.async_refresh()

    unsub_coe = async_track_time_change(
        hass,
        _refresh_coe,
        hour=_COE_REFRESH_HOUR,
        minute=_COE_REFRESH_MINUTE,
        second=0,
    )
    entry.async_on_unload(unsub_coe)

    entry.runtime_data = SingaporeData(
        tariff=tariff_coordinator,
        coe=coe_coordinator,
        weather=weather_coordinator,
        holiday=holiday_coordinator,
        train=train_coordinator,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
