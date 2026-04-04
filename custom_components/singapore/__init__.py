"""Singapore electricity tariff integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coe_coordinator import CoeCoordinator
from .coordinator import SPGroupCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "singapore"
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration and kick off the first data fetch."""
    tariff_coordinator = SPGroupCoordinator(hass)
    await tariff_coordinator.async_config_entry_first_refresh()

    coe_coordinator = CoeCoordinator(hass)
    await coe_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "tariff": tariff_coordinator,
        "coe": coe_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
