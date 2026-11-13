"""Diagnostics support for the Singapore integration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import SingaporeConfigEntry
from .sp_account.const import CONF_ELECTRICITY_PRICE, CONF_REFRESH_TOKEN
from .sp_account_coordinator import SpAccountCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SingaporeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The public government/utility sources need no redacting. SP e-accounts are
    personal, so they are reported as presence flags and counts only (a
    download is something users paste into public issues): no address,
    account number, premise id, tokens or readings.
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
            "flood": _coordinator_diagnostics(data.flood),
            "traffic": _coordinator_diagnostics(data.traffic),
            "dengue": _coordinator_diagnostics(data.dengue),
            "lightning": _coordinator_diagnostics(data.lightning),
            "uv": _coordinator_diagnostics(data.uv),
        },
        "sp_accounts": [
            _sp_account_diagnostics(coordinator)
            for coordinator in data.sp_accounts.values()
        ],
    }


def _sp_account_diagnostics(coordinator: SpAccountCoordinator) -> dict[str, Any]:
    """Redacted SP e-account status (adapted from maci0/home-assistant-sp)."""
    subentry = coordinator.subentry
    stored = subentry.data if subentry else {}
    usage = coordinator.data
    status: dict[str, Any] = {
        "last_update_success": coordinator.last_update_success,
        "last_error": coordinator.last_error,
        "last_poll_ms": coordinator.last_poll_ms,
        "last_success": (
            coordinator.last_success.isoformat() if coordinator.last_success else None
        ),
        "failed_reads": coordinator.client.read_failures,
        "has_refresh_token": bool(stored.get(CONF_REFRESH_TOKEN)),
        "has_electricity_price": stored.get(CONF_ELECTRICITY_PRICE) is not None,
    }
    if usage is None:
        return {**status, "usage": None}
    return {
        **status,
        "account_status": usage.premise.account_status,
        "account_type": usage.premise.account_type,
        "premise_type": usage.premise.premise_type,
        "utilities": list(usage.premise.utilities),
        "ami_elec": usage.premise.ami_elec,
        "ppms_exists": usage.premise.ppms_exists,
        "has_gas": usage.gas is not None,
        "electricity_periods": len(usage.electricity_periods),
        "water_periods": len(usage.water_periods),
        "gas_periods": len(usage.gas_periods),
        "bill_count": len(usage.bills),
        "has_amount_due": usage.amount_due is not None,
        "meter_register_count": len(usage.meter_registers),
        "green_goal_kinds": [goal.kind for goal in usage.green_goals],
        "has_greenup": usage.greenup is not None,
        "has_ev_wallet": usage.ev_wallet is not None,
        "has_ev_session": usage.ev_session is not None,
        "fcu_count": len(usage.fcus),
        "has_tariff": usage.tariff is not None,
        "sensor_keys": [spec.key for spec in coordinator.sensor_specs],
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
