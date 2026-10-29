"""Tests for the diagnostics platform."""

from datetime import date
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntry

from custom_components.singapore import SingaporeData
from custom_components.singapore.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.singapore.holiday_coordinator import PublicHoliday
from custom_components.singapore.psi_coordinator import PsiData
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
        psi=_coordinator(PsiData(national={"psi_twenty_four_hourly": 39.0})),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    coordinators = result["coordinators"]
    assert set(coordinators) == {"tariff", "coe", "weather", "holiday", "train", "psi"}
    assert coordinators["tariff"]["last_update_success"] is False
    assert "boom" in coordinators["tariff"]["last_exception"]
    assert coordinators["holiday"]["data"] == [
        {"name": "New Year", "day": date(2026, 1, 1)}
    ]
    assert coordinators["train"]["data"]["status"] == "normal"
    assert coordinators["coe"]["data"] is None
    assert coordinators["psi"]["data"]["national"]["psi_twenty_four_hourly"] == 39.0
    assert result["sp_accounts"] == []


async def test_diagnostics_never_include_sp_session_or_account():
    from datetime import UTC, datetime

    from custom_components.singapore.sp_services_coordinator import SpUsageData

    usage = MagicMock()
    usage.electricity_hourly_history = [MagicMock()] * 3
    usage.electricity_daily_history = None
    usage.electricity_monthly_history = []
    usage.water_monthly_history = [MagicMock()]
    sp = _coordinator(
        SpUsageData(
            account_no="8949049293",
            electricity_today=9.5,
            electricity_this_month=None,
            electricity_last_month=572.0,
            water_this_month=31.0,
            water_last_month=33.9,
            usage=usage,
        )
    )
    sp.import_statistics = True
    sp.token_expires_at = datetime(2026, 10, 29, 8, tzinfo=UTC)
    sp.account_no = "8949049293"
    sp._session = "SECRET-SESSION"
    entry = ConfigEntry()
    entry.runtime_data = SingaporeData(
        tariff=_coordinator(None),
        coe=_coordinator(None),
        weather=_coordinator(None),
        holiday=_coordinator(None),
        train=_coordinator(None),
        psi=_coordinator(None),
        sp_accounts={"sub1": sp},
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    (account,) = result["sp_accounts"]
    assert account["values"]["electricity_last_month"] == 572.0
    assert account["history_points"] == {
        "electricity_hourly_history": 3,
        "electricity_daily_history": 0,
        "electricity_monthly_history": 0,
        "water_monthly_history": 1,
    }
    assert account["token_expires_at"] == "2026-10-29T08:00:00+00:00"
    text = repr(result)
    assert "SECRET-SESSION" not in text
    assert "8949049293" not in text
