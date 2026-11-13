"""Tests for the NEA rain radar coordinator and camera entities."""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from PIL import Image

from custom_components.singapore.camera import (
    BASEMAP_RETRY_SECONDS,
    SingaporeRainRadarCamera,
)
from custom_components.singapore.radar_coordinator import (
    RADAR_RANGES,
    RADAR_URL,
    RadarCoordinator,
    RadarData,
    UpdateFailed,
    _parse_radar,
)
from custom_components.singapore.radar_image import BASEMAPS, composite_radar


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
    assert attrs["basemap"] is True
    assert attrs["image_timestamp"] == "2026-11-13T22:25:00+08:00"
    assert attrs["north"] == pytest.approx(1.97854)
    assert attrs["east"] == pytest.approx(104.602315)
    assert missing.available is False
    assert missing.extra_state_attributes == {"range": "480 km", "basemap": False}
    # Shares the Weather device with the area weather entities.
    assert present.device_info["identifiers"] == {("singapore", "entry1_weather")}


def _png(size, pixels=None, mode="RGBA") -> bytes:
    """A tiny image: transparent (or black) with the given {(x, y): colour} pixels."""
    image = Image.new(mode, size)
    for xy, colour in (pixels or {}).items():
        image.putpixel(xy, colour)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


RED = (255, 0, 0, 255)
OVERLAY = _png((480, 480), {(83 + 108, 193 + 60): RED, (10, 10): RED})
BASE_70 = _png((853, 479), mode="RGB")
BASE_240 = _png((853, 853), mode="RGB")


def test_composite_radar_uses_nea_crop_and_scale():
    out = composite_radar(BASE_70, OVERLAY, BASEMAPS["70km"])
    image = Image.open(io.BytesIO(out))
    assert image.format == "JPEG"
    assert image.size == (853, 479)
    # Crop pixel (108, 60) of 217 px wide, scaled to 853 px: about (425, 236).
    r, g, b = image.getpixel((426, 237))
    assert r > 100 and g < 60 and b < 60
    # (10, 10) of the radar image lies outside NEA's 70 km crop.
    assert max(image.getpixel((5, 5))) < 30


def test_composite_radar_full_frame_for_240km():
    out = composite_radar(BASE_240, OVERLAY, BASEMAPS["240km"])
    image = Image.open(io.BytesIO(out))
    assert image.size == (853, 853)
    # (10, 10) * 853 / 480 is about (18, 18); one pixel blurs when scaled, so
    # look at the neighbourhood.
    reds = [image.getpixel((x, y))[0] for x in range(16, 22) for y in range(16, 22)]
    assert max(reds) > 100


def test_basemaps_cover_70_and_240_only():
    assert set(BASEMAPS) == {"70km", "240km"}
    assert BASEMAPS["70km"].url == (
        "https://www.nea.gov.sg/assets/images/map/base-853.png"
    )


def _routed_session(routes: dict):
    """Session answering GET per URL with bytes (200) or an int status."""
    session = MagicMock()

    def _get(url, **_kwargs):
        result = routes[url]
        if isinstance(result, int):
            return _response(status=result)
        return _response(body=result)

    session.get = MagicMock(side_effect=_get)
    return session


def _hass():
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
    return hass


def _urls(session) -> list[str]:
    return [call.args[0] for call in session.get.call_args_list]


@pytest.mark.asyncio
async def test_radar_480km_is_bare_overlay_cached_by_capture_time():
    coordinator = _coordinator(RadarData())
    camera = SingaporeRainRadarCamera(coordinator, "entry1", "480km")
    camera.hass = _hass()
    session = _routed_session({"https://s3.example/480km.png": b"png1"})
    session.get.side_effect = None
    session.get.return_value = _response(body=b"png1")

    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=session,
    ):
        assert await camera.async_camera_image() is None  # no data yet

        coordinator.data = _data("480km")
        assert await camera.async_camera_image() == b"png1"
        assert camera.content_type == "image/png"
        assert camera.extra_state_attributes["basemap"] is False
        # Same capture, freshly re-signed URL: no re-download.
        coordinator.data = _data("480km", url_suffix="?sig=2")
        assert await camera.async_camera_image() == b"png1"
        assert session.get.call_count == 1

        coordinator.data = _data("480km", timestamp="2026-11-13T22:30:00+08:00")
        await camera.async_camera_image()
        assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_radar_70km_drawn_on_nea_basemap_fetched_once():
    coordinator = _coordinator(_data("70km"))
    camera = SingaporeRainRadarCamera(coordinator, "entry1", "70km")
    camera.hass = _hass()
    session = _routed_session(
        {
            BASEMAPS["70km"].url: BASE_70,
            "https://s3.example/70km.png": OVERLAY,
        }
    )

    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=session,
    ):
        image = await camera.async_camera_image()
        assert Image.open(io.BytesIO(image)).size == (853, 479)
        assert camera.content_type == "image/jpeg"
        assert camera.extra_state_attributes["basemap"] is True

        coordinator.data = _data("70km", timestamp="2026-11-13T22:30:00+08:00")
        await camera.async_camera_image()

    assert _urls(session).count(BASEMAPS["70km"].url) == 1
    assert _urls(session).count("https://s3.example/70km.png") == 2


@pytest.mark.asyncio
async def test_radar_basemap_failure_serves_overlay_and_retries_later():
    coordinator = _coordinator(_data("240km"))
    camera = SingaporeRainRadarCamera(coordinator, "entry1", "240km")
    camera.hass = _hass()
    routes = {BASEMAPS["240km"].url: 503, "https://s3.example/240km.png": OVERLAY}
    session = _routed_session(routes)
    clock = [1000.0]

    with (
        patch(
            "custom_components.singapore.camera.async_get_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.singapore.camera.time.monotonic",
            side_effect=lambda: clock[0],
        ),
    ):
        assert await camera.async_camera_image() == OVERLAY
        assert camera.content_type == "image/png"
        # Within the retry window the base map isn't requested again.
        await camera.async_camera_image()
        assert _urls(session).count(BASEMAPS["240km"].url) == 1

        routes[BASEMAPS["240km"].url] = BASE_240
        clock[0] += BASEMAP_RETRY_SECONDS + 1
        image = await camera.async_camera_image()

    assert Image.open(io.BytesIO(image)).size == (853, 853)
    assert camera.content_type == "image/jpeg"
    assert _urls(session).count(BASEMAPS["240km"].url) == 2


@pytest.mark.asyncio
async def test_radar_corrupt_basemap_falls_back_to_overlay():
    coordinator = _coordinator(_data("70km"))
    camera = SingaporeRainRadarCamera(coordinator, "entry1", "70km")
    camera.hass = _hass()
    session = _routed_session(
        {
            BASEMAPS["70km"].url: b"<html>not an image</html>",
            "https://s3.example/70km.png": OVERLAY,
        }
    )
    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=session,
    ):
        assert await camera.async_camera_image() == OVERLAY
    assert camera.content_type == "image/png"
    assert camera._basemap is None


@pytest.mark.asyncio
async def test_radar_image_download_failure_returns_cached():
    coordinator = _coordinator(_data("480km"))
    camera = SingaporeRainRadarCamera(coordinator, "entry1", "480km")
    camera.hass = _hass()

    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=MagicMock(get=MagicMock(return_value=_response(body=b"ok"))),
    ):
        assert await camera.async_camera_image() == b"ok"

    coordinator.data = _data("480km", timestamp="2026-11-13T22:30:00+08:00")
    with patch(
        "custom_components.singapore.camera.async_get_clientsession",
        return_value=MagicMock(get=MagicMock(return_value=_response(status=403))),
    ):
        assert await camera.async_camera_image() == b"ok"
