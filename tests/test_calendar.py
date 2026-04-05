"""Tests for Singapore public holiday calendar entity."""

from datetime import date, datetime
from unittest.mock import MagicMock

from custom_components.singapore.calendar import SingaporePublicHolidayCalendar
from custom_components.singapore.holiday_coordinator import PublicHoliday


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_calendar_event_and_attributes():
    holidays = [
        PublicHoliday(name="Future Holiday", day=date(2099, 1, 1)),
    ]
    entity = SingaporePublicHolidayCalendar(_coordinator(holidays), "entry1")

    event = entity.event
    assert event["summary"] == "Future Holiday"
    assert event["start"].isoformat() == "2099-01-01"
    assert event["end"].isoformat() == "2099-01-02"

    attrs = entity.extra_state_attributes
    assert attrs["source"] == "MOM"
    assert attrs["events"] == 1


async def test_calendar_async_get_events_range_filter():
    holidays = [
        PublicHoliday(name="Holiday A", day=date(2026, 1, 1)),
        PublicHoliday(name="Holiday B", day=date(2026, 12, 25)),
    ]
    entity = SingaporePublicHolidayCalendar(_coordinator(holidays), "entry1")

    events = await entity.async_get_events(
        MagicMock(),
        datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
    )

    assert len(events) == 1
    assert events[0]["summary"] == "Holiday A"
