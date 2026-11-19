"""Calendar platform: Singapore public holidays, plus SP e-account dates."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SingaporeConfigEntry
from .entity import DEVICE_HOLIDAY, SingaporeEntity
from .holiday_coordinator import PublicHoliday, PublicHolidayCoordinator
from .sp_account_calendar import async_setup_sp_account_calendars

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the public holiday calendar and each SP account's calendar."""
    coordinator = entry.runtime_data.holiday
    if coordinator is not None:  # None: public holidays turned off in the options.
        async_add_entities(
            [SingaporePublicHolidayCalendar(coordinator, entry.entry_id)]
        )
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
