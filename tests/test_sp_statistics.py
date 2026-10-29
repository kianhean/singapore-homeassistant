"""Tests for importing SP Services usage into long-term statistics."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sp_services import UsageData, UsagePoint

from custom_components.singapore import sp_statistics
from custom_components.singapore.sp_statistics import (
    SP_TIMEZONE,
    async_import_usage_statistics,
    hourly_electricity,
    monthly_water,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=SP_TIMEZONE).astimezone(UTC)


def test_half_hours_are_summed_into_utc_hours():
    points = [
        UsagePoint("2026-10-29 00:00:00", 0.5),
        UsagePoint("2026-10-29 00:30:00", 0.25),
        UsagePoint("2026-10-29 01:00:00", 0.4),
        UsagePoint("2026-10-29 01:30:00", None),
        UsagePoint("2026-10-29 02:00:00", 0.3),
        UsagePoint("2026-10-29 02:30:00", 0.3),
    ]

    assert hourly_electricity(points) == [
        (_utc(2026, 10, 29, 0), 0.75),
        (_utc(2026, 10, 29, 1), 0.4),
        (_utc(2026, 10, 29, 2), 0.6),
    ]


def test_incomplete_latest_hour_is_held_back():
    points = [
        UsagePoint("2026-10-29 10:00:00", 0.5),
        UsagePoint("2026-10-29 10:30:00", 0.5),
        UsagePoint("2026-10-29 11:00:00", 0.4),
    ]

    assert hourly_electricity(points) == [(_utc(2026, 10, 29, 10), 1.0)]


def test_day_first_periods_parse():
    points = [
        UsagePoint("29/10/2026 08:00", 1.0),
        UsagePoint("29/10/2026 08:30", 1.0),
    ]
    assert hourly_electricity(points) == [(_utc(2026, 10, 29, 8), 2.0)]


def test_water_skips_in_progress_month():
    now = datetime(2026, 10, 29, 12, tzinfo=SP_TIMEZONE)
    points = [
        UsagePoint("2026-08-01", 30.0, "Actual"),
        UsagePoint("2026-09-01", 33.9, "Actual"),
        UsagePoint("2026-10-01", 31.0, "Estimated"),
    ]

    assert monthly_water(points, now) == [
        (_utc(2026, 8, 1, 0), 30.0),
        (_utc(2026, 9, 1, 0), 33.9),
    ]


def _hass(recorder_loaded: bool = True) -> MagicMock:
    hass = MagicMock()
    hass.config.components = {"recorder"} if recorder_loaded else set()
    return hass


async def _run_import(hass, last_rows, usage):
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value=last_rows)
    with (
        patch.object(sp_statistics, "get_instance", return_value=recorder),
        patch.object(sp_statistics, "async_add_external_statistics") as add,
    ):
        await async_import_usage_statistics(hass, "123", usage)
    return add


def _usage(hourly=None, water=None) -> UsageData:
    return UsageData(
        electricity_today_kwh=None,
        electricity_month_kwh=None,
        water_today_m3=None,
        water_month_m3=None,
        account_no="123",
        last_updated=datetime.now(SP_TIMEZONE),
        electricity_hourly_history=hourly,
        water_monthly_history=water,
    )


async def test_import_continues_running_sum_after_last_statistic():
    hourly = [
        UsagePoint(f"2026-10-29 {h:02d}:{m}:00", 0.5)
        for h in range(0, 4)
        for m in ("00", "30")
    ]
    last_start = _utc(2026, 10, 29, 1).timestamp()
    last_rows = {"singapore:sp_123_electricity": [{"start": last_start, "sum": 100.0}]}

    add = await _run_import(_hass(), last_rows, _usage(hourly=hourly))

    add.assert_called_once()
    _, metadata, stats = add.call_args.args
    assert metadata["statistic_id"] == "singapore:sp_123_electricity"
    assert metadata["source"] == "singapore"
    assert metadata["has_sum"] is True
    assert metadata["unit_of_measurement"] == "kWh"
    assert [s["start"] for s in stats] == [
        _utc(2026, 10, 29, 2),
        _utc(2026, 10, 29, 3),
    ]
    assert [s["sum"] for s in stats] == [101.0, 102.0]


async def test_first_import_starts_sum_at_zero_and_adds_water():
    hourly = [
        UsagePoint("2026-10-29 00:00:00", 0.5),
        UsagePoint("2026-10-29 00:30:00", 0.5),
    ]
    water = [UsagePoint("2026-09-01", 33.9)]

    add = await _run_import(_hass(), {}, _usage(hourly=hourly, water=water))

    assert add.call_count == 2
    elec, water_call = add.call_args_list
    assert elec.args[2][0]["sum"] == 1.0
    assert water_call.args[1]["statistic_id"] == "singapore:sp_123_water"
    assert water_call.args[1]["unit_of_measurement"] == "m³"
    assert water_call.args[2][0]["state"] == 33.9


async def test_nothing_new_adds_nothing():
    hourly = [
        UsagePoint("2026-10-29 00:00:00", 0.5),
        UsagePoint("2026-10-29 00:30:00", 0.5),
    ]
    last_rows = {
        "singapore:sp_123_electricity": [
            {"start": _utc(2026, 10, 29, 0).timestamp(), "sum": 5.0}
        ]
    }

    add = await _run_import(_hass(), last_rows, _usage(hourly=hourly))

    add.assert_not_called()


async def test_skipped_without_recorder():
    hourly = [
        UsagePoint("2026-10-29 00:00:00", 0.5),
        UsagePoint("2026-10-29 00:30:00", 0.5),
    ]

    add = await _run_import(_hass(recorder_loaded=False), {}, _usage(hourly=hourly))

    add.assert_not_called()


def test_daily_points_skip_bad_dates_and_trailing_zero_days():
    # Shape of a live export: an impossible date, a negative correction and
    # unpublished days at the end filled with 0.
    points = [
        UsagePoint("2026-06-27", -9.487),
        UsagePoint("2026-06-30", 38.274),
        UsagePoint("2026-06-31", 0.0),
        UsagePoint("2026-07-01", 0.0),
        UsagePoint("2026-07-29", 13.192),
        UsagePoint("2026-07-30", 0.0),
        UsagePoint("2026-07-31", 0.0),
    ]

    assert sp_statistics.daily_electricity(points) == [
        (_utc(2026, 6, 27, 0), -9.487),
        (_utc(2026, 6, 30, 0), 38.274),
        (_utc(2026, 7, 1, 0), 0.0),
        (_utc(2026, 7, 29, 0), 13.192),
    ]


def test_electricity_uses_daily_history_without_half_hourly_data():
    usage = _usage(hourly=[])
    usage.electricity_daily_history = [
        UsagePoint("2026-07-28", 44.519),
        UsagePoint("2026-07-29", 13.192),
    ]

    assert sp_statistics.electricity_points(usage) == [
        (_utc(2026, 7, 28, 0), 44.519),
        (_utc(2026, 7, 29, 0), 13.192),
    ]


def test_daily_history_only_fills_days_before_half_hourly_data():
    usage = _usage(
        hourly=[
            UsagePoint("2026-07-29 00:00:00", 0.5),
            UsagePoint("2026-07-29 00:30:00", 0.5),
        ]
    )
    usage.electricity_daily_history = [
        UsagePoint("2026-07-28", 44.519),
        UsagePoint("2026-07-29", 13.192),
    ]

    assert sp_statistics.electricity_points(usage) == [
        (_utc(2026, 7, 28, 0), 44.519),
        (_utc(2026, 7, 29, 0), 1.0),
    ]
