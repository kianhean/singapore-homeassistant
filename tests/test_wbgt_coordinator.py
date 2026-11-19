"""Tests for the NEA heat stress (WBGT) coordinator and sensors."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.sensor import (
    SingaporeHeatStressSensor,
    SingaporeWbgtSensor,
)
from custom_components.singapore.wbgt_coordinator import (
    HEAT_STRESS_OPTIONS,
    WbgtCoordinator,
    WbgtData,
    _parse_wbgt,
    heat_stress,
)

_PATCH_SESSION = "custom_components.singapore.wbgt_coordinator.async_get_clientsession"

# Trimmed from the live payload on 2026-11-19.
_LIVE = {
    "code": 0,
    "data": {
        "records": [
            {
                "datetime": "2026-11-19T18:00:00+08:00",
                "item": {
                    "type": "observation",
                    "isStationData": True,
                    "readings": [
                        {
                            "station": {
                                "id": "S124",
                                "name": "Upper Changi Road North",
                                "townCenter": "Changi Meteorological Station",
                            },
                            "location": {
                                "latitude": "1.36777",
                                "longitude": "103.982262",
                            },
                            "wbgt": "25.4",
                            "heatStress": "Low",
                        },
                        {
                            "station": {
                                "id": "S125",
                                "name": "Woodlands Street 13",
                                "townCenter": "Woodlands Stadium",
                            },
                            "location": {
                                "latitude": "1.433789",
                                "longitude": "103.780731",
                            },
                            "wbgt": "32.1",
                            "heatStress": "Moderate",
                        },
                    ],
                },
            }
        ]
    },
    "errorMsg": "",
}


def test_parse_live_shape():
    data = _parse_wbgt(_LIVE)

    assert [r.station_id for r in data.readings] == ["S124", "S125"]
    changi = data.readings[0]
    assert changi.wbgt == 25.4
    assert changi.heat_stress == "low"
    assert changi.town == "Changi Meteorological Station"
    assert (changi.latitude, changi.longitude) == (1.36777, 103.982262)
    assert data.timestamp.isoformat() == "2026-11-19T18:00:00+08:00"
    assert data.highest.station_id == "S125"


def test_nearest_station():
    data = _parse_wbgt(_LIVE)
    reading, distance = data.nearest(1.43, 103.78)  # Woodlands
    assert reading.station_id == "S125"
    assert distance < 1
    assert WbgtData().nearest(1.43, 103.78) is None


def test_parse_skips_bad_rows_and_derives_missing_category():
    payload = {
        "data": {
            "records": [
                {
                    "item": {
                        "readings": [
                            "junk",
                            {"location": {"latitude": "1.3"}, "wbgt": "30"},
                            {
                                "station": {"id": "S1"},
                                "location": {"latitude": "1.3", "longitude": "103.8"},
                                "wbgt": "33.2",
                                "heatStress": "Unheard of",
                            },
                        ]
                    }
                }
            ]
        }
    }
    (reading,) = _parse_wbgt(payload).readings
    assert reading.name == "S1"
    assert reading.heat_stress == "high"


def test_parse_empty_readings_is_valid():
    assert (
        _parse_wbgt({"data": {"records": [{"item": {"readings": []}}]}}).readings == []
    )


@pytest.mark.parametrize(
    "payload", [{}, [], {"data": {"records": []}}, {"data": {"records": [{}]}}]
)
def test_parse_bad_envelope_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_wbgt(payload)


@pytest.mark.parametrize(
    ("value", "band"),
    [
        (25.0, "low"),
        (30.9, "low"),
        (31.0, "moderate"),
        (32.9, "moderate"),
        (33.0, "high"),
        (None, None),
    ],
)
def test_heat_stress_bands(value, band):
    assert heat_stress(value) == band
    assert band is None or band in HEAT_STRESS_OPTIONS


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
    session.get = MagicMock(return_value=_response(payload=_LIVE))
    coordinator = WbgtCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        cached = coordinator.data

        session.get = MagicMock(return_value=_response(status=503))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        session.get = MagicMock(return_value=_response(payload={"data": {}}))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_http_error_without_cache_fails():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(status=503))
    coordinator = WbgtCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False


def _sensor(cls, data):
    coordinator = MagicMock()
    coordinator.data = data
    sensor = cls(coordinator, "entry1")
    sensor.hass = MagicMock()
    sensor.hass.config.latitude = 1.36
    sensor.hass.config.longitude = 103.98  # Changi
    return sensor


def test_wbgt_sensor_uses_the_nearest_station():
    sensor = _sensor(SingaporeWbgtSensor, _parse_wbgt(_LIVE))
    assert sensor.unique_id == "entry1_wbgt"
    assert sensor.native_value == 25.4
    attrs = sensor.extra_state_attributes
    assert attrs["station"] == "Upper Changi Road North"
    assert attrs["heat_stress"] == "low"
    assert attrs["highest_wbgt"] == 32.1
    assert attrs["latitude"] == 1.36777
    assert attrs["distance_km"] < 2


def test_heat_stress_sensor():
    sensor = _sensor(SingaporeHeatStressSensor, _parse_wbgt(_LIVE))
    assert sensor.unique_id == "entry1_heat_stress"
    assert sensor.native_value == "low"
    assert sensor._attr_options == HEAT_STRESS_OPTIONS


def test_sensors_without_data():
    assert _sensor(SingaporeWbgtSensor, None).native_value is None
    assert _sensor(SingaporeWbgtSensor, None).extra_state_attributes == {}
    assert _sensor(SingaporeHeatStressSensor, WbgtData()).native_value is None
