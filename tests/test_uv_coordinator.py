"""Tests for the NEA UV index coordinator and sensors."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.sensor import (
    SingaporeUvExposureSensor,
    SingaporeUvIndexSensor,
)
from custom_components.singapore.uv_coordinator import (
    UV_EXPOSURE_OPTIONS,
    UvCoordinator,
    UvData,
    UvReading,
    _parse_uv,
    uv_exposure,
)

_PATCH_SESSION = "custom_components.singapore.uv_coordinator.async_get_clientsession"

# Trimmed from the live v2 payload on 2026-11-13 (newest hour first).
_LIVE = {
    "code": 0,
    "data": {
        "records": [
            {
                "date": "2026-11-13",
                "updatedTimestamp": "2026-11-13T17:11:13+08:00",
                "timestamp": "2026-11-13T17:00:00+08:00",
                "index": [
                    {"hour": "2026-11-13T17:00:00+08:00", "value": 1},
                    {"hour": "2026-11-13T16:00:00+08:00", "value": 3},
                    {"hour": "2026-11-13T14:00:00+08:00", "value": 6},
                    {"hour": "2026-11-13T15:00:00+08:00", "value": 5},
                    {"hour": "2026-11-13T13:00:00+08:00", "value": 6},
                    {"hour": "2026-11-13T07:00:00+08:00", "value": 0},
                ],
            }
        ]
    },
    "errorMsg": "",
}


def _hour(h: int) -> datetime:
    return datetime.fromisoformat(f"2026-11-13T{h:02d}:00:00+08:00")


def test_parse_live_shape_sorts_oldest_first():
    data = _parse_uv(_LIVE)

    assert [r.hour.hour for r in data.readings] == [7, 13, 14, 15, 16, 17]
    assert data.latest == UvReading(_hour(17), 1)
    # Ties keep the earliest hour.
    assert data.peak == UvReading(_hour(13), 6)


def test_parse_v1_shape():
    payload = {
        "items": [
            {
                "index": [
                    {"timestamp": "2026-11-13T12:00:00+08:00", "value": 9},
                    {"timestamp": "2026-11-13T11:00:00+08:00", "value": "7"},
                ]
            }
        ]
    }

    data = _parse_uv(payload)

    assert [(r.hour.hour, r.value) for r in data.readings] == [(11, 7), (12, 9)]


def test_parse_skips_bad_rows():
    payload = {
        "data": {
            "records": [
                {
                    "index": [
                        {"hour": "2026-11-13T09:00:00+08:00", "value": None},
                        {"hour": "not a time", "value": 3},
                        {"hour": "2026-11-13T10:00:00+08:00", "value": -1},
                        "junk",
                        {"hour": "2026-11-13T11:00:00+08:00", "value": 4},
                    ]
                }
            ]
        }
    }

    assert _parse_uv(payload).readings == [UvReading(_hour(11), 4)]


def test_parse_empty_index_is_valid():
    payload = {"data": {"records": [{"index": []}]}}

    data = _parse_uv(payload)

    assert data.readings == []
    assert data.latest is None
    assert data.peak is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"data": {"records": []}}, {"data": {"records": [{"date": "x"}]}}, []],
)
def test_parse_bad_envelope_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_uv(payload)


@pytest.mark.parametrize(
    ("value", "band"),
    [
        (0, "low"),
        (2, "low"),
        (3, "moderate"),
        (5, "moderate"),
        (6, "high"),
        (7, "high"),
        (8, "very_high"),
        (10, "very_high"),
        (11, "extreme"),
        (14, "extreme"),
        (None, None),
    ],
)
def test_uv_exposure_bands(value, band):
    assert uv_exposure(value) == band
    assert band is None or band in UV_EXPOSURE_OPTIONS


def _response(status=200, payload=None):
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


@pytest.mark.asyncio
async def test_coordinator_fetches_and_parses():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(payload=_LIVE))
    coordinator = UvCoordinator(MagicMock())

    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.latest.value == 1


@pytest.mark.asyncio
async def test_coordinator_http_error_fallback_and_failure():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(status=429))
    coordinator = UvCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        cached = UvData(readings=[UvReading(_hour(12), 9)])
        coordinator.data = cached
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached


@pytest.mark.asyncio
async def test_coordinator_bad_payload_fails_even_with_cache():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(payload={"data": {}}))
    coordinator = UvCoordinator(MagicMock())
    coordinator.data = UvData(readings=[UvReading(_hour(12), 9)])

    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()

    # A changed payload shape must surface as a failure, not stale data.
    assert coordinator.last_update_success is False


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_uv_index_sensor():
    sensor = SingaporeUvIndexSensor(_coordinator(_parse_uv(_LIVE)), "entry1")

    assert sensor.unique_id == "entry1_uv_index"
    assert sensor.native_unit_of_measurement == "UV index"
    assert sensor.native_value == 1
    attrs = sensor.extra_state_attributes
    assert attrs["hour"] == "2026-11-13T17:00:00+08:00"
    assert attrs["exposure"] == "low"
    assert attrs["peak"] == 6
    assert attrs["peak_hour"] == "2026-11-13T13:00:00+08:00"
    assert attrs["readings"][0] == {"hour": "2026-11-13T07:00:00+08:00", "value": 0}
    assert sensor.device_info["identifiers"] == {("singapore", "entry1_nea")}


def test_uv_exposure_sensor():
    data = UvData(readings=[UvReading(_hour(13), 9)])
    sensor = SingaporeUvExposureSensor(_coordinator(data), "entry1")

    assert sensor.unique_id == "entry1_uv_exposure"
    assert sensor._attr_options == UV_EXPOSURE_OPTIONS
    assert sensor.native_value == "very_high"


@pytest.mark.parametrize("data", [None, UvData()])
def test_uv_sensors_without_readings(data):
    index = SingaporeUvIndexSensor(_coordinator(data), "entry1")
    exposure = SingaporeUvExposureSensor(_coordinator(data), "entry1")

    assert index.native_value is None
    assert index.extra_state_attributes == {}
    assert exposure.native_value is None
