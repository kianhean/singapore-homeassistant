"""Tests for the NEA dengue clusters coordinator and sensors."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.dengue_coordinator import (
    RETRY_INTERVAL,
    UPDATE_INTERVAL,
    DengueClusterCoordinator,
    DengueData,
    UpdateFailed,
    _parse_dengue_geojson,
    _retry_after_seconds,
    distance_to_cluster_km,
    nearest_cluster,
)
from custom_components.singapore.sensor import (
    SingaporeDengueClustersSensor,
    SingaporeNearestDengueClusterSensor,
)

# ~1.1 km square around (1.385, 103.835) — Lentor-ish.
_SQUARE = [
    [103.830, 1.380],
    [103.840, 1.380],
    [103.840, 1.390],
    [103.830, 1.390],
    [103.830, 1.380],
]


def _feature(locality: str, cases: int, ring=None, gtype="Polygon") -> dict:
    ring = ring or _SQUARE
    coords = [ring] if gtype == "Polygon" else [[ring]]
    return {
        "type": "Feature",
        "properties": {
            "OBJECTID": 1,
            "LOCALITY": locality,
            "CASE_SIZE": cases,
            "NAME": "Dengue_Cluster",
        },
        "geometry": {"type": gtype, "coordinates": coords},
    }


def _geojson(*features) -> dict:
    return {"type": "FeatureCollection", "name": "x", "features": list(features)}


_FAR_RING = [[103.90, 1.30], [103.91, 1.30], [103.91, 1.31], [103.90, 1.31]]


def test_parse_clusters_sorted_by_cases_with_centroid():
    data = _parse_dengue_geojson(
        _geojson(_feature("Small", 3, _FAR_RING), _feature("Lentor Gdns", 21))
    )
    assert [c.locality for c in data.clusters] == ["Lentor Gdns", "Small"]
    assert data.total_cases == 24
    assert data.clusters[0].latitude == pytest.approx(1.384, abs=1e-3)


def test_parse_multipolygon():
    data = _parse_dengue_geojson(_geojson(_feature("Multi", 5, gtype="MultiPolygon")))
    assert len(data.clusters[0].polygons) == 1


def test_parse_empty_feature_collection_is_valid():
    assert _parse_dengue_geojson(_geojson()).clusters == []


def test_parse_bad_payload_raises():
    with pytest.raises(UpdateFailed):
        _parse_dengue_geojson({"code": 0})


def test_distance_inside_is_zero():
    cluster = _parse_dengue_geojson(_geojson(_feature("A", 1))).clusters[0]
    assert distance_to_cluster_km(1.385, 103.835, cluster) == 0.0


def test_distance_outside_to_edge():
    cluster = _parse_dengue_geojson(_geojson(_feature("A", 1))).clusters[0]
    # 0.01° of latitude north of the top edge ≈ 1.11 km
    assert distance_to_cluster_km(1.400, 103.835, cluster) == pytest.approx(
        1.112, abs=0.01
    )


def test_nearest_cluster_picks_closest():
    data = _parse_dengue_geojson(
        _geojson(_feature("Far", 50, _FAR_RING), _feature("Near", 2))
    )
    cluster, dist = nearest_cluster(1.395, 103.835, data)
    assert cluster.locality == "Near"
    assert dist == pytest.approx(0.556, abs=0.01)
    assert nearest_cluster(1.3, 103.8, DengueData()) is None


def _response(status=200, payload=None, headers=None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


@pytest.mark.asyncio
async def test_coordinator_polls_then_downloads_file():
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _response(payload={"code": 0, "data": {"url": "https://s3/file"}}),
            _response(payload=_geojson(_feature("A", 4))),
        ]
    )
    coordinator = DengueClusterCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.dengue_coordinator.async_get_clientsession",
        return_value=session,
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.total_cases == 4
    assert session.get.call_args_list[1].args[0] == "https://s3/file"


@pytest.mark.asyncio
async def test_coordinator_accepts_201_from_download_api():
    # data.gov.sg's poll-download returns 201 Created with the signed URL.
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _response(status=201, payload={"code": 0, "data": {"url": "https://s3/f"}}),
            _response(payload=_geojson(_feature("A", 4))),
        ]
    )
    coordinator = DengueClusterCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.dengue_coordinator.async_get_clientsession",
        return_value=session,
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.total_cases == 4


@pytest.mark.asyncio
async def test_coordinator_initiates_download_when_poll_has_no_url():
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _response(payload={"code": 0, "data": {}}),
            _response(payload={"code": 0, "data": {"url": "https://s3/file"}}),
            _response(payload=_geojson()),
        ]
    )
    coordinator = DengueClusterCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.dengue_coordinator.async_get_clientsession",
        return_value=session,
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.clusters == []


@pytest.mark.asyncio
async def test_coordinator_retries_429_then_succeeds():
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _response(status=429, headers={"Retry-After": "5"}),
            _response(status=429),
            _response(payload={"code": 0, "data": {"url": "https://s3/file"}}),
            _response(payload=_geojson(_feature("A", 4))),
        ]
    )
    sleep = AsyncMock()
    coordinator = DengueClusterCoordinator(MagicMock())
    with (
        patch(
            "custom_components.singapore.dengue_coordinator.async_get_clientsession",
            return_value=session,
        ),
        patch("custom_components.singapore.dengue_coordinator.asyncio.sleep", sleep),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.total_cases == 4
    assert [c.args[0] for c in sleep.await_args_list] == [5.0, 4.0]
    assert coordinator.update_interval == UPDATE_INTERVAL


def test_retry_after_seconds():
    assert _retry_after_seconds("3", 0) == 3.0
    assert _retry_after_seconds("9999", 0) == 60.0
    assert _retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT", 0) == 2.0
    assert _retry_after_seconds(None, 2) == 8.0


@pytest.mark.asyncio
async def test_coordinator_http_error_fallback_and_failure():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(status=429))
    sleep = AsyncMock()
    coordinator = DengueClusterCoordinator(MagicMock())
    with (
        patch(
            "custom_components.singapore.dengue_coordinator.async_get_clientsession",
            return_value=session,
        ),
        patch("custom_components.singapore.dengue_coordinator.asyncio.sleep", sleep),
    ):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        # 1 attempt + 3 retries, then give up and retry soon (no data yet).
        assert session.get.call_count == 4
        assert coordinator.update_interval == RETRY_INTERVAL

        coordinator.data = DengueData()
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_dengue_clusters_sensor():
    data = _parse_dengue_geojson(_geojson(_feature("A", 4), _feature("B", 6)))
    sensor = SingaporeDengueClustersSensor(_coordinator(data), "entry1")
    assert sensor.native_value == 2
    assert sensor.unique_id == "entry1_dengue_clusters"
    assert sensor.extra_state_attributes["total_cases"] == 10
    assert sensor.device_info["identifiers"] == {("singapore", "entry1_nea")}
    assert SingaporeDengueClustersSensor(_coordinator(None), "e").native_value is None


def test_nearest_dengue_cluster_sensor_uses_home_location():
    data = _parse_dengue_geojson(_geojson(_feature("Lentor", 21)))
    sensor = SingaporeNearestDengueClusterSensor(_coordinator(data), "entry1")
    sensor.hass = MagicMock()
    sensor.hass.config.latitude = 1.385
    sensor.hass.config.longitude = 103.835
    assert sensor.native_value == 0.0
    assert sensor.native_unit_of_measurement == "km"
    assert sensor.extra_state_attributes["inside_cluster"] is True
    assert sensor.extra_state_attributes["locality"] == "Lentor"

    empty = SingaporeNearestDengueClusterSensor(_coordinator(DengueData()), "entry1")
    empty.hass = sensor.hass
    assert empty.native_value is None
    assert empty.extra_state_attributes == {}
