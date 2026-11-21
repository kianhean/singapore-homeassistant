"""Diagnostics support for the Singapore integration."""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import SingaporeConfigEntry
from .const import (
    CONF_API_KEY,
    CONF_LTA_API_KEY,
    CONF_POLLING,
    POLLING_NORMAL,
    SCHOOL_EVENT_KINDS,
    SOURCES,
    enabled_school_events,
    enabled_sources,
)
from .sp_account.const import CONF_REFRESH_TOKEN
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
        "options": {
            "disabled_sources": sorted(
                source for source in SOURCES if source not in enabled_sources(entry)
            ),
            "disabled_school_events": [
                kind
                for kind in SCHOOL_EVENT_KINDS
                if kind not in enabled_school_events(entry)
            ],
            "polling": entry.options.get(CONF_POLLING, POLLING_NORMAL),
            # Never the key itself: diagnostics get pasted into public issues.
            "has_api_key": bool(entry.options.get(CONF_API_KEY)),
            "has_lta_api_key": bool(entry.options.get(CONF_LTA_API_KEY)),
        },
        "coordinators": {
            source: _coordinator_diagnostics(coordinator)
            for source, coordinator in data.coordinators().items()
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
        "electricity_price_sgd_kwh": coordinator.electricity_price,
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
        "has_peer_comparison": any(
            period.peer_average is not None
            for period in (*usage.electricity_periods, *usage.water_periods)
        ),
        "has_meter_reading_window": bool(
            usage.meter_reading
            and usage.meter_reading.start
            and usage.meter_reading.end
        ),
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
        # Fields marked metadata={"diagnostics": False} stay out (e.g. taxi
        # positions around home: downloads get pasted into public issues).
        result = asdict(value)
        for item in fields(value):
            if item.metadata.get("diagnostics") is False:
                result.pop(item.name, None)
        return result
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
