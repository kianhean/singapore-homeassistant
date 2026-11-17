"""Derived SP e-account sensors, the account calendar and the peer statistics."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.singapore.sp_account.const import UNIT_KWH, UNIT_M3
from custom_components.singapore.sp_account.mapper import electricity_view
from custom_components.singapore.sp_account.models import (
    SG_TZ,
    BillInfo,
    GreenGoal,
    MeterReadingInfo,
    PayableInfo,
    PeriodReading,
    PremiseInfo,
    UsageReadings,
    UtilitySeries,
)
from custom_components.singapore.sp_account_calendar import (
    SpAccountCalendar,
    async_setup_sp_account_calendars,
    sp_account_events,
)
from custom_components.singapore.sp_account_coordinator import monthly_points
from custom_components.singapore.sp_account_insights import (
    always_on,
    bill_status,
    costs_so_far,
    goal_remaining,
    insights,
    meter_reading_window,
    projected_cost,
    vs_peers,
    yesterday,
)
from custom_components.singapore.sp_account_sensor import SpAccountSensor

from .sp_account.conftest import FIXED_NOW, fixture_client
from .test_sp_account import _coordinator

PRICE = 0.3


def _slots(
    start: datetime, end: datetime, kwh: float = 0.5, low: float | None = None
) -> tuple[PeriodReading, ...]:
    """Half-hour slots from start up to (not including) end.

    With ``low``, the 03:00 slot of each day reads ``low`` (the always-on load).
    """
    out = []
    at = start
    while at < end:
        amount = low if low is not None and (at.hour, at.minute) == (3, 0) else kwh
        out.append(PeriodReading(start=at, amount=amount))
        at += timedelta(minutes=30)
    return tuple(out)


def _sgt(*args: int) -> datetime:
    return datetime(*args, tzinfo=SG_TZ)


def _usage(**kwargs) -> UsageReadings:
    premise = PremiseInfo(
        id="premise-001",
        address="1 Example Road, Singapore",
        account_number="1234567890",
        account_status="Active",
        account_type="DOM",
        premise_type="HDB",
        utilities=("electric",),
        ami_elec=True,
        retailer_name=None,
        ppms_exists=False,
    )
    base = {"premise": premise, "electricity": None, "water": None, "gas": None}
    return UsageReadings(**(base | kwargs))


# ---------------------------------------------------------------------------
# Always-on power
# ---------------------------------------------------------------------------


def test_always_on_is_median_lowest_half_hour_of_recent_full_days():
    now = _sgt(2026, 8, 10, 12)
    slots = _slots(_sgt(2026, 8, 1), now, low=0.05)
    # One unusual night (everything off) must not set the figure.
    slots = tuple(
        replace(s, amount=0.01) if s.start == _sgt(2026, 8, 8, 3) else s for s in slots
    )
    result = always_on(slots, now, PRICE)
    assert result is not None
    assert result.spec.native_value == 100.0  # 0.05 kWh in 30 min = 100 W
    assert result.spec.device_class == "power"
    assert result.spec.unit_of_measurement == "W"
    assert result.attributes["days"] == 7
    assert result.attributes["estimated_yearly_kwh"] == 876.6
    assert result.attributes["estimated_yearly_cost"] == round(876.6 * PRICE, 2)


def test_always_on_needs_a_full_day_and_ignores_today():
    now = _sgt(2026, 8, 10, 12)
    assert always_on(_slots(_sgt(2026, 8, 10), now), now, PRICE) is None
    # A day with a long AMI gap is not a full day.
    gappy = _slots(_sgt(2026, 8, 9), _sgt(2026, 8, 9, 20))
    assert always_on(gappy, now, PRICE) is None
    assert (
        "estimated_yearly_cost"
        not in always_on(_slots(_sgt(2026, 8, 9), now), now, None).attributes
    )


# ---------------------------------------------------------------------------
# Yesterday, cost today / this month, projection
# ---------------------------------------------------------------------------


def test_yesterday_once_its_last_half_hour_is_reported():
    now = _sgt(2026, 8, 10, 9)
    slots = _slots(_sgt(2026, 8, 9), _sgt(2026, 8, 10, 1))
    slots = tuple(
        replace(s, amount=1.2) if s.start == _sgt(2026, 8, 9, 19, 30) else s
        for s in slots
    )
    kwh, cost = yesterday(slots, now, PRICE)
    assert kwh.spec.native_value == round(47 * 0.5 + 1.2, 3)
    assert kwh.attributes["date"] == "2026-08-09"
    assert kwh.attributes["peak_half_hour"] == "2026-08-09T19:30:00+08:00"
    assert kwh.attributes["peak_half_hour_kwh"] == 1.2
    assert cost.spec.key == "electricity_cost_yesterday"
    assert cost.spec.native_value == round(kwh.spec.native_value * PRICE, 2)
    assert cost.spec.state_class is None  # monetary: no measurement class

    # AMI still at 23:00 yesterday: not complete yet.
    partial = _slots(_sgt(2026, 8, 9), _sgt(2026, 8, 9, 23, 30))
    assert yesterday(partial, now, PRICE) == []
    # No tariff: kWh only.
    assert [i.spec.key for i in yesterday(slots, now, None)] == [
        "electricity_yesterday"
    ]


def test_costs_so_far_today_and_month():
    now = _sgt(2026, 8, 2, 12)
    slots = _slots(_sgt(2026, 7, 31), _sgt(2026, 8, 2, 10))
    view_today = 20 * 0.5
    today, month = costs_so_far(slots, view_today, now, PRICE)
    assert today.spec.key == "electricity_cost_today"
    assert today.spec.native_value == round(10 * PRICE, 2)
    assert month.spec.key == "electricity_cost_month"
    # Aug 1 (48 slots) + Aug 2 to 10:00 (20 slots); July 31 is last month.
    assert month.attributes["kwh"] == 34.0
    assert month.spec.native_value == round(34 * PRICE, 2)
    assert month.attributes["reported_until"] == "2026-08-02T10:00:00+08:00"
    assert costs_so_far(slots, view_today, now, None) == []


def test_projected_cost_adds_recent_daily_average_for_days_left():
    now = _sgt(2026, 8, 10, 12)
    slots = _slots(_sgt(2026, 8, 1), _sgt(2026, 8, 10, 11, 30))
    result = projected_cost(slots, now, PRICE)
    so_far = 9 * 24 + 23 * 0.5
    # Reported until 11:30 on the 10th: 21 days 12.5 h left at 24 kWh/day.
    projected = so_far + 24 * (21 + 12.5 / 24)
    assert result.attributes["month_to_date_kwh"] == so_far
    assert result.attributes["daily_average_kwh"] == 24.0
    assert result.attributes["projected_kwh"] == round(projected, 1)
    assert result.spec.native_value == round(projected * PRICE, 2)


def test_projected_cost_early_in_month_uses_last_months_days():
    now = _sgt(2026, 8, 1, 6)
    slots = _slots(_sgt(2026, 7, 25), _sgt(2026, 8, 1, 2))
    result = projected_cost(slots, now, PRICE)
    assert result.attributes["month_to_date_kwh"] == 2.0
    assert result.attributes["days_averaged"] == 7
    assert projected_cost(slots, now, None) is None
    assert projected_cost((), now, PRICE) is None


# ---------------------------------------------------------------------------
# Similar homes, Green Goals, bill status, meter reading window
# ---------------------------------------------------------------------------


def _series(unit: str, *rows: tuple[str, float, float | None, float | None]):
    periods = tuple(
        PeriodReading(
            start=datetime.fromisoformat(start),
            amount=amount,
            peer_average=average,
            peer_efficient=efficient,
        )
        for start, amount, average, efficient in rows
    )
    return UtilitySeries(
        total=sum(p.amount for p in periods),
        unit=unit,
        periods=periods,
        average=None,
        comparison=None,
    )


def test_vs_peers_uses_latest_compared_period():
    series = _series(
        UNIT_KWH,
        ("2026-06-01T00:00:00+08:00", 210.5, 200.0, 150.0),
        ("2026-07-01T00:00:00+08:00", 150.0, 200.0, 150.0),
        ("2026-08-01T00:00:00+08:00", 90.0, None, None),  # not compared yet
    )
    result = vs_peers("electricity_vs_peers", series)
    assert result.spec.native_value == -25
    assert result.spec.unit_of_measurement == "%"
    assert result.attributes == {
        "period": "2026-07-01T00:00:00+08:00",
        "usage": 150.0,
        "similar_homes_average": 200.0,
        "efficient_homes": 150.0,
        "unit": UNIT_KWH,
    }
    assert vs_peers("water_vs_peers", None) is None
    plain = _series(UNIT_M3, ("2026-07-01T00:00:00+08:00", 5.0, None, None))
    assert vs_peers("water_vs_peers", plain) is None


def test_peer_comparison_parsed_from_recorded_charts():
    usage = fixture_client().fetch_usage()
    newest = max(usage.electricity.periods, key=lambda p: p.start)
    assert (newest.peer_average, newest.peer_efficient) == (200.0, 150.0)
    water = vs_peers("water_vs_peers", usage.water)
    assert water.spec.native_value == round((6.59 - 14) / 14 * 100)


def _goal(used: float, target: float) -> GreenGoal:
    return GreenGoal(
        kind="elec",
        month="August 2026",
        used=used,
        target=target,
        percent_difference=None,
        cost_difference_sgd=None,
        unit=UNIT_KWH,
    )


@pytest.mark.parametrize(
    ("used", "on_track"),
    [(100.0, True), (200.0, False), (400.0, False)],
)
def test_goal_remaining_and_pace(used, on_track):
    # Day 11 of 31 at noon: ~34% of the month gone.
    now = _sgt(2026, 8, 11, 12)
    usage = _usage(green_goals=(_goal(used, 350.0),))
    result = goal_remaining("electricity_goal_remaining", usage, "elec", now)
    assert result.spec.native_value == round(350 - used, 2)
    assert result.spec.unit_of_measurement == UNIT_KWH
    assert result.attributes["on_track"] is on_track
    assert result.attributes["target"] == 350.0
    assert goal_remaining("water_goal_remaining", usage, "water", now) is None


def _billed(due: str, amount_due: float) -> UsageReadings:
    return _usage(
        bills=(
            BillInfo(
                amount_sgd=203.69,
                date="2026-08-03T16:00:00Z",
                period=None,
                due_date=due,
            ),
        ),
        amount_due=PayableInfo(
            amount_sgd=amount_due,
            currency="SGD",
            giro_enabled=False,
            recurring_enabled=False,
        ),
    )


def test_bill_status_paid_due_overdue():
    due = "2026-08-17T16:00:00Z"  # 18 Aug in Singapore
    status = bill_status(_billed(due, 203.69), _sgt(2026, 8, 2, 12))
    assert status.spec.native_value == "due"
    assert status.spec.device_class == "enum"
    assert status.attributes["due_date"] == "2026-08-18"
    assert status.attributes["days_until_due"] == 16
    assert (
        bill_status(_billed(due, 203.69), _sgt(2026, 8, 18, 23)).spec.native_value
        == "due"
    )
    overdue = bill_status(_billed(due, 203.69), _sgt(2026, 8, 19, 0, 1))
    assert overdue.spec.native_value == "overdue"
    assert overdue.attributes["days_until_due"] == -1
    paid = bill_status(_billed(due, 0.0), _sgt(2026, 8, 19))
    assert paid.spec.native_value == "paid"
    assert "due_date" not in paid.attributes
    assert bill_status(_usage(), _sgt(2026, 8, 2)) is None


@pytest.mark.parametrize(
    ("now", "state"),
    [
        (_sgt(2026, 8, 2), "upcoming"),
        (_sgt(2026, 8, 31), "open"),
        (_sgt(2026, 9, 3, 18), "closed"),
    ],
)
def test_meter_reading_window(now, state):
    usage = fixture_client().fetch_usage()  # window 30 Aug 06:00 – 3 Sep 17:00
    result = meter_reading_window(usage, now)
    assert result.spec.native_value == state
    assert result.attributes["period"] == "Sep 2026"
    assert result.attributes["opens"] == "2026-08-30T06:00:00+08:00"


def test_meter_reading_window_needs_both_dates():
    reading = MeterReadingInfo(message="x", title="Sep", start=None, end=None)
    assert meter_reading_window(_usage(meter_reading=reading), FIXED_NOW) is None


def test_fixture_insights_without_tariff():
    usage = fixture_client().fetch_usage()
    view = electricity_view(usage, FIXED_NOW)
    keys = {i.spec.key for i in insights(usage, view, FIXED_NOW, None)}
    assert keys == {
        "electricity_vs_peers",
        "water_vs_peers",
        "electricity_goal_remaining",
        "bill_status",
        "meter_reading_window",
    }
    assert insights(None, view, FIXED_NOW, None) == ()


# ---------------------------------------------------------------------------
# Coordinator + sensor platform
# ---------------------------------------------------------------------------


async def test_coordinator_serves_insights_and_reprices_on_tariff_change():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    coordinator.data = await coordinator._async_update_data()
    keys = {spec.key for spec in coordinator.sensor_specs}
    assert "bill_status" in keys
    assert "electricity_cost_today" not in keys  # no tariff yet

    status = coordinator.spec_for("bill_status")
    assert status.native_value == "due"
    assert coordinator.extra_attributes("bill_status")["amount_due"] == 203.69
    # Mapper keys still come from the mapper.
    assert coordinator.spec_for("electricity").native_value is not None

    calls = []
    coordinator.async_add_listener(lambda: calls.append(1))
    coordinator.tariff = MagicMock(data=MagicMock(electricity_price=30.0))
    coordinator.async_import_billed_history = MagicMock(side_effect=_noop)
    await coordinator.async_tariff_updated()
    assert calls == [1]
    cost = coordinator.spec_for("electricity_cost_today")
    assert cost.native_value == round(2.0 * 0.3, 2)  # fixture: 2 kWh today


async def _noop(*_args, **_kwargs):
    return None


async def test_enum_insight_sensor_gets_options():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    coordinator.data = await coordinator._async_update_data()
    sensor = SpAccountSensor(
        coordinator, "entry1", coordinator.spec_for("meter_reading_window")
    )
    assert sensor.entity_description.options == ["upcoming", "open", "closed"]
    assert sensor._attr_translation_key == "sp_meter_reading_window"
    assert sensor.native_value == "upcoming"
    assert sensor.extra_state_attributes["period"] == "Sep 2026"
    plain = SpAccountSensor(coordinator, "entry1", coordinator.spec_for("electricity"))
    assert plain.entity_description.options is None


# ---------------------------------------------------------------------------
# Peer statistics
# ---------------------------------------------------------------------------


def test_monthly_points_split_usage_and_peers():
    series = _series(
        UNIT_KWH,
        ("2026-07-01T00:00:00+08:00", 150.0, 200.0, 150.0),
        ("2026-06-01T00:00:00+08:00", 210.5, 190.0, None),
        ("2026-08-01T00:00:00+08:00", 90.0, None, None),
    )
    usage, average, efficient = monthly_points(series)
    assert [p.amount for p in usage] == [210.5, 150.0, 90.0]
    assert [p.amount for p in average] == [190.0, 200.0]
    assert [p.amount for p in efficient] == [150.0]
    assert usage[0].start == datetime.fromisoformat("2026-05-31T16:00:00+00:00")


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def test_calendar_events_from_fixture():
    usage = fixture_client().fetch_usage()
    events = sp_account_events(usage)
    assert [e.summary for e in events] == [
        "SP bill due (S$323.26)",
        "SP bill due (S$203.69)",
        "Submit SP meter reading (Sep 2026)",
    ]
    assert events[1].start == date(2026, 8, 18)
    assert events[1].end == date(2026, 8, 19)
    assert events[2].start == _sgt(2026, 8, 30, 6)
    assert events[2].end == _sgt(2026, 9, 3, 17)
    assert sp_account_events(None) == []


async def test_calendar_entity_added_with_data_and_queries_range():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    add = MagicMock()
    unsubs = async_setup_sp_account_calendars("entry1", {"sub1": coordinator}, add)
    assert len(unsubs) == 1
    add.assert_not_called()  # no data yet
    coordinator.data = await coordinator._async_update_data()
    coordinator.async_update_listeners()
    coordinator.async_update_listeners()
    add.assert_called_once()
    assert add.call_args.kwargs == {"config_subentry_id": "sub1"}
    calendar = add.call_args.args[0][0]
    assert isinstance(calendar, SpAccountCalendar)
    premise = coordinator.data.premise_id
    assert calendar.unique_id == f"entry1_sp_{premise}_calendar"
    assert calendar._attr_device_info["identifiers"] == {
        ("singapore", f"entry1_sp_{premise}")
    }

    with patch(
        "custom_components.singapore.sp_account_calendar.dt_util.now",
        return_value=FIXED_NOW,
    ):
        assert calendar.event.summary == "SP bill due (S$203.69)"
    august = await calendar.async_get_events(None, _sgt(2026, 8, 1), _sgt(2026, 9, 1))
    assert [e.summary for e in august] == [
        "SP bill due (S$203.69)",
        "Submit SP meter reading (Sep 2026)",
    ]
