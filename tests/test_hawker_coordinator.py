"""Tests for the hawker centre closure coordinator and calendar."""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.calendar import SingaporeHawkerClosureCalendar
from custom_components.singapore.hawker_coordinator import (
    CLOSURE_CLEANING,
    CLOSURE_OTHER_WORKS,
    NEAREST_CENTRES,
    HawkerCentre,
    HawkerClosure,
    HawkerClosureCoordinator,
    HawkerData,
    _parse_hawker,
)

_PATCH_SESSION = (
    "custom_components.singapore.hawker_coordinator.async_get_clientsession"
)


def _row(name, lat, lon, **dates):
    row = {
        "name": name,
        "latitude_hc": str(lat),
        "longitude_hc": str(lon),
        "address_myenv": f"{name} address",
        "status": "Existing",
    }
    for q in range(1, 5):
        row[f"q{q}_cleaningstartdate"] = "TBC"
        row[f"q{q}_cleaningenddate"] = "TBC"
        row[f"remarks_q{q}"] = "nil"
    row.update(
        other_works_startdate="NA", other_works_enddate="NA", remarks_other_works="nil"
    )
    row.update(dates)
    return row


# Shaped like the live CKAN result (2026-11-19).
_PAYLOAD = {
    "success": True,
    "result": {
        "records": [
            _row(
                "Adam Road Food Centre",
                1.324131966,
                103.8141632,
                q1_cleaningstartdate="30/3/2026",
                q1_cleaningenddate="31/3/2026",
                q4_cleaningstartdate="7/12/2026",
                q4_cleaningenddate="8/12/2026",
            ),
            _row(
                "Old Airport Road Food Centre",
                1.308,
                103.886,
                q4_cleaningstartdate="1/12/2026",
                q4_cleaningenddate="1/12/2026",
                other_works_startdate="1/1/2027",
                other_works_enddate="31/3/2027",
                remarks_other_works="Repairs and redecoration works",
            ),
            _row("No Location", "", ""),
            {"name": "nil"},
        ]
    },
}


def test_parse_closures():
    data = _parse_hawker(_PAYLOAD)

    assert [c.name for c in data.centres] == [
        "Adam Road Food Centre",
        "Old Airport Road Food Centre",
        "No Location",
    ]
    adam = data.centres[0]
    assert adam.address == "Adam Road Food Centre address"
    assert adam.closures == (
        HawkerClosure(CLOSURE_CLEANING, date(2026, 3, 30), date(2026, 3, 31)),
        HawkerClosure(CLOSURE_CLEANING, date(2026, 12, 7), date(2026, 12, 8)),
    )
    other = data.centres[1].closures[-1]
    assert other.kind == CLOSURE_OTHER_WORKS
    assert other.remarks == "Repairs and redecoration works"
    assert data.centres[2].latitude is None


def test_parse_single_day_and_swapped_dates():
    payload = {
        "result": {
            "records": [
                _row(
                    "X",
                    1.3,
                    103.8,
                    q1_cleaningstartdate="5/1/2026",
                    q1_cleaningenddate="TBC",
                    q2_cleaningstartdate="9/4/2026",
                    q2_cleaningenddate="7/4/2026",
                )
            ]
        }
    }
    (centre,) = _parse_hawker(payload).centres
    assert [(c.start, c.end) for c in centre.closures] == [
        (date(2026, 1, 5), date(2026, 1, 5)),
        (date(2026, 4, 7), date(2026, 4, 9)),
    ]


@pytest.mark.parametrize(
    "payload", [{}, [], {"result": {}}, {"result": {"records": []}}]
)
def test_parse_bad_payload_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_hawker(payload)


def test_nearest_skips_centres_without_location():
    data = _parse_hawker(_PAYLOAD)
    assert [c.name for c in data.nearest(1.31, 103.88, 5)] == [
        "Old Airport Road Food Centre",
        "Adam Road Food Centre",
    ]
    assert [c.name for c in data.nearest(1.31, 103.88, 1)] == [
        "Old Airport Road Food Centre"
    ]


def _response(status=200, payload=None):
    response = AsyncMock()
    response.headers = {}
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


@pytest.mark.asyncio
async def test_coordinator_fetches_and_falls_back():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(payload=_PAYLOAD))
    coordinator = HawkerClosureCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert len(coordinator.data.centres) == 3
        cached = coordinator.data

        session.get = MagicMock(return_value=_response(status=500))
        await coordinator.async_refresh()
        assert coordinator.data is cached
        assert coordinator.last_update_success is True

        session.get = MagicMock(return_value=_response(payload={"result": {}}))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


def _calendar(data):
    coordinator = MagicMock()
    coordinator.data = data
    calendar = SingaporeHawkerClosureCalendar(coordinator, "entry1")
    calendar.hass = MagicMock()
    calendar.hass.config.latitude = 1.31
    calendar.hass.config.longitude = 103.88
    return calendar


def _dt(*args):
    return datetime(*args)


@pytest.mark.asyncio
async def test_calendar_events_for_nearby_centres():
    calendar = _calendar(_parse_hawker(_PAYLOAD))
    assert calendar.unique_id == "entry1_hawker_closures"

    events = await calendar.async_get_events(
        calendar.hass, _dt(2026, 12, 1), _dt(2026, 12, 31)
    )
    assert [(e.summary, e.start, e.end) for e in events] == [
        (
            "Old Airport Road Food Centre closed (cleaning)",
            date(2026, 12, 1),
            date(2026, 12, 2),
        ),
        (
            "Adam Road Food Centre closed (cleaning)",
            date(2026, 12, 7),
            date(2026, 12, 9),
        ),
    ]
    assert events[1].location == "Adam Road Food Centre address"

    works = await calendar.async_get_events(
        calendar.hass, _dt(2027, 2, 1), _dt(2027, 2, 2)
    )
    assert works[0].summary == "Old Airport Road Food Centre closed (other works)"
    assert "Repairs" in works[0].description

    centres = calendar.extra_state_attributes["centres"]
    assert [c["name"] for c in centres][0] == "Old Airport Road Food Centre"


def test_calendar_current_event_and_limit():
    far = [
        HawkerCentre(
            f"C{i}",
            None,
            1.31 + i * 0.01,
            103.88,
            (
                HawkerClosure(
                    CLOSURE_CLEANING, date(2099, 1, i + 1), date(2099, 1, i + 1)
                ),
            ),
        )
        for i in range(NEAREST_CENTRES + 2)
    ]
    calendar = _calendar(HawkerData(centres=far))
    assert calendar.event.summary == "C0 closed (cleaning)"
    assert len(calendar.extra_state_attributes["centres"]) == NEAREST_CENTRES
    assert _calendar(None).event is None
