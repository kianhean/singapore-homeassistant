"""Diagnostics support for the Singapore integration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import SingaporeConfigEntry
from .sp_services_coordinator import SpServicesCoordinator

_HISTORY_SERIES = (
    "electricity_hourly_history",
    "electricity_daily_history",
    "electricity_monthly_history",
    "water_monthly_history",
)
_SAMPLE_POINTS = 3


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SingaporeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The public data sources need no redacting. SP Services accounts report
    only derived usage values: never the session (a login credential), the
    access token or the account number.
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
            "psi": _coordinator_diagnostics(data.psi),
        },
        "sp_accounts": [
            _sp_account_diagnostics(coordinator)
            for coordinator in data.sp_accounts.values()
        ],
    }


def _sp_account_diagnostics(coordinator: SpServicesCoordinator) -> dict[str, Any]:
    data = coordinator.data
    expires_at = coordinator.token_expires_at
    usage = data.usage if data else None
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception)
        if coordinator.last_exception
        else None,
        "import_statistics": coordinator.import_statistics,
        "token_expires_at": expires_at.isoformat() if expires_at else None,
        "values": {
            "electricity_today": data.electricity_today,
            "electricity_this_month": data.electricity_this_month,
            "electricity_last_month": data.electricity_last_month,
            "water_this_month": data.water_this_month,
            "water_last_month": data.water_last_month,
        }
        if data
        else None,
        # The newest few points of each series show which period labels SP
        # sends, which is what breaks first when SP changes its exports.
        "history_samples": {
            name: [
                {"period": p.period, "value": p.value, "status": p.status}
                for p in (getattr(usage, name) or [])[-_SAMPLE_POINTS:]
            ]
            for name in _HISTORY_SERIES
        }
        if usage
        else None,
        "history_points": {
            name: len(getattr(usage, name) or []) for name in _HISTORY_SERIES
        }
        if usage
        else None,
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
