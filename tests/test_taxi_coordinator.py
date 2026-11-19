"""Tests for the LTA taxi availability coordinator and sensors."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.sensor import (
    SingaporeTaxisAvailableSensor,
    SingaporeTaxisNearbySensor,
)
from custom_components.singapore.taxi_coordinator import (
    TaxiAvailabilityCoordinator,
    TaxiData,
    _parse_taxis,
)

_PATCH_SESSION = "custom_components.singapore.taxi_coordinator.async_get_clientsession"
_HOME = (1.3000, 103.8000)


def _payload(coords, count=None):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "MultiPoint", "coordinates": coords},
                "properties": {
                    "timestamp": "2026-11-19T18:19:30+08:00",
                    "taxi_count": len(coords) if count is None else count,
                    "api_info": {"status": "healthy"},
                },
            }
        ],
    }


# [lon, lat]: ~0.11 km, ~0.9 km, ~1.7 km and ~22 km from home, plus junk.
_COORDS = [
    [103.8010, 1.3000],
    [103.8000, 1.3080],
    [103.8150, 1.3000],
    [104.0000, 1.3000],
    ["x", 1.3],
    [103.8],
]


def test_parse_counts_around_home():
    data = _parse_taxis(_payload(_COORDS, count=3466), *_HOME)
    assert data.total == 3466
    assert (data.within_500_m, data.within_1_km, data.within_2_km) == (1, 2, 3)
    assert data.nearby == 2
    assert data.nearest_km == pytest.approx(0.111, abs=0.002)
    assert data.timestamp.isoformat() == "2026-11-19T18:19:30+08:00"


def test_parse_counts_points_without_total():
    payload = _payload(_COORDS[:2])
    payload["features"][0]["properties"].pop("taxi_count")
    assert _parse_taxis(payload, *_HOME).total == 2


def test_parse_no_taxis():
    data = _parse_taxis(_payload([]), *_HOME)
    assert data.total == 0
    assert data.nearest_km is None


@pytest.mark.parametrize(
    "payload", [{}, [], {"features": []}, {"features": [{"geometry": {}}]}]
)
def test_parse_bad_payload_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_taxis(payload, *_HOME)


def _response(status=200, payload=None):
    response = AsyncMock()
    response.headers = {}
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _hass():
    hass = MagicMock()
    hass.config.latitude, hass.config.longitude = _HOME
    return hass


@pytest.mark.asyncio
async def test_coordinator_uses_home_and_falls_back():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(payload=_payload(_COORDS)))
    coordinator = TaxiAvailabilityCoordinator(_hass())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data.nearby == 2
        cached = coordinator.data

        session.get = MagicMock(return_value=_response(status=429))
        await coordinator.async_refresh()
        assert coordinator.data is cached

        session.get = MagicMock(return_value=_response(payload={}))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


def _sensor(cls, data):
    coordinator = MagicMock()
    coordinator.data = data
    return cls(coordinator, "entry1")


def test_sensors():
    data = TaxiData(3466, 1, 2, 3, 0.111, None)
    total = _sensor(SingaporeTaxisAvailableSensor, data)
    nearby = _sensor(SingaporeTaxisNearbySensor, data)
    assert total.unique_id == "entry1_taxis_available"
    assert total.native_value == 3466
    assert nearby.unique_id == "entry1_taxis_nearby"
    assert nearby.native_value == 2
    assert nearby.extra_state_attributes == {
        "radius_km": 1.0,
        "within_500_m": 1,
        "within_2_km": 3,
        "nearest_km": 0.111,
        "reading_time": None,
    }
    assert _sensor(SingaporeTaxisNearbySensor, None).native_value is None
    assert _sensor(SingaporeTaxisAvailableSensor, None).extra_state_attributes == {}
