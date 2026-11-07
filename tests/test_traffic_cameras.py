"""Tests for the checkpoint traffic camera coordinator and camera entities."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.camera import SingaporeCheckpointCamera
from custom_components.singapore.traffic_coordinator import (
    CHECKPOINT_CAMERAS,
    TrafficImagesCoordinator,
    TrafficImagesData,
    UpdateFailed,
    _parse_traffic_images,
)


def _cam(camera_id: str, image: str = "https://images.data.gov.sg/x.jpg") -> dict:
    return {
        "timestamp": "2026-11-07T16:15:38+08:00",
        "image": image,
        "location": {"latitude": 1.447, "longitude": 103.771},
        "camera_id": camera_id,
        "image_metadata": {"height": 1080, "width": 1920, "md5": "abc"},
    }


def _payload(*cams) -> dict:
    return {
        "items": [{"timestamp": "2026-11-07T16:18:38+08:00", "cameras": list(cams)}],
        "api_info": {"status": "healthy"},
    }


def test_parse_keeps_only_checkpoint_cameras():
    data = _parse_traffic_images(
        _payload(_cam("2701"), _cam("4713"), _cam("1001"), _cam("4798"))
    )
    assert set(data.cameras) == {"2701", "4713"}
    cam = data.cameras["2701"]
    assert cam.image_url == "https://images.data.gov.sg/x.jpg"
    assert cam.latitude == pytest.approx(1.447)
    assert cam.timestamp.isoformat() == "2026-11-07T16:15:38+08:00"


def test_parse_bad_payload_raises():
    with pytest.raises(UpdateFailed):
        _parse_traffic_images({"items": []})


def test_checkpoint_cameras_cover_woodlands_and_tuas():
    assert {v[1] for v in CHECKPOINT_CAMERAS.values()} == {"Woodlands", "Tuas"}


def _session(status: int = 200, json_payload=None, body: bytes = b"jpeg"):
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=json_payload)
    response.read = AsyncMock(return_value=body)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


@pytest.mark.asyncio
async def test_coordinator_fetch_and_http_error_fallback():
    coordinator = TrafficImagesCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.traffic_coordinator.async_get_clientsession",
        return_value=_session(json_payload=_payload(_cam("4703"))),
    ):
        await coordinator.async_refresh()
    assert set(coordinator.data.cameras) == {"4703"}

    with patch(
        "custom_components.singapore.traffic_coordinator.async_get_clientsession",
        return_value=_session(status=429),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert set(coordinator.data.cameras) == {"4703"}


@pytest.mark.asyncio
async def test_coordinator_http_error_without_cache_fails():
    coordinator = TrafficImagesCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.traffic_coordinator.async_get_clientsession",
        return_value=_session(status=503),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    return coordinator


def test_camera_entity_ids_attrs_and_availability():
    data = _parse_traffic_images(_payload(_cam("2701")))
    present = SingaporeCheckpointCamera(_coordinator(data), "entry1", "2701")
    missing = SingaporeCheckpointCamera(_coordinator(data), "entry1", "2702")

    assert present.unique_id == "entry1_traffic_camera_2701"
    assert present._attr_translation_placeholders == {"location": "Woodlands Causeway"}
    assert present.available is True
    assert present.extra_state_attributes["checkpoint"] == "Woodlands"
    assert present.extra_state_attributes["image_url"].endswith("x.jpg")
    assert missing.available is False
    assert present.device_info["identifiers"] == {
        ("singapore", "entry1_traffic_cameras")
    }


@pytest.mark.asyncio
async def test_camera_image_cached_until_url_changes():
    coordinator = _coordinator(TrafficImagesData())
    camera = SingaporeCheckpointCamera(coordinator, "entry1", "4713")
    camera.hass = MagicMock()
    session = _session(body=b"img1")

    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=session,
    ):
        assert await camera.async_camera_image() is None  # no data yet

        coordinator.data = _parse_traffic_images(_payload(_cam("4713", "https://a")))
        assert await camera.async_camera_image() == b"img1"
        assert await camera.async_camera_image() == b"img1"
        assert session.get.call_count == 1

        coordinator.data = _parse_traffic_images(_payload(_cam("4713", "https://b")))
        await camera.async_camera_image()
        assert session.get.call_count == 2
