"""Tests for the diagnostics platform."""

from datetime import date
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntry

from custom_components.singapore import SingaporeData
from custom_components.singapore.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.singapore.holiday_coordinator import PublicHoliday
from custom_components.singapore.train_coordinator import TrainStatusData


def _coordinator(data, success=True, exc=None):
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = success
    coordinator.last_exception = exc
    return coordinator


async def test_diagnostics_serializes_all_coordinators():
    entry = ConfigEntry()
    entry.runtime_data = SingaporeData(
        tariff=_coordinator(None, success=False, exc=RuntimeError("boom")),
        coe=_coordinator(None),
        weather=_coordinator(None),
        holiday=_coordinator([PublicHoliday(name="New Year", day=date(2026, 1, 1))]),
        train=_coordinator(
            TrainStatusData(status="normal", details="", line_statuses={})
        ),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    coordinators = result["coordinators"]
    assert set(coordinators) == {"tariff", "coe", "weather", "holiday", "train"}
    assert coordinators["tariff"]["last_update_success"] is False
    assert "boom" in coordinators["tariff"]["last_exception"]
    assert coordinators["holiday"]["data"] == [
        {"name": "New Year", "day": date(2026, 1, 1)}
    ]
    assert coordinators["train"]["data"]["status"] == "normal"
    assert coordinators["coe"]["data"] is None
