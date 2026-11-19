"""Calendar platform: public holidays, MOE school calendars, hawker centre closures,
SP e-account dates."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SingaporeConfigEntry
from .const import SCHOOL_EVENT_KINDS, enabled_school_events
from .entity import (
    DEVICE_HAWKER,
    DEVICE_HOLIDAY,
    DEVICE_SCHOOL,
    SingaporeEntity,
    school_translation_key,
)
from .hawker_coordinator import (
    CLOSURE_CLEANING,
    NEAREST_CENTRES,
    HawkerCentre,
    HawkerClosure,
    HawkerClosureCoordinator,
)
from .holiday_coordinator import PublicHoliday, PublicHolidayCoordinator
from .school_coordinator import SchoolCalendarCoordinator, SchoolEvent
from .sp_account_calendar import async_setup_sp_account_calendars

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the public holiday and school calendars, and each SP account's."""
    entities: list[CalendarEntity] = []
    coordinator = entry.runtime_data.holiday
    if coordinator is not None:  # None: public holidays turned off in the options.
        entities.append(SingaporePublicHolidayCalendar(coordinator, entry.entry_id))
    if (school := entry.runtime_data.school) is not None:
        # One calendar per kind the user kept ticked in the options.
        chosen = enabled_school_events(entry)
        entities += [
            SingaporeSchoolCalendar(school, entry.entry_id, kind)
            for kind in SCHOOL_EVENT_KINDS
            if kind in chosen
        ]
    if (hawker := entry.runtime_data.hawker) is not None:
        entities.append(SingaporeHawkerClosureCalendar(hawker, entry.entry_id))
    async_add_entities(entities)
    for unsub in async_setup_sp_account_calendars(
        entry.entry_id, entry.runtime_data.sp_accounts, async_add_entities
    ):
        entry.async_on_unload(unsub)


class SingaporePublicHolidayCalendar(
    SingaporeEntity[PublicHolidayCoordinator], CalendarEntity
):
    """Single calendar containing Singapore public holidays."""

    _device = DEVICE_HOLIDAY
    _attr_name = None
    _attr_translation_key = "public_holidays"
    _attr_attribution = "Data provided by the Ministry of Manpower"

    def __init__(self, coordinator: PublicHolidayCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "public_holidays")

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming holiday."""
        if self.coordinator.data is None:
            return None

        today = dt_util.now().date()
        for holiday in self.coordinator.data:
            if holiday.day >= today:
                return _to_event(holiday)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return holidays between start and end."""
        if self.coordinator.data is None:
            return []

        start = start_date.date()
        end = end_date.date()

        return [
            _to_event(holiday)
            for holiday in self.coordinator.data
            if start <= holiday.day < end
        ]

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {"source": "MOM", "events": 0}
        return {
            "source": "MOM",
            "events": len(self.coordinator.data),
            "url": "https://www.mom.gov.sg/employment-practices/public-holidays",
        }


def _to_event(holiday: PublicHoliday) -> CalendarEvent:
    return CalendarEvent(
        summary=holiday.name,
        start=holiday.day,
        end=holiday.day + timedelta(days=1),
        description="Singapore public holiday",
    )


class SingaporeSchoolCalendar(
    SingaporeEntity[SchoolCalendarCoordinator], CalendarEntity
):
    """MOE school holidays, terms or national exams (one calendar per kind).

    MOE lists most dates per level ("MK, Primary & Secondary", "Post-secondary"),
    so events can overlap; the level is part of each event's title.
    """

    _device = DEVICE_SCHOOL
    _attr_attribution = "Data provided by the Ministry of Education"

    def __init__(
        self, coordinator: SchoolCalendarCoordinator, entry_id: str, kind: str
    ) -> None:
        key = school_translation_key(kind)
        super().__init__(coordinator, entry_id, key)
        self._kind = kind
        self._attr_translation_key = key

    def _events(self) -> list[SchoolEvent]:
        if self.coordinator.data is None:
            return []
        return [e for e in self.coordinator.data if e.kind == self._kind]

    @property
    def event(self) -> CalendarEvent | None:
        """The event in progress today, else the next one."""
        today = dt_util.now().date()
        for event in self._events():  # sorted by start date
            if event.end >= today:
                return _school_event(event)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Events overlapping [start, end)."""
        start, end = start_date.date(), end_date.date()
        return [
            _school_event(event)
            for event in self._events()
            if event.start < end and event.end >= start
        ]

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "source": "MOE",
            "events": len(self._events()),
            "url": "https://www.moe.gov.sg/calendar",
        }


def _school_event(event: SchoolEvent) -> CalendarEvent:
    # All-day events: HA's end date is exclusive, MOE's is the last day.
    return CalendarEvent(
        summary=event.name,
        start=event.start,
        end=event.end + timedelta(days=1),
        description="MOE school calendar",
    )


class SingaporeHawkerClosureCalendar(
    SingaporeEntity[HawkerClosureCoordinator], CalendarEntity
):
    """Closures (quarterly cleaning, other works) of the hawker centres nearest home."""

    _device = DEVICE_HAWKER
    _attr_translation_key = "hawker_closures"
    _attr_attribution = "Data provided by NEA via data.gov.sg"

    def __init__(self, coordinator: HawkerClosureCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "hawker_closures")

    def _centres(self) -> list[HawkerCentre]:
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.nearest(
            self.hass.config.latitude, self.hass.config.longitude, NEAREST_CENTRES
        )

    def _closures(self) -> list[tuple[HawkerCentre, HawkerClosure]]:
        pairs = [(c, closure) for c in self._centres() for closure in c.closures]
        pairs.sort(key=lambda pair: (pair[1].start, pair[1].end, pair[0].name))
        return pairs

    @property
    def event(self) -> CalendarEvent | None:
        """The closure in progress today, else the next one."""
        today = dt_util.now().date()
        for centre, closure in self._closures():
            if closure.end >= today:
                return _hawker_event(centre, closure)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Closures overlapping [start, end)."""
        start, end = start_date.date(), end_date.date()
        return [
            _hawker_event(centre, closure)
            for centre, closure in self._closures()
            if closure.start < end and closure.end >= start
        ]

    @property
    def extra_state_attributes(self) -> dict:
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        return {
            "source": "NEA via data.gov.sg",
            "centres": [
                {
                    "name": centre.name,
                    "address": centre.address,
                    "distance_km": round(centre.distance_km(lat, lon) or 0.0, 2),
                }
                for centre in self._centres()
            ],
        }


def _hawker_event(centre: HawkerCentre, closure: HawkerClosure) -> CalendarEvent:
    reason = "cleaning" if closure.kind == CLOSURE_CLEANING else "other works"
    description = f"Closed for {reason}."
    if closure.remarks:
        description += f" {closure.remarks}"
    # All-day events: HA's end date is exclusive, NEA's is the last closed day.
    return CalendarEvent(
        summary=f"{centre.name} closed ({reason})",
        start=closure.start,
        end=closure.end + timedelta(days=1),
        description=description,
        location=centre.address,
    )
