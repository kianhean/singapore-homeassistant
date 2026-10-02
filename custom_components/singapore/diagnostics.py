"""Diagnostics support for the Singapore integration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import SingaporeConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SingaporeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    All data comes from public government/utility sources, so nothing needs
    redacting.
    """
    data = entry.runtime_data
    return {
        "entry": {"title": entry.title, "version": entry.version},
        "coordinators": {
            "tariff": _coordinator_diagnostics(data.tariff),
            "coe": _coordinator_diagnostics(data.coe),
            "weather": _coordinator_diagnostics(data.weather),
            "holiday": _coordinator_diagnostics(data.holiday),
            "train": _coordinator_diagnostics(data.train),
        },
    }


def _coordinator_diagnostics(coordinator: DataUpdateCoordinator) -> dict[str, Any]:
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception)
        if coordinator.last_exception
        else None,
        "data": _serialize(coordinator.data),
    }


def _serialize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
