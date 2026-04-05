"""Calendar platform for Singapore public holidays."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .holiday_coordinator import PublicHoliday, PublicHolidayCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up public holiday calendar."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PublicHolidayCoordinator = entry_data["holiday"]
    async_add_entities([SingaporePublicHolidayCalendar(coordinator, entry.entry_id)])


class SingaporePublicHolidayCalendar(
    CoordinatorEntity[PublicHolidayCoordinator], CalendarEntity
):
    """Single calendar containing Singapore public holidays."""

    _attr_has_entity_name = False
    _attr_name = "Singapore Public Holidays"
    _attr_icon = "mdi:calendar"

    def __init__(self, coordinator: PublicHolidayCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_public_holidays"

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming holiday."""
        if self.coordinator.data is None:
            return None

        today = date.today()
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

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_holiday")},
            "name": "Singapore Public Holidays",
            "manufacturer": "Singapore",
            "model": "MOM Public Holidays",
        }


def _to_event(holiday: PublicHoliday) -> CalendarEvent:
    return CalendarEvent(
        summary=holiday.name,
        start=holiday.day,
        end=holiday.day + timedelta(days=1),
        description="Singapore public holiday",
    )
