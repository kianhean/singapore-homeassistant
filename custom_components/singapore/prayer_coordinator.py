"""Data coordinator for MUIS Muslim prayer times (data.gov.sg).

The *Muslim Prayer Timetable (consolidated)* dataset
(``d_a6a206cba471fe04b62dd886ef5eaf22``) holds one row per day with ``Date``
(``YYYY-MM-DD``) and the times of Subuh, Syuruk (sunrise), Zohor, Asar, Maghrib
and Isyak as ``HH:MM``. It is published once a year, so the coordinator asks the
CKAN ``datastore_search`` for the next few days only (``filters`` on ``Date``).

Rows before 2026 are in 12-hour time without am/pm (Zohor ``01:10``, Maghrib
``07:10``); from 2026 they are 24-hour. ``_parse_day`` reads both by making
each time later than the one before it in the day's order.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    PRAYER_SYURUK,
    PRAYER_TIMES,
    SOURCE_PRAYER,
    data_gov_headers,
    scaled_interval,
)
from .datagov import data_gov_get
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

PRAYER_DATASET = "d_a6a206cba471fe04b62dd886ef5eaf22"
PRAYER_URL = "https://data.gov.sg/api/action/datastore_search"
# Today plus the next two days, so "next prayer" after Isyak always has a row.
DAYS_AHEAD = 3
UPDATE_INTERVAL = timedelta(hours=12)

SGT = timezone(timedelta(hours=8))

# The prayers "next prayer" chooses from (Syuruk, sunrise, is not one).
PRAYERS: tuple[str, ...] = tuple(p for p in PRAYER_TIMES if p != PRAYER_SYURUK)

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})")


@dataclass(frozen=True, slots=True)
class PrayerDay:
    """One day's prayer times (Singapore time)."""

    day: date
    times: dict[str, datetime]


@dataclass
class PrayerData:
    """The prayer times of today and the next days, oldest first."""

    days: list[PrayerDay] = field(default_factory=list)

    def for_date(self, day: date) -> PrayerDay | None:
        return next((d for d in self.days if d.day == day), None)

    def today(self, now: datetime) -> PrayerDay | None:
        """The row for ``now``'s date in Singapore."""
        return self.for_date(now.astimezone(SGT).date())

    def next_prayer(self, now: datetime) -> tuple[str, datetime] | None:
        """The next prayer (not Syuruk) after ``now``, and its time."""
        for day in self.days:
            for key in PRAYERS:
                if (when := day.times.get(key)) is not None and when > now:
                    return key, when
        return None

    def next_change(self, now: datetime) -> datetime:
        """When a prayer sensor's state next changes: a time or midnight."""
        midnight = datetime.combine(
            now.astimezone(SGT).date() + timedelta(days=1), time(), SGT
        )
        upcoming = [
            when for day in self.days for when in day.times.values() if when > now
        ]
        return min([*upcoming, midnight])


def _parse_day(row: dict) -> PrayerDay | None:
    try:
        day = date.fromisoformat(str(row.get("Date") or "").strip())
    except ValueError:
        return None
    times: dict[str, datetime] = {}
    previous: datetime | None = None
    for key in PRAYER_TIMES:
        match = _TIME_RE.match(str(row.get(key.capitalize()) or ""))
        if match is None:
            continue
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            continue
        when = datetime.combine(day, time(hour, minute), SGT)
        # 12-hour rows (before 2026): a time not after the previous one is pm.
        while previous is not None and when <= previous:
            when += timedelta(hours=12)
        times[key] = when
        previous = when
    return PrayerDay(day, times) if times else None


def _parse_prayer_times(payload: object) -> PrayerData:
    result = payload.get("result") if isinstance(payload, dict) else None
    records = result.get("records") if isinstance(result, dict) else None
    if not isinstance(records, list):
        raise source_error(ERROR_PAYLOAD, SOURCE_PRAYER, detail="no records list")
    days = [
        parsed
        for row in records
        if isinstance(row, dict) and (parsed := _parse_day(row)) is not None
    ]
    if not days:
        # The timetable is published a year at a time; none means no new year
        # yet (or a changed dataset), which should show as a failed update.
        raise source_error(ERROR_PAYLOAD, SOURCE_PRAYER, detail="no timetable rows")
    days.sort(key=lambda d: d.day)
    return PrayerData(days)


class PrayerTimesCoordinator(DataUpdateCoordinator[PrayerData]):
    """Fetches the next few days of the MUIS prayer timetable."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore Prayer Times",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> PrayerData:
        today = dt_util.utcnow().astimezone(SGT).date()
        dates = [(today + timedelta(days=n)).isoformat() for n in range(DAYS_AHEAD)]
        params = {
            "resource_id": PRAYER_DATASET,
            "filters": json.dumps({"Date": dates}),
            "limit": str(DAYS_AHEAD * 2),
        }
        session = async_get_clientsession(self.hass)
        try:
            async with data_gov_get(
                self.hass,
                session,
                PRAYER_URL,
                params=params,
                headers=data_gov_headers(self.config_entry),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(
                        ERROR_HTTP, SOURCE_PRAYER, status=response.status
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching prayer times (%s); using last known values", err
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_PRAYER, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching prayer times (%s); using last known values", err
                )
                return self.data
            raise
        return _parse_prayer_times(payload)
