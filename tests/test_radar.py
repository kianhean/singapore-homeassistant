"""Tests for the NEA rain radar coordinator and camera entities."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.singapore.camera import SingaporeRainRadarCamera
from custom_components.singapore.radar_coordinator import (
    RADAR_RANGES,
    RADAR_URL,
    RadarCoordinator,
    RadarData,
    UpdateFailed,
    _parse_radar,
)


def _payload(
    radar_range: str = "70km",
    url: str = "https://s3.example/rain.png?sig=1",
    timestamp: str = "2026-11-13T22:25:00+08:00",
) -> dict:
    # Trimmed from a live 2026-11-13 response.
    return {
        "code": 0,
        "data": {
            "projection": {
                "type": "Azimuthal Equidistant",
                "center": {"longitude": 103.972583, "latitude": 1.34911},
            },
            "boundaryBox": {
                "upperLeft": {"longitude": 103.342685, "latitude": 1.97854},
                "lowerRight": {"longitude": 104.602315, "latitude": 0.719515},
                "crs": "EPSG:4326",
            },
            "records": [
                {
                    "timestamp": timestamp,
                    "updatedTimestamp": "2026-11-13T22:24:13+08:00",
                    "image": {
                        "url": url,
                        "urlExpiresAt": "2026-11-13T22:49:51+08:00",
                        "label": radar_range,
                        "range": radar_range,
                        "format": "png",
                    },
                }
            ],
        },
        "errorMsg": "",
    }


def test_parse_radar_image_and_bounds():
    image = _parse_radar(_payload(), "70km")
    assert image.range == "70km"
    assert image.image_url == "https://s3.example/rain.png?sig=1"
    assert image.timestamp.isoformat() == "2026-11-13T22:25:00+08:00"
    assert image.url_expires_at.isoformat() == "2026-11-13T22:49:51+08:00"
    assert image.north == pytest.approx(1.97854)
    assert image.south == pytest.approx(0.719515)
    assert image.west == pytest.approx(103.342685)
    assert image.east == pytest.approx(104.602315)
    assert image.center_latitude == pytest.approx(1.34911)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"code": 0, "data": {"records": []}},
        {"code": 0, "data": {"records": [{"timestamp": "x", "image": {}}]}},
    ],
)
def test_parse_radar_bad_payload_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_radar(payload, "70km")


def test_parse_radar_tolerates_missing_metadata():
    payload = {"data": {"records": [{"image": {"url": "https://x.png"}}]}}
    image = _parse_radar(payload, "480km")
    assert image.image_url == "https://x.png"
    assert image.timestamp is None
    assert image.north is None


def test_radar_ranges():
    assert list(RADAR_RANGES) == ["70km", "240km", "480km"]


def _response(status: int = 200, json_payload=None, body: bytes = b"png"):
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=json_payload)
    response.read = AsyncMock(return_value=body)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _session(responses: dict):
    """Session whose GET answers per-URL; values are responses or exceptions."""
    session = MagicMock()

    def _get(url, **_kwargs):
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    session.get = MagicMock(side_effect=_get)
    return session


def _all_ok(**overrides) -> dict:
    responses = {
        RADAR_URL.format(range=r): _response(
            json_payload=_payload(r, url=f"https://s3.example/{r}.png")
        )
        for r in RADAR_RANGES
    }
    responses.update(overrides)
    return responses


@pytest.mark.asyncio
async def test_coordinator_fetches_every_range():
    coordinator = RadarCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.radar_coordinator.async_get_clientsession",
        return_value=_session(_all_ok()),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert set(coordinator.data.images) == set(RADAR_RANGES)
    assert coordinator.data.images["240km"].image_url.endswith("240km.png")


@pytest.mark.asyncio
async def test_coordinator_partial_failure_keeps_last_image():
    coordinator = RadarCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.radar_coordinator.async_get_clientsession",
        return_value=_session(_all_ok()),
    ):
        await coordinator.async_refresh()
    old_480 = coordinator.data.images["480km"]

    url_480 = RADAR_URL.format(range="480km")
    with patch(
        "custom_components.singapore.radar_coordinator.async_get_clientsession",
        return_value=_session(_all_ok(**{url_480: _response(status=429)})),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.images["480km"] is old_480


@pytest.mark.asyncio
async def test_coordinator_partial_failure_without_cache_omits_range():
    coordinator = RadarCoordinator(MagicMock())
    url_70 = RADAR_URL.format(range="70km")
    with patch(
        "custom_components.singapore.radar_coordinator.async_get_clientsession",
        return_value=_session(_all_ok(**{url_70: aiohttp.ClientError("boom")})),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert set(coordinator.data.images) == {"240km", "480km"}


@pytest.mark.asyncio
async def test_coordinator_all_failed_without_cache_fails():
    coordinator = RadarCoordinator(MagicMock())
    responses = {RADAR_URL.format(range=r): _response(status=503) for r in RADAR_RANGES}
    with patch(
        "custom_components.singapore.radar_coordinator.async_get_clientsession",
        return_value=_session(responses),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_parse_failure_propagates():
    coordinator = RadarCoordinator(MagicMock())
    url_70 = RADAR_URL.format(range="70km")
    bad = _response(json_payload={"code": 0, "data": {"records": []}})
    with patch(
        "custom_components.singapore.radar_coordinator.async_get_clientsession",
        return_value=_session(_all_ok(**{url_70: bad})),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    return coordinator


def _data(*ranges, timestamp="2026-11-13T22:25:00+08:00", url_suffix="") -> RadarData:
    return RadarData(
        images={
            r: _parse_radar(
                _payload(r, f"https://s3.example/{r}.png{url_suffix}", timestamp), r
            )
            for r in ranges
        }
    )


def test_radar_camera_ids_attrs_and_availability():
    data = _data("70km")
    present = SingaporeRainRadarCamera(_coordinator(data), "entry1", "70km")
    missing = SingaporeRainRadarCamera(_coordinator(data), "entry1", "480km")

    assert present.unique_id == "entry1_rain_radar_70km"
    assert present._attr_translation_placeholders == {"range": "70 km"}
    assert present.content_type == "image/png"
    assert present.available is True
    attrs = present.extra_state_attributes
    assert attrs["range"] == "70 km"
    assert attrs["image_timestamp"] == "2026-11-13T22:25:00+08:00"
    assert attrs["north"] == pytest.approx(1.97854)
    assert attrs["east"] == pytest.approx(104.602315)
    assert missing.available is False
    assert missing.extra_state_attributes == {"range": "480 km"}
    # Shares the Weather device with the area weather entities.
    assert present.device_info["identifiers"] == {("singapore", "entry1_weather")}


@pytest.mark.asyncio
async def test_radar_image_cached_until_capture_time_changes():
    coordinator = _coordinator(RadarData())
    camera = SingaporeRainRadarCamera(coordinator, "entry1", "240km")
    camera.hass = MagicMock()
    session = _session({})
    session.get = MagicMock(return_value=_response(body=b"png1"))

    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=session,
    ):
        assert await camera.async_camera_image() is None  # no data yet

        coordinator.data = _data("240km")
        assert await camera.async_camera_image() == b"png1"
        # Same capture, freshly re-signed URL: no re-download.
        coordinator.data = _data("240km", url_suffix="?sig=2")
        assert await camera.async_camera_image() == b"png1"
        assert session.get.call_count == 1

        coordinator.data = _data("240km", timestamp="2026-11-13T22:30:00+08:00")
        await camera.async_camera_image()
        assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_radar_image_download_failure_returns_cached():
    coordinator = _coordinator(_data("70km"))
    camera = SingaporeRainRadarCamera(coordinator, "entry1", "70km")
    camera.hass = MagicMock()

    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=MagicMock(get=MagicMock(return_value=_response(body=b"ok"))),
    ):
        assert await camera.async_camera_image() == b"ok"

    coordinator.data = _data("70km", timestamp="2026-11-13T22:30:00+08:00")
    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=MagicMock(get=MagicMock(return_value=_response(status=403))),
    ):
        assert await camera.async_camera_image() == b"ok"
