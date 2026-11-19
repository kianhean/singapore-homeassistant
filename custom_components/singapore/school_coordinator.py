"""Data coordinator for the MOE school calendar (terms, holidays, national exams)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    SCHOOL_EXAMS,
    SCHOOL_HOLIDAYS,
    SCHOOL_TERMS,
    SOURCE_SCHOOL,
    scaled_interval,
)
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error
from .holiday_coordinator import _HEADERS

_LOGGER = logging.getLogger(__name__)

SCHOOL_CALENDAR_URL = "https://www.moe.gov.sg/calendar"
UPDATE_INTERVAL = timedelta(hours=24)

# MOE's event type label -> our event kind. The page also lists public
# holidays (the MOM calendar has those) and GIRO deductions, which are skipped.
MOE_EVENT_TYPES: dict[str, str] = {
    "school holidays": SCHOOL_HOLIDAYS,
    "school terms": SCHOOL_TERMS,
    "national exams": SCHOOL_EXAMS,
}


@dataclass(frozen=True)
class SchoolEvent:
    """One MOE calendar entry; ``end`` is the last day (inclusive)."""

    name: str
    kind: str
    start: date
    end: date


class SchoolCalendarCoordinator(DataUpdateCoordinator[list[SchoolEvent]]):
    """Fetches the MOE school calendar page once a day."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore MOE School Calendar",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> list[SchoolEvent]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                SCHOOL_CALENDAR_URL,
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(
                        ERROR_HTTP, SOURCE_SCHOOL, status=response.status
                    )
                html = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching the MOE calendar (%s); using last known values", err
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_SCHOOL, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching the MOE calendar (%s); using last known values", err
                )
                return self.data
            raise

        # BeautifulSoup on a ~450 KB page is blocking: parse in the executor.
        return await self.hass.async_add_executor_job(_parse_school_calendar, html)


def _parse_date(value: str | None) -> date | None:
    if not value or value == "null":
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_school_calendar(html: str) -> list[SchoolEvent]:
    """Parse the event list on moe.gov.sg/calendar.

    Each event is a ``tr.fc-list-item`` row with ``data-start`` (and, for
    multi-day events, ``data-end``) as ISO dates, an ``.event-l-title`` and an
    ``.event-l-type`` label. Raises ``UpdateFailed`` when the page has no
    events at all, which means its markup changed.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr.fc-list-item")
    if not rows:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_SCHOOL, detail="No calendar events found on MOE page"
        )

    events: set[SchoolEvent] = set()
    for row in rows:
        type_el = row.select_one(".event-l-type")
        title_el = row.select_one(".event-l-title")
        if type_el is None or title_el is None:
            continue
        kind = MOE_EVENT_TYPES.get(type_el.get_text(" ", strip=True).lower())
        name = title_el.get_text(" ", strip=True)
        start = _parse_date(row.get("data-start"))
        if kind is None or not name or start is None:
            continue
        end = _parse_date(row.get("data-end")) or start
        events.add(SchoolEvent(name=name, kind=kind, start=start, end=max(end, start)))
    return sorted(events, key=lambda e: (e.start, e.end, e.name))
