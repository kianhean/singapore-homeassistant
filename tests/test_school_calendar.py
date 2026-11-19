"""MOE school calendar: parser, coordinator, calendar entities, options and cleanup."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore import async_remove_stale
from custom_components.singapore.calendar import (
    SingaporeSchoolCalendar,
)
from custom_components.singapore.calendar import (
    async_setup_entry as calendar_setup_entry,
)
from custom_components.singapore.config_flow import (
    SingaporeOptionsFlow,
    options_from_input,
)
from custom_components.singapore.const import (
    CONF_DISABLED_SCHOOL_EVENTS,
    SOURCES,
    enabled_school_events,
)
from custom_components.singapore.school_coordinator import (
    SchoolCalendarCoordinator,
    SchoolEvent,
    _parse_school_calendar,
    is_bot_check,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "moe_calendar.html").read_text()


def test_parse_keeps_terms_holidays_and_exams_only():
    events = _parse_school_calendar(FIXTURE)
    assert [(e.kind, e.name) for e in events] == [
        ("terms", "Term 1 (MK, Primary & Secondary)"),
        ("holidays", "Term 1 school holidays (MK, Primary & Secondary)"),
        ("holidays", "Day off-in-lieu for Hari Raya Puasa"),
        ("exams", "PSLE--Written"),
        ("holidays", "Term 4 school holidays (MK, Primary & Secondary)"),
    ]
    # Public holidays (MOM has them) and GIRO deductions are skipped.
    assert not any("GIRO" in e.name or e.name == "New Year's Day" for e in events)
    term = events[0]
    assert (term.start, term.end) == (date(2026, 1, 2), date(2026, 3, 13))
    # A one-day event has no data-end: it ends the day it starts.
    day_off = events[2]
    assert day_off.start == day_off.end == date(2026, 3, 23)


def test_parse_without_events_raises():
    with pytest.raises(UpdateFailed, match="No calendar events found"):
        _parse_school_calendar("<html><body>Maintenance</body></html>")


def test_parse_skips_rows_with_missing_parts():
    html = (
        "<table>"
        '<tr class="fc-list-item" data-start="2026-01-02">'
        '<h2 class="event-l-title">No type</h2></tr>'
        '<tr class="fc-list-item" data-start="not-a-date">'
        '<h2 class="event-l-title">Bad date</h2>'
        '<div class="event-l-type">School terms</div></tr>'
        '<tr class="fc-list-item" data-start="2026-05-01" data-end="2026-04-01">'
        '<h2 class="event-l-title">Backwards</h2>'
        '<div class="event-l-type">National exams</div></tr>'
        "</table>"
    )
    events = _parse_school_calendar(html)
    assert events == [
        SchoolEvent("Backwards", "exams", date(2026, 5, 1), date(2026, 5, 1))
    ]


def _session(status: int = 200, text: str = FIXTURE, error: Exception | None = None):
    response = AsyncMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = (
        MagicMock(side_effect=error) if error else MagicMock(return_value=response)
    )
    return session


async def _refresh(session, data=None) -> SchoolCalendarCoordinator:
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *a: func(*a))
    coordinator = SchoolCalendarCoordinator(hass)
    coordinator.data = data
    with patch(
        "custom_components.singapore.school_coordinator.async_get_clientsession",
        return_value=session,
    ):
        await coordinator.async_refresh()
    return coordinator


async def test_coordinator_fetches_and_parses():
    coordinator = await _refresh(_session())
    assert coordinator.last_update_success is True
    assert len(coordinator.data) == 5


@pytest.mark.parametrize(
    "session",
    [_session(status=503), _session(error=aiohttp.ClientError("down"))],
)
async def test_coordinator_errors_fail_without_data(session):
    coordinator = await _refresh(session)
    assert coordinator.last_update_success is False
    assert coordinator.last_exception.translation_kwargs["translation_key"] in {
        "http_error",
        "fetch_failed",
    }


@pytest.mark.parametrize(
    "session",
    [_session(status=503), _session(error=TimeoutError())],
)
async def test_coordinator_errors_keep_last_known_data(session):
    last = [SchoolEvent("Term 1", "terms", date(2026, 1, 2), date(2026, 3, 13))]
    coordinator = await _refresh(session, data=last)
    assert coordinator.last_update_success is True
    assert coordinator.data == last


# The page MOE served to GitHub Actions (2026-11-19), shortened.
BOT_CHECK = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"></head>
<body><p>Please wait while we verify you are not a bot.</p>
<noscript>This requires JavaScript. Enable JavaScript and then reload the page.
</noscript></body></html>"""


def test_is_bot_check():
    assert is_bot_check(BOT_CHECK)
    assert not is_bot_check(FIXTURE)
    assert not is_bot_check("<html><body>Maintenance</body></html>")


async def test_bot_check_keeps_last_known_data():
    last = [SchoolEvent("Term 1", "terms", date(2026, 1, 2), date(2026, 3, 13))]
    coordinator = await _refresh(_session(text=BOT_CHECK), data=last)
    assert coordinator.last_update_success is True
    assert coordinator.data == last


async def test_bot_check_without_data_fails_as_a_fetch_error():
    coordinator = await _refresh(_session(text=BOT_CHECK))
    assert coordinator.last_update_success is False
    kwargs = coordinator.last_exception.translation_kwargs
    assert kwargs["translation_key"] == "fetch_failed"
    assert "bot check" in kwargs["translation_placeholders"]["error"]


async def test_changed_markup_fails_the_update():
    coordinator = await _refresh(_session(text="<html></html>"))
    assert coordinator.last_update_success is False


# -- calendar entities -------------------------------------------------------


def _calendar(kind: str) -> SingaporeSchoolCalendar:
    coordinator = MagicMock()
    coordinator.data = _parse_school_calendar(FIXTURE)
    return SingaporeSchoolCalendar(coordinator, "entry1", kind)


def test_calendar_identity():
    cal = _calendar("holidays")
    assert cal.unique_id == "entry1_school_holidays"
    assert cal._attr_translation_key == "school_holidays"
    assert cal.device_info["identifiers"] == {("singapore", "entry1_school")}
    assert cal.device_info["name"] == "School"
    assert cal.extra_state_attributes == {
        "source": "MOE",
        "events": 3,
        "url": "https://www.moe.gov.sg/calendar",
    }


def test_event_is_the_current_one_then_the_next():
    cal = _calendar("holidays")
    with patch(
        "custom_components.singapore.calendar.dt_util.now",
        return_value=datetime(2026, 3, 20, 9, 0),
    ):
        current = cal.event
    assert current.summary == "Term 1 school holidays (MK, Primary & Secondary)"
    assert current.start == date(2026, 3, 14)
    assert current.end == date(2026, 3, 23)  # exclusive end
    with patch(
        "custom_components.singapore.calendar.dt_util.now",
        return_value=datetime(2026, 4, 1, 9, 0),
    ):
        assert cal.event.summary.startswith("Term 4 school holidays")
    with patch(
        "custom_components.singapore.calendar.dt_util.now",
        return_value=datetime(2027, 1, 5, 9, 0),
    ):
        assert cal.event is None


async def test_get_events_returns_overlapping_events():
    cal = _calendar("terms")
    events = await cal.async_get_events(
        MagicMock(), datetime(2026, 3, 1), datetime(2026, 3, 2)
    )
    assert [e.summary for e in events] == ["Term 1 (MK, Primary & Secondary)"]
    exams = await _calendar("exams").async_get_events(
        MagicMock(), datetime(2026, 1, 1), datetime(2026, 12, 31)
    )
    assert [e.summary for e in exams] == ["PSLE--Written"]
    none = await cal.async_get_events(
        MagicMock(), datetime(2026, 6, 1), datetime(2026, 6, 2)
    )
    assert none == []


def test_calendar_without_data():
    coordinator = MagicMock()
    coordinator.data = None
    cal = SingaporeSchoolCalendar(coordinator, "entry1", "terms")
    assert cal.event is None
    assert cal.extra_state_attributes["events"] == 0


async def _setup_calendars(options: dict, school=True) -> list:
    entry = ConfigEntry("entry1")
    entry.options = options
    entry.runtime_data = SimpleNamespace(
        holiday=None,
        school=MagicMock() if school else None,
        sp_accounts={},
    )
    added: list = []
    with patch(
        "custom_components.singapore.calendar.async_setup_sp_account_calendars",
        return_value=[],
    ):
        await calendar_setup_entry(MagicMock(), entry, added.extend)
    return [e._attr_translation_key for e in added]


async def test_platform_creates_the_chosen_calendars():
    assert await _setup_calendars({}) == [
        "school_holidays",
        "school_terms",
        "school_exams",
    ]
    only_holidays = {CONF_DISABLED_SCHOOL_EVENTS: ["terms", "exams"]}
    assert await _setup_calendars(only_holidays) == ["school_holidays"]
    assert await _setup_calendars({}, school=False) == []


# -- options -----------------------------------------------------------------


def test_school_event_options_are_stored_inverted():
    base = {"sources": list(SOURCES), "polling": "normal"}
    assert CONF_DISABLED_SCHOOL_EVENTS not in options_from_input(
        {**base, "school_events": ["holidays", "terms", "exams"]}
    )
    # Not in the submitted form at all: keep everything.
    assert CONF_DISABLED_SCHOOL_EVENTS not in options_from_input(base)
    stored = options_from_input({**base, "school_events": ["holidays"]})
    assert stored[CONF_DISABLED_SCHOOL_EVENTS] == ["terms", "exams"]
    assert enabled_school_events(stored) == {"holidays"}
    assert enabled_school_events({}) == {"holidays", "terms", "exams"}


async def test_options_flow_requires_a_school_calendar_when_the_source_is_on():
    flow = SingaporeOptionsFlow()
    flow.config_entry = ConfigEntry()
    flow.config_entry.options = {}
    result = await flow.async_step_init(
        {"sources": list(SOURCES), "school_events": [], "polling": "normal"}
    )
    assert result["errors"] == {"school_events": "no_school_events"}

    # With the source off, an empty choice is fine.
    without_school = [s for s in SOURCES if s != "school"]
    result = await flow.async_step_init(
        {"sources": without_school, "school_events": [], "polling": "normal"}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_DISABLED_SCHOOL_EVENTS] == [
        "holidays",
        "terms",
        "exams",
    ]


# -- stale cleanup -----------------------------------------------------------


def test_unticked_school_calendars_are_removed():
    entries = [
        SimpleNamespace(
            entity_id=f"calendar.school_{kind}",
            domain="calendar",
            translation_key=f"school_{kind}",
            config_subentry_id=None,
        )
        for kind in ("holidays", "terms", "exams")
    ]
    entry = ConfigEntry("entry1")
    entry.options = {CONF_DISABLED_SCHOOL_EVENTS: ["terms"]}
    entity_registry = MagicMock()
    with (
        patch("custom_components.singapore.er.async_get", return_value=entity_registry),
        patch(
            "custom_components.singapore.er.async_entries_for_config_entry",
            return_value=entries,
        ),
        patch("custom_components.singapore.dr.async_get"),
        patch(
            "custom_components.singapore.dr.async_entries_for_config_entry",
            return_value=[],
        ),
    ):
        async_remove_stale(MagicMock(), entry, set(SOURCES))
    removed = [c.args[0] for c in entity_registry.async_remove.call_args_list]
    assert removed == ["calendar.school_terms"]
