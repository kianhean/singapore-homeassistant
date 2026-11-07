"""Data coordinator for LTA traffic camera images (Woodlands & Tuas checkpoints)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

TRAFFIC_IMAGES_URL = "https://api.data.gov.sg/v1/transport/traffic-images"
# LTA refreshes camera stills roughly every 1-5 minutes.
UPDATE_INTERVAL = timedelta(minutes=2)

# Only the causeway / second-link checkpoint cameras are exposed.
# camera_id -> (entity name, checkpoint, LTA description)
CHECKPOINT_CAMERAS: Final[dict[str, tuple[str, str, str]]] = {
    "2701": ("Woodlands Causeway", "Woodlands", "Woodlands Causeway (Towards Johor)"),
    "2702": ("Woodlands Checkpoint", "Woodlands", "Woodlands Checkpoint (Towards BKE)"),
    "4703": ("Tuas Second Link", "Tuas", "Tuas Second Link"),
    "4713": ("Tuas Checkpoint", "Tuas", "Tuas Checkpoint"),
}


@dataclass
class TrafficCamera:
    """Latest still for one traffic camera."""

    camera_id: str
    image_url: str
    timestamp: datetime | None
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class TrafficImagesData:
    """Latest images for the checkpoint cameras, keyed by camera_id."""

    cameras: dict[str, TrafficCamera] = field(default_factory=dict)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_traffic_images(payload: dict) -> TrafficImagesData:
    """Pick the checkpoint cameras out of the traffic-images payload.

    Expected shape::

        {"items": [{"timestamp": "...", "cameras": [
            {"camera_id": "2701", "image": "https://...jpg", "timestamp": "...",
             "location": {"latitude": 1.44, "longitude": 103.77}}
        ]}]}

    Raises ``UpdateFailed`` if the envelope is missing. A checkpoint camera that is
    absent from the payload is simply omitted (its entity goes unavailable).
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise UpdateFailed("Unexpected traffic-images payload shape")
    cameras = items[0].get("cameras")
    if not isinstance(cameras, list):
        raise UpdateFailed("Traffic-images payload has no cameras list")

    result: dict[str, TrafficCamera] = {}
    for cam in cameras:
        if not isinstance(cam, dict):
            continue
        camera_id = str(cam.get("camera_id") or "")
        image_url = cam.get("image")
        if camera_id not in CHECKPOINT_CAMERAS or not image_url:
            continue
        location = cam.get("location") or {}
        result[camera_id] = TrafficCamera(
            camera_id=camera_id,
            image_url=str(image_url),
            timestamp=_parse_datetime(cam.get("timestamp")),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
        )
    return TrafficImagesData(cameras=result)


class TrafficImagesCoordinator(DataUpdateCoordinator[TrafficImagesData]):
    """Polls the LTA traffic-images feed for checkpoint camera stills."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore Checkpoint Traffic Cameras",
            update_interval=UPDATE_INTERVAL,
            # Parsed data is a dataclass, so unchanged polls skip state writes.
            always_update=False,
        )

    async def _async_update_data(self) -> TrafficImagesData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                TRAFFIC_IMAGES_URL, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"data.gov.sg traffic images returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching traffic images (%s); using last known values", err
                )
                return self.data
            raise UpdateFailed(f"Error fetching traffic images: {err}") from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching traffic images (%s); using last known values", err
                )
                return self.data
            raise

        return _parse_traffic_images(payload)
