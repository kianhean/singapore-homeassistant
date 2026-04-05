"""Singapore electricity tariff integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

_LOGGER = logging.getLogger(__name__)

DOMAIN = "singapore"
PLATFORMS = [Platform.SENSOR, Platform.WEATHER, Platform.CALENDAR]

# COE results are published after each bidding exercise; refresh daily at 19:30.
_COE_REFRESH_HOUR = 19
_COE_REFRESH_MINUTE = 30


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration and kick off the first data fetch."""
    from .coe_coordinator import CoeCoordinator
    from .coordinator import SPGroupCoordinator
    from .holiday_coordinator import PublicHolidayCoordinator
    from .train_coordinator import TrainStatusCoordinator
    from .weather_coordinator import SingaporeWeatherCoordinator

    tariff_coordinator = SPGroupCoordinator(hass)
    weather_coordinator = SingaporeWeatherCoordinator(hass)
    holiday_coordinator = PublicHolidayCoordinator(hass)
    train_coordinator = TrainStatusCoordinator(hass)
    coe_coordinator = CoeCoordinator(hass)

    # Fetch independent data sources concurrently to speed up setup.
    await asyncio.gather(
        tariff_coordinator.async_config_entry_first_refresh(),
        weather_coordinator.async_config_entry_first_refresh(),
        holiday_coordinator.async_config_entry_first_refresh(),
        train_coordinator.async_config_entry_first_refresh(),
    )

    async def _initial_refresh_coe() -> None:
        await coe_coordinator.async_refresh()
        if not coe_coordinator.last_update_success:
            _LOGGER.warning(
                "Initial COE refresh failed; continuing setup and retrying later"
            )

    # Don't block setup on COE API availability/rate limits.
    hass.async_create_task(_initial_refresh_coe())

    async def _refresh_coe(_now) -> None:
        await coe_coordinator.async_refresh()

    unsub_coe = async_track_time_change(
        hass,
        _refresh_coe,
        hour=_COE_REFRESH_HOUR,
        minute=_COE_REFRESH_MINUTE,
        second=0,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "tariff": tariff_coordinator,
        "coe": coe_coordinator,
        "weather": weather_coordinator,
        "holiday": holiday_coordinator,
        "train": train_coordinator,
        "unsub_coe": unsub_coe,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data["unsub_coe"]()
    return unload_ok
