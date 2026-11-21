"""Tests for the LTA traffic incidents coordinator and sensors."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.sensor import (
    SingaporeNearestTrafficIncidentSensor,
    SingaporeTrafficIncidentsSensor,
)
from custom_components.singapore.traffic_incidents_coordinator import (
    TrafficIncident,
    TrafficIncidentsCoordinator,
    TrafficIncidentsData,
    _parse_traffic_incidents,
)

_PATCH_SESSION = (
    "custom_components.singapore.traffic_incidents_coordinator.async_get_clientsession"
)
_HOME = (1.3521, 103.8198)


def _payload(*items: dict) -> dict:
    return {
        "odata.metadata": "https://datamall2.mytransport.sg/ltaodataservice/TrafficIncidents",
        "value": list(items),
    }


def _incident(
    itype: str = "Accident",
    msg: str = "(21/11)10:09 Accident on PIE.",
    lat: float | str | None = 1.3550,
    lon: float | str | None = 103.8200,
) -> dict:
    return {
        "Type": itype,
        "Latitude": lat,
        "Longitude": lon,
        "Message": msg,
    }


def test_parse_traffic_incidents():
    data = _parse_traffic_incidents(
        _payload(
            _incident("Accident", "(21/11)10:09 Accident on PIE.", 1.355, 103.82),
            _incident("Roadwork", "(21/11)10:05 Road Works on KJE.", 1.390, 103.77),
            _incident("Roadwork", "(21/11)10:04 Road Works on TPE.", "bad", None),
            {"Type": "Accident", "Message": "   "},  # blank message skipped
            "not-a-dict",
        )
    )
    assert len(data.incidents) == 3
    assert data.counts_by_type == {"Accident": 1, "Roadwork": 2}
    assert data.incidents[0].type == "Accident"
    assert data.incidents[0].latitude == 1.355
    assert data.incidents[2].latitude is None
    nearest = data.nearest(*_HOME)
    assert nearest is not None
    assert nearest[0].type == "Accident"
    assert nearest[1] < 0.5


def test_parse_empty_incidents():
    data = _parse_traffic_incidents(_payload())
    assert data.incidents == []
    assert data.counts_by_type == {}
    assert data.nearest(*_HOME) is None


@pytest.mark.parametrize("bad", [{}, [], {"value": None}, {"value": "no"}])
def test_parse_bad_payload_raises(bad):
    with pytest.raises(UpdateFailed):
        _parse_traffic_incidents(bad)


def _response(status: int = 200, payload=None):
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


@pytest.mark.asyncio
async def test_coordinator_fetch_and_fallback():
    session = MagicMock()
    session.post = MagicMock(return_value=_response(payload=_payload(_incident())))
    coordinator = TrafficIncidentsCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert len(coordinator.data.incidents) == 1
        cached = coordinator.data

        # HTTP error with cached data falls back
        session.post = MagicMock(return_value=_response(status=503))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        # Network error with cached data falls back
        session.post = MagicMock(side_effect=aiohttp.ClientError("down"))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        # Bad payload propagates UpdateFailed
        session.post = MagicMock(return_value=_response(payload={}))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_error_without_cache_fails():
    session = MagicMock()
    session.post = MagicMock(return_value=_response(status=502))
    coordinator = TrafficIncidentsCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

    session.post = MagicMock(side_effect=TimeoutError("timeout"))
    coordinator2 = TrafficIncidentsCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator2.async_refresh()
        assert coordinator2.last_update_success is False


def test_traffic_incidents_sensors():
    incidents = [
        TrafficIncident(
            type="Accident",
            message="(21/11)10:09 Accident on PIE.",
            latitude=1.355,
            longitude=103.82,
        ),
        TrafficIncident(
            type="Roadwork",
            message="(21/11)10:05 Road Works on KJE.",
            latitude=1.39,
            longitude=103.77,
        ),
    ]
    data = TrafficIncidentsData(incidents=incidents)
    coord = MagicMock()
    coord.data = data

    count_sensor = SingaporeTrafficIncidentsSensor(coord, "e1")
    assert count_sensor.unique_id == "e1_traffic_incidents"
    assert count_sensor.native_value == 2
    assert count_sensor.extra_state_attributes["counts_by_type"] == {
        "Accident": 1,
        "Roadwork": 1,
    }
    assert len(count_sensor.extra_state_attributes["incidents"]) == 2

    nearest_sensor = SingaporeNearestTrafficIncidentSensor(coord, "e1")
    nearest_sensor.hass = SimpleNamespace(
        config=SimpleNamespace(latitude=_HOME[0], longitude=_HOME[1])
    )
    assert nearest_sensor.unique_id == "e1_nearest_traffic_incident"
    assert nearest_sensor.native_value is not None and nearest_sensor.native_value < 0.5
    attrs = nearest_sensor.extra_state_attributes
    assert attrs["type"] == "Accident"
    assert attrs["message"] == "(21/11)10:09 Accident on PIE."
    assert attrs["latitude"] == 1.355

    coord.data = None
    assert count_sensor.native_value is None
    assert count_sensor.extra_state_attributes == {}
    assert nearest_sensor.native_value is None
    assert nearest_sensor.extra_state_attributes == {}
