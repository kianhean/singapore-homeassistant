"""MUIS prayer times: 12/24-hour parsing, today/next prayer, coordinator, sensors."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.const import PRAYER_TIMES
from custom_components.singapore.prayer_coordinator import (
    PRAYER_DATASET,
    SGT,
    PrayerData,
    PrayerTimesCoordinator,
    _parse_prayer_times,
)
from custom_components.singapore.sensor import (
    SingaporeNextPrayerSensor,
    SingaporePrayerTimeSensor,
)

# Real rows from data.gov.sg: 2025 (12-hour) and 2026 (24-hour).
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "prayer_times.json").read_text()
)
_SESSION = "custom_components.singapore.prayer_coordinator.async_get_clientsession"
_SENSOR_NOW = "custom_components.singapore.sensor.dt_util.utcnow"


def _sgt(*args) -> datetime:
    return datetime(*args, tzinfo=SGT)


def _entry() -> ConfigEntry:
    entry = ConfigEntry("e1")
    entry.options = {}
    return entry


def _session(payload, status=200):
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value=payload)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm
    return session


def test_parse_reads_24_hour_rows():
    data = _parse_prayer_times(FIXTURE)
    day = data.for_date(date(2026, 11, 22))
    assert day.times == {
        "subuh": _sgt(2026, 11, 22, 5, 28),
        "syuruk": _sgt(2026, 11, 22, 6, 50),
        "zohor": _sgt(2026, 11, 22, 12, 53),
        "asar": _sgt(2026, 11, 22, 16, 16),
        "maghrib": _sgt(2026, 11, 22, 18, 53),
        "isyak": _sgt(2026, 11, 22, 20, 7),
    }


def test_parse_turns_12_hour_rows_into_pm():
    # 2025 rows say Zohor "01:06", Asar "04:30", Maghrib "07:10", Isyak "08:25".
    day = _parse_prayer_times(FIXTURE).for_date(date(2025, 6, 10))
    assert [day.times[key].strftime("%H:%M") for key in PRAYER_TIMES] == [
        "05:35",
        "06:59",
        "13:06",
        "16:30",
        "19:10",
        "20:25",
    ]


def test_parse_keeps_noon_zohor():
    data = _parse_prayer_times(
        {
            "result": {
                "records": [{"Date": "2025-01-01", "Subuh": "05:44", "Zohor": "12:58"}]
            }
        }
    )
    assert data.days[0].times["zohor"] == _sgt(2025, 1, 1, 12, 58)


def test_parse_sorts_and_skips_bad_rows():
    data = _parse_prayer_times(
        {
            "result": {
                "records": [
                    {"Date": "2026-11-23", "Subuh": "05:28"},
                    {"Date": "bad", "Subuh": "05:28"},
                    {"Date": "2026-11-22", "Subuh": "25:00", "Zohor": "12:53"},
                    "nope",
                ]
            }
        }
    )
    assert [d.day for d in data.days] == [date(2026, 11, 22), date(2026, 11, 23)]
    assert set(data.days[0].times) == {"zohor"}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"result": {}},
        {"result": {"records": []}},
        {"result": {"records": [{}]}},
    ],
)
def test_parse_bad_payload_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_prayer_times(payload)


def test_today_next_prayer_and_next_change():
    data = _parse_prayer_times(FIXTURE)
    after_subuh = _sgt(2026, 11, 22, 6, 0)
    assert data.today(after_subuh).day == date(2026, 11, 22)
    # Syuruk (sunrise) is skipped: the next prayer after Subuh is Zohor.
    assert data.next_prayer(after_subuh) == ("zohor", _sgt(2026, 11, 22, 12, 53))
    assert data.next_change(after_subuh) == _sgt(2026, 11, 22, 6, 50)
    after_isyak = _sgt(2026, 11, 22, 21, 0)
    assert data.next_prayer(after_isyak) == ("subuh", _sgt(2026, 11, 23, 5, 28))
    # Midnight comes first: "today" rolls over before tomorrow's Subuh.
    assert data.next_change(after_isyak) == _sgt(2026, 11, 23, 0, 0)
    last = _sgt(2026, 11, 23, 21, 0)
    assert data.next_prayer(last) is None
    assert PrayerData().next_change(last) == _sgt(2026, 11, 24, 0, 0)


async def test_coordinator_asks_for_the_next_days():
    session = _session(FIXTURE)
    coordinator = PrayerTimesCoordinator(MagicMock(), _entry())
    with (
        patch(_SESSION, return_value=session),
        patch(
            "custom_components.singapore.prayer_coordinator.dt_util.utcnow",
            return_value=_sgt(2026, 12, 31, 7, 0),
        ),
    ):
        data = await coordinator._async_update_data()
    params = session.get.call_args.kwargs["params"]
    assert params["resource_id"] == PRAYER_DATASET
    assert json.loads(params["filters"]) == {
        "Date": ["2026-12-31", "2027-01-01", "2027-01-02"]
    }
    assert len(data.days) == 4


async def test_coordinator_errors_and_fallback():
    coordinator = PrayerTimesCoordinator(MagicMock(), _entry())
    with patch(_SESSION, return_value=_session(FIXTURE, status=500)):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
    failing = MagicMock()
    failing.get.side_effect = aiohttp.ClientError("down")
    with patch(_SESSION, return_value=failing):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    coordinator.data = previous = _parse_prayer_times(FIXTURE)
    with patch(_SESSION, return_value=_session(FIXTURE, status=429)):
        assert await coordinator._async_update_data() is previous
    with patch(_SESSION, return_value=failing):
        assert await coordinator._async_update_data() is previous
    # A changed payload is not transient: it fails even with data.
    with patch(_SESSION, return_value=_session({"result": {"records": []}})):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


def _coordinator():
    coordinator = PrayerTimesCoordinator(MagicMock(), _entry())
    coordinator.data = _parse_prayer_times(FIXTURE)
    return coordinator


def test_prayer_time_sensors():
    coordinator = _coordinator()
    maghrib = SingaporePrayerTimeSensor(coordinator, "e1", "maghrib")
    assert maghrib.unique_id == "e1_prayer_maghrib"
    assert maghrib._attr_translation_key == "prayer_maghrib"
    assert maghrib.device_class == "timestamp"
    with patch(_SENSOR_NOW, return_value=_sgt(2026, 11, 22, 19, 0)):
        assert maghrib.native_value == _sgt(2026, 11, 22, 18, 53)
        assert maghrib.extra_state_attributes == {
            "date": "2026-11-22",
            "next": _sgt(2026, 11, 23, 18, 53).isoformat(),
        }
    with patch(_SENSOR_NOW, return_value=_sgt(2026, 11, 24, 1, 0)):
        assert maghrib.native_value is None
        assert maghrib.extra_state_attributes == {}
    coordinator.data = None
    assert maghrib.native_value is None
    assert maghrib.extra_state_attributes == {}


def test_next_prayer_sensor():
    coordinator = _coordinator()
    sensor = SingaporeNextPrayerSensor(coordinator, "e1")
    assert sensor.unique_id == "e1_next_prayer"
    with patch(_SENSOR_NOW, return_value=_sgt(2026, 11, 22, 13, 0)):
        assert sensor.native_value == _sgt(2026, 11, 22, 16, 16)
        assert sensor.extra_state_attributes == {"prayer": "asar"}
    coordinator.data = None
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_sensor_reschedules_at_the_next_time():
    coordinator = _coordinator()
    sensor = SingaporeNextPrayerSensor(coordinator, "e1")
    sensor.hass = MagicMock()
    unsub = MagicMock()
    sensor.async_write_ha_state = MagicMock()
    with (
        patch(_SENSOR_NOW, return_value=_sgt(2026, 11, 22, 13, 0)),
        patch(
            "custom_components.singapore.sensor.async_track_point_in_utc_time",
            return_value=unsub,
        ) as track,
    ):
        sensor._handle_coordinator_update()
        assert track.call_args.args[2] == _sgt(2026, 11, 22, 16, 16)
        sensor._async_timer(_sgt(2026, 11, 22, 16, 16))
        assert track.call_count == 2
        sensor.async_write_ha_state.assert_called()
        # The fired timer had nothing to cancel; a poll cancels the pending one.
        unsub.assert_not_called()
        sensor._handle_coordinator_update()
        unsub.assert_called_once()
    sensor._cancel_timer()
    coordinator.data = None
    sensor._schedule_next()
    assert sensor._unsub_timer is None
