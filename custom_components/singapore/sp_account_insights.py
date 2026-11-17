"""Derived SP e-account sensors: what the readings mean, not just what they are.

The vendored mapper turns each Jarvis reading into a sensor. This module adds
figures worked out from the same poll, with no extra API calls:

- **Always-on power**: the lowest half-hour of each recent full day, as watts
  (the fridge, routers and standby load that never switch off), the median
  over the last week so one holiday does not set it.
- **Electricity yesterday** and **its cost**: smart-meter (AMI) data lags SP
  by hours, so yesterday is the first complete day.
- **Projected electricity cost**: this month so far plus the recent daily
  average for the days left, priced at the scraped SP Group tariff.
- **Electricity cost today / this month**: AMI kWh priced at that tariff.
- **Electricity / water vs similar homes**: the latest billed period against
  SP's peer average for comparable homes, in percent.
- **Electricity / water goal remaining**: Green Goals target minus usage so
  far, and whether this month's pace stays under it.
- **Bill status**: paid / due / overdue, from the amount due and the latest
  bill's due date (payments are not read).
- **Meter reading window**: upcoming / open / closed, for homes that submit
  their own readings.

No Home Assistant imports, like ``sp_account/``, so it is unit-tested directly.
"""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .sp_account.const import (
    DEVICE_CLASS_MONETARY,
    GOAL_KIND_ELECTRICITY,
    GOAL_KIND_WATER,
    STATE_CLASS_MEASUREMENT,
    UNIT_KWH,
    UNIT_SGD,
)
from .sp_account.mapper import ElectricityView, SensorSpec
from .sp_account.models import SG_TZ, PeriodReading, UsageReadings, UtilitySeries

KEY_ALWAYS_ON = "electricity_always_on"
KEY_YESTERDAY = "electricity_yesterday"
KEY_YESTERDAY_COST = "electricity_cost_yesterday"
KEY_PROJECTED_COST = "electricity_projected_cost"
KEY_ELECTRICITY_VS_PEERS = "electricity_vs_peers"
KEY_WATER_VS_PEERS = "water_vs_peers"
KEY_COST_TODAY = "electricity_cost_today"
KEY_COST_MONTH = "electricity_cost_month"
KEY_ELECTRICITY_GOAL_REMAINING = "electricity_goal_remaining"
KEY_WATER_GOAL_REMAINING = "water_goal_remaining"
KEY_BILL_STATUS = "bill_status"
KEY_METER_READING_WINDOW = "meter_reading_window"

INSIGHT_KEYS = (
    KEY_ALWAYS_ON,
    KEY_YESTERDAY,
    KEY_YESTERDAY_COST,
    KEY_PROJECTED_COST,
    KEY_COST_TODAY,
    KEY_COST_MONTH,
    KEY_ELECTRICITY_VS_PEERS,
    KEY_WATER_VS_PEERS,
    KEY_ELECTRICITY_GOAL_REMAINING,
    KEY_WATER_GOAL_REMAINING,
    KEY_BILL_STATUS,
    KEY_METER_READING_WINDOW,
)

BILL_STATUS_OPTIONS = ["paid", "due", "overdue"]
METER_READING_WINDOW_OPTIONS = ["upcoming", "open", "closed"]
# Enum sensors and their states (SensorSpec has no options field).
INSIGHT_OPTIONS = {
    KEY_BILL_STATUS: BILL_STATUS_OPTIONS,
    KEY_METER_READING_WINDOW: METER_READING_WINDOW_OPTIONS,
}
DEVICE_CLASS_ENUM = "enum"

SLOT = timedelta(minutes=30)
SLOTS_PER_DAY = 48
# A day missing a few slots (an AMI gap) still counts; one missing hours does
# not, or its lowest slot and its total would both read low.
MIN_SLOTS_FOR_FULL_DAY = 44
# Full days the always-on load and the projected daily average look back over.
RECENT_DAYS = 7
HOURS_PER_YEAR = 8766  # 365.25 days

DEVICE_CLASS_POWER = "power"
UNIT_WATT = "W"
UNIT_PERCENT = "%"


@dataclass(frozen=True)
class Insight:
    """One derived sensor: its spec plus the attributes that explain it."""

    spec: SensorSpec
    attributes: dict[str, object]


def _spec(
    key: str,
    value: float | str,
    *,
    unit: str | None,
    precision: int | None,
    device_class: str | None = None,
    state_class: str | None = STATE_CLASS_MEASUREMENT,
) -> SensorSpec:
    return SensorSpec(
        key=key,
        native_value=value,
        device_class=device_class,
        state_class=state_class,
        unit_of_measurement=unit,
        suggested_display_precision=precision,
    )


def _by_day(slots: tuple[PeriodReading, ...]) -> dict[date, list[PeriodReading]]:
    days: dict[date, list[PeriodReading]] = defaultdict(list)
    for slot in slots:
        days[slot.start.astimezone(SG_TZ).date()].append(slot)
    return days


def _full_days(
    days: dict[date, list[PeriodReading]], today: date
) -> list[tuple[date, list[PeriodReading]]]:
    """The most recent complete days before today, newest first."""
    full = [
        (day, slots)
        for day, slots in days.items()
        if day < today and len(slots) >= MIN_SLOTS_FOR_FULL_DAY
    ]
    full.sort(key=lambda item: item[0], reverse=True)
    return full[:RECENT_DAYS]


def always_on(
    slots: tuple[PeriodReading, ...], now: datetime, price: float | None
) -> Insight | None:
    """Median over recent full days of each day's lowest half-hour, in watts."""
    recent = _full_days(_by_day(slots), now.astimezone(SG_TZ).date())
    if not recent:
        return None
    lows = [min(slot.amount for slot in day_slots) for _, day_slots in recent]
    # kWh in half an hour -> average kW over it -> W.
    watts = round(statistics.median(lows) * 2 * 1000, 1)
    yearly_kwh = round(watts * HOURS_PER_YEAR / 1000, 1)
    attrs: dict[str, object] = {
        "days": len(recent),
        "estimated_yearly_kwh": yearly_kwh,
    }
    if price is not None:
        attrs["estimated_yearly_cost"] = round(yearly_kwh * price, 2)
    return Insight(
        _spec(
            KEY_ALWAYS_ON,
            watts,
            unit=UNIT_WATT,
            precision=0,
            device_class=DEVICE_CLASS_POWER,
        ),
        attrs,
    )


def yesterday(
    slots: tuple[PeriodReading, ...], now: datetime, price: float | None
) -> list[Insight]:
    """Yesterday's kWh (and cost), once its last half-hour has been reported."""
    if not slots:
        return []
    today = now.astimezone(SG_TZ).date()
    day = today - timedelta(days=1)
    last_slot_start = datetime.combine(today, datetime.min.time(), SG_TZ) - SLOT
    if slots[-1].start < last_slot_start:
        return []  # AMI has not reached the end of yesterday yet
    day_slots = _by_day(slots).get(day, [])
    if len(day_slots) < MIN_SLOTS_FOR_FULL_DAY:
        return []
    kwh = round(sum(slot.amount for slot in day_slots), 3)
    peak = max(day_slots, key=lambda slot: slot.amount)
    out = [
        Insight(
            _spec(KEY_YESTERDAY, kwh, unit=UNIT_KWH, precision=2),
            {
                "date": day.isoformat(),
                "peak_half_hour": peak.start.isoformat(),
                "peak_half_hour_kwh": peak.amount,
            },
        )
    ]
    if price is not None:
        out.append(
            Insight(
                _spec(
                    KEY_YESTERDAY_COST,
                    round(kwh * price, 2),
                    unit=UNIT_SGD,
                    precision=2,
                    device_class=DEVICE_CLASS_MONETARY,
                    state_class=None,
                ),
                {"date": day.isoformat(), "price_per_kwh": price},
            )
        )
    return out


def projected_cost(
    slots: tuple[PeriodReading, ...], now: datetime, price: float | None
) -> Insight | None:
    """This month so far plus the recent daily average for the days left."""
    if price is None or not slots:
        return None
    local_now = now.astimezone(SG_TZ)
    recent = _full_days(_by_day(slots), local_now.date())
    if not recent:
        return None
    daily = statistics.mean(
        sum(slot.amount for slot in day_slots) for _, day_slots in recent
    )
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(local_now.year, local_now.month)[1]
    month_end = month_start + timedelta(days=days_in_month)
    so_far = sum(slot.amount for slot in slots if slot.start >= month_start)
    # Reported up to the end of the newest slot; the rest of the month is
    # estimated, including the part of today AMI has not delivered yet.
    reported_until = max(slots[-1].start + SLOT, month_start)
    days_left = max((month_end - reported_until).total_seconds(), 0) / 86400
    projected_kwh = so_far + daily * days_left
    return Insight(
        _spec(
            KEY_PROJECTED_COST,
            round(projected_kwh * price, 2),
            unit=UNIT_SGD,
            precision=2,
            device_class=DEVICE_CLASS_MONETARY,
            state_class=None,
        ),
        {
            "projected_kwh": round(projected_kwh, 1),
            "month_to_date_kwh": round(so_far, 2),
            "month_to_date_cost": round(so_far * price, 2),
            "daily_average_kwh": round(daily, 2),
            "days_averaged": len(recent),
            "price_per_kwh": price,
        },
    )


def _cost(key: str, kwh: float, price: float, attrs: dict[str, object]) -> Insight:
    return Insight(
        _spec(
            key,
            round(kwh * price, 2),
            unit=UNIT_SGD,
            precision=2,
            device_class=DEVICE_CLASS_MONETARY,
            state_class=None,
        ),
        {"kwh": round(kwh, 3), "price_per_kwh": price, **attrs},
    )


def costs_so_far(
    slots: tuple[PeriodReading, ...],
    today_kwh: float | None,
    now: datetime,
    price: float | None,
) -> list[Insight]:
    """Electricity cost today and this month so far, at the scraped tariff."""
    if price is None or not slots:
        return []
    local_now = now.astimezone(SG_TZ)
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_kwh = sum(slot.amount for slot in slots if slot.start >= month_start)
    reported = {"reported_until": (slots[-1].start + SLOT).isoformat()}
    out = [_cost(KEY_COST_MONTH, month_kwh, price, reported)]
    if today_kwh is not None:
        out.insert(0, _cost(KEY_COST_TODAY, today_kwh, price, reported))
    return out


def _month_elapsed(now: datetime) -> float:
    """How much of the current SGT month has passed, 0 to 1."""
    local_now = now.astimezone(SG_TZ)
    days_in_month = calendar.monthrange(local_now.year, local_now.month)[1]
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elapsed = (local_now - month_start).total_seconds() / 86400
    return min(max(elapsed / days_in_month, 0.0), 1.0)


def goal_remaining(
    key: str, usage: UsageReadings, kind: str, now: datetime
) -> Insight | None:
    """Green Goals target minus usage so far; on track if the pace fits."""
    goal = usage.goal(kind)
    if goal is None or not goal.target:
        return None
    elapsed = _month_elapsed(now)
    projected = goal.used / elapsed if elapsed > 0 else None
    attrs: dict[str, object] = {
        "month": goal.month,
        "target": goal.target,
        "used": goal.used,
        "percent_used": round(goal.used / goal.target * 100),
        "on_track": goal.used <= goal.target
        and (projected is None or projected <= goal.target),
    }
    if projected is not None:
        attrs["projected"] = round(projected, 1)
    return Insight(
        _spec(
            key,
            round(goal.target - goal.used, 2),
            unit=goal.unit,
            precision=1,
        ),
        {k: v for k, v in attrs.items() if v is not None},
    )


def parse_instant(value: str | None) -> datetime | None:
    """An SP timestamp (``Z`` or ``+08:00``) as an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SG_TZ)


def bill_due_date(usage: UsageReadings) -> date | None:
    """The latest bill's due date, as the SGT calendar day."""
    bill = usage.last_bill
    due = parse_instant(bill.due_date) if bill is not None else None
    return due.astimezone(SG_TZ).date() if due is not None else None


def bill_status(usage: UsageReadings, now: datetime) -> Insight | None:
    """Paid when nothing is owed, else due or overdue by the bill's due date."""
    due = usage.amount_due
    if due is None:
        return None
    due_date = bill_due_date(usage)
    today = now.astimezone(SG_TZ).date()
    attrs: dict[str, object] = {"amount_due": due.amount_sgd}
    if due.amount_sgd <= 0:
        state = "paid"
    elif due_date is not None and today > due_date:
        state = "overdue"
    else:
        state = "due"
    if due_date is not None and state != "paid":
        attrs["due_date"] = due_date.isoformat()
        attrs["days_until_due"] = (due_date - today).days
    if due.giro_enabled is not None:
        attrs["giro_enabled"] = due.giro_enabled
    return Insight(
        _spec(
            KEY_BILL_STATUS,
            state,
            unit=None,
            precision=None,
            device_class=DEVICE_CLASS_ENUM,
            state_class=None,
        ),
        attrs,
    )


def meter_reading_window(usage: UsageReadings, now: datetime) -> Insight | None:
    """Whether the self-submitted meter reading window is upcoming/open/closed."""
    reading = usage.meter_reading
    if reading is None:
        return None
    opens, closes = parse_instant(reading.start), parse_instant(reading.end)
    if opens is None or closes is None:
        return None
    if now < opens:
        state = "upcoming"
    elif now <= closes:
        state = "open"
    else:
        state = "closed"
    attrs: dict[str, object] = {
        "period": reading.title,
        "opens": opens.isoformat(),
        "closes": closes.isoformat(),
        "message": reading.message,
    }
    return Insight(
        _spec(
            KEY_METER_READING_WINDOW,
            state,
            unit=None,
            precision=None,
            device_class=DEVICE_CLASS_ENUM,
            state_class=None,
        ),
        {k: v for k, v in attrs.items() if v is not None},
    )


def latest_peer_period(series: UtilitySeries | None) -> PeriodReading | None:
    """The newest billed period that SP compared with similar homes."""
    if series is None:
        return None
    compared = [p for p in series.periods if p.peer_average]
    return max(compared, key=lambda p: p.start) if compared else None


def vs_peers(key: str, series: UtilitySeries | None) -> Insight | None:
    """Latest billed period against similar homes' average, in percent."""
    period = latest_peer_period(series)
    if period is None or series is None or not period.peer_average:
        return None
    percent = round((period.amount - period.peer_average) / period.peer_average * 100)
    attrs: dict[str, object] = {
        "period": period.start.isoformat(),
        "usage": period.amount,
        "similar_homes_average": period.peer_average,
        "unit": series.unit,
    }
    if period.peer_efficient is not None:
        attrs["efficient_homes"] = period.peer_efficient
    return Insight(_spec(key, percent, unit=UNIT_PERCENT, precision=0), attrs)


def insights(
    usage: UsageReadings | None,
    view: ElectricityView,
    now: datetime,
    price: float | None,
) -> tuple[Insight, ...]:
    """Every derived sensor this poll's readings support.

    ``price`` is SGD/kWh (the scraped tariff); without it the cost sensors are
    left out rather than reported as zero.
    """
    if usage is None:
        return ()
    slots = view.slots
    found = [
        always_on(slots, now, price),
        *yesterday(slots, now, price),
        projected_cost(slots, now, price),
        *costs_so_far(slots, view.today_kwh, now, price),
        vs_peers(KEY_ELECTRICITY_VS_PEERS, usage.electricity),
        vs_peers(KEY_WATER_VS_PEERS, usage.water),
        goal_remaining(
            KEY_ELECTRICITY_GOAL_REMAINING, usage, GOAL_KIND_ELECTRICITY, now
        ),
        goal_remaining(KEY_WATER_GOAL_REMAINING, usage, GOAL_KIND_WATER, now),
        bill_status(usage, now),
        meter_reading_window(usage, now),
    ]
    return tuple(item for item in found if item is not None)
