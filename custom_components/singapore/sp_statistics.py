"""Import SP Services usage history into Home Assistant long-term statistics.

SP publishes consumption per period (half-hourly electricity, monthly water)
rather than meter readings, so each import continues the running ``sum`` from
the last imported hour. Only periods after that hour are added, which also
backfills any gap left while the account was logged out.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from sp_services import UsageData, UsagePoint

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SP_TIMEZONE: Final = ZoneInfo("Asia/Singapore")

_DATE_FORMATS: Final = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d")
# Month-only labels SP uses in some exports (e.g. "Apr 2026", "Apr-26"); they
# parse to the first of the month.
_MONTH_FORMATS: Final = (
    "%b %Y",
    "%B %Y",
    "%b-%y",
    "%b-%Y",
    "%b %y",
    "%Y-%m",
    "%Y/%m",
    "%m/%Y",
    "%m-%Y",
    "%Y%m",
)
_DATETIME_FORMATS: Final = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%S",
)


def parse_period_day(period: str) -> date | None:
    """Parse a day label (ISO or day-first, optionally with a time)."""
    head = period.strip().strip('"').split(" ")[0].split("T")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def parse_period_date(period: str) -> date | None:
    """Parse a day or month-only label; a month gives its first day."""
    if (day := parse_period_day(period)) is not None:
        return day
    text = period.strip().strip('"')
    for fmt in _MONTH_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_period_datetime(period: str) -> datetime | None:
    """Parse a half-hourly SP period label as a Singapore-local datetime."""
    text = period.strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=SP_TIMEZONE)
        except ValueError:
            continue
    return None


def electricity_statistic_id(account_no: str) -> str:
    return f"{DOMAIN}:sp_{account_no}_electricity"


def water_statistic_id(account_no: str) -> str:
    return f"{DOMAIN}:sp_{account_no}_water"


def hourly_electricity(points: list[UsagePoint] | None) -> list[tuple[datetime, float]]:
    """Sum half-hourly points into hourly buckets (UTC starts).

    The latest bucket is left out unless both half-hours are in, because an
    hour is never re-imported once its start is behind the last statistic.
    """
    buckets: dict[datetime, list[float]] = {}
    for point in points or []:
        if point.value is None:
            continue
        local = parse_period_datetime(point.period)
        if local is None:
            continue
        hour = local.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
        buckets.setdefault(hour, []).append(point.value)

    hours = sorted(buckets)
    if hours and len(buckets[hours[-1]]) < 2:
        hours.pop()
    return [(hour, round(sum(buckets[hour]), 3)) for hour in hours]


def monthly_water(
    points: list[UsagePoint] | None, now: datetime
) -> list[tuple[datetime, float]]:
    """Place each completed month's water usage at the month's first hour (UTC).

    The in-progress month is skipped: SP may still be adding to it.
    """
    current = now.astimezone(SP_TIMEZONE)
    months: dict[datetime, float] = {}
    for point in points or []:
        if point.value is None:
            continue
        day = parse_period_date(point.period)
        if day is None or (day.year, day.month) >= (current.year, current.month):
            continue
        start = datetime(day.year, day.month, 1, tzinfo=SP_TIMEZONE)
        months[start.astimezone(UTC)] = point.value
    return sorted(months.items())


async def async_import_usage_statistics(
    hass: HomeAssistant, account_no: str, usage: UsageData
) -> None:
    """Add any new SP usage periods to the account's external statistics."""
    if "recorder" not in hass.config.components:
        return
    await _async_import(
        hass,
        statistic_id=electricity_statistic_id(account_no),
        name=f"SP Services {account_no} electricity",
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        points=hourly_electricity(usage.electricity_hourly_history),
    )
    await _async_import(
        hass,
        statistic_id=water_statistic_id(account_no),
        name=f"SP Services {account_no} water",
        unit=UnitOfVolume.CUBIC_METERS,
        points=monthly_water(usage.water_monthly_history, datetime.now(UTC)),
    )


async def _async_import(
    hass: HomeAssistant,
    *,
    statistic_id: str,
    name: str,
    unit: str,
    points: list[tuple[datetime, float]],
) -> None:
    if not points:
        return

    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    last_start: datetime | None = None
    total = 0.0
    if rows := last.get(statistic_id):
        start = rows[0]["start"]
        last_start = (
            datetime.fromtimestamp(start, UTC)
            if isinstance(start, (int, float))
            else start
        )
        total = float(rows[0].get("sum") or 0.0)

    statistics: list[StatisticData] = []
    for start, value in points:
        if last_start is not None and start <= last_start:
            continue
        total = round(total + value, 3)
        statistics.append(StatisticData(start=start, state=value, sum=total))

    if not statistics:
        return

    _LOGGER.debug("Importing %d %s statistics", len(statistics), statistic_id)
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
    )
    async_add_external_statistics(hass, metadata, statistics)
