"""Camera platform: LTA checkpoint traffic cameras and NEA rain radar images."""

from __future__ import annotations

import logging
import time

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SingaporeConfigEntry
from .const import DOMAIN
from .radar_coordinator import RADAR_RANGES, RadarCoordinator, RadarImage
from .radar_image import BASEMAPS, composite_radar
from .traffic_coordinator import CHECKPOINT_CAMERAS, TrafficImagesCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# How long to serve the bare radar overlay before trying NEA's base map again.
BASEMAP_RETRY_SECONDS = 30 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the checkpoint traffic cameras and the rain radar cameras."""
    coordinator = entry.runtime_data.traffic
    radar = entry.runtime_data.radar
    async_add_entities(
        [
            *(
                SingaporeCheckpointCamera(coordinator, entry.entry_id, camera_id)
                for camera_id in CHECKPOINT_CAMERAS
            ),
            *(
                SingaporeRainRadarCamera(radar, entry.entry_id, radar_range)
                for radar_range in RADAR_RANGES
            ),
        ]
    )


async def _download_image(hass: HomeAssistant, url: str, label: str) -> bytes | None:
    """Download an image, returning None (with a warning) on any failure."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if response.status != 200:
                _LOGGER.warning("%s image returned HTTP %s", label, response.status)
                return None
            return await response.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Error fetching %s image: %s", label, err)
        return None


class SingaporeCheckpointCamera(CoordinatorEntity[TrafficImagesCoordinator], Camera):
    """Latest LTA still image for a Woodlands / Tuas checkpoint camera."""

    _attr_has_entity_name = True
    _attr_translation_key = "checkpoint_camera"
    _attr_attribution = "Data provided by LTA via data.gov.sg"

    def __init__(
        self, coordinator: TrafficImagesCoordinator, entry_id: str, camera_id: str
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._entry_id = entry_id
        self._camera_id = camera_id
        name, self._checkpoint, self._description = CHECKPOINT_CAMERAS[camera_id]
        self._attr_unique_id = f"{entry_id}_traffic_camera_{camera_id}"
        self._attr_translation_placeholders = {"location": name}
        self._cached_url: str | None = None
        self._cached_image: bytes | None = None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._camera_id in self.coordinator.data.cameras
        )

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {
            "camera_id": self._camera_id,
            "checkpoint": self._checkpoint,
            "description": self._description,
        }
        data = self.coordinator.data
        cam = data.cameras.get(self._camera_id) if data is not None else None
        if cam is not None:
            attrs["image_url"] = cam.image_url
            attrs["image_timestamp"] = (
                cam.timestamp.isoformat() if cam.timestamp else None
            )
            attrs["latitude"] = cam.latitude
            attrs["longitude"] = cam.longitude
        return attrs

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest still, re-downloading only when the URL changes."""
        data = self.coordinator.data
        cam = data.cameras.get(self._camera_id) if data is not None else None
        if cam is None:
            return self._cached_image
        if cam.image_url == self._cached_url and self._cached_image is not None:
            return self._cached_image

        image = await _download_image(
            self.hass, cam.image_url, f"Traffic camera {self._camera_id}"
        )
        if image is None:
            return self._cached_image

        self._cached_url = cam.image_url
        self._cached_image = image
        return image

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_traffic_cameras")},
            name="Traffic Cameras",
            manufacturer="Singapore",
            model="LTA Traffic Images",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=(
                "https://data.gov.sg/datasets/d_6cdb6b405b25aaaacbaf7689bcc6fae0/view"
            ),
        )


class SingaporeRainRadarCamera(CoordinatorEntity[RadarCoordinator], Camera):
    """Latest NEA rain radar image for one radar range.

    For the 70 km and 240 km ranges the transparent radar PNG is drawn on NEA's own
    rain-areas base map (fetched once from nea.gov.sg and kept in memory). The 480 km
    range has no NEA map, so it is the bare overlay; so is any range while the base
    map can't be fetched.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "rain_radar"
    _attr_attribution = "Data provided by NEA via data.gov.sg; map by NEA"

    def __init__(
        self, coordinator: RadarCoordinator, entry_id: str, radar_range: str
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self.content_type = "image/png"
        self._entry_id = entry_id
        self._range = radar_range
        self._attr_unique_id = f"{entry_id}_rain_radar_{radar_range}"
        self._attr_translation_placeholders = {"range": RADAR_RANGES[radar_range]}
        self._basemap_spec = BASEMAPS.get(radar_range)
        self._basemap: bytes | None = None
        self._basemap_failed_at: float | None = None
        self._cached_key: object = None
        self._cached_image: bytes | None = None

    @property
    def _radar(self) -> RadarImage | None:
        data = self.coordinator.data
        return data.images.get(self._range) if data is not None else None

    @property
    def available(self) -> bool:
        return super().available and self._radar is not None

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {
            "range": RADAR_RANGES[self._range],
            "basemap": self._basemap_spec is not None,
        }
        radar = self._radar
        if radar is not None:
            attrs["image_timestamp"] = (
                radar.timestamp.isoformat() if radar.timestamp else None
            )
            attrs["north"] = radar.north
            attrs["south"] = radar.south
            attrs["east"] = radar.east
            attrs["west"] = radar.west
            attrs["center_latitude"] = radar.center_latitude
            attrs["center_longitude"] = radar.center_longitude
        return attrs

    async def _async_get_basemap(self) -> bytes | None:
        """Return NEA's base map for this range, fetching it at most once.

        A failed fetch is retried after ``BASEMAP_RETRY_SECONDS``, not on every
        image request.
        """
        if self._basemap_spec is None or self._basemap is not None:
            return self._basemap
        if (
            self._basemap_failed_at is not None
            and time.monotonic() - self._basemap_failed_at < BASEMAP_RETRY_SECONDS
        ):
            return None
        self._basemap = await _download_image(
            self.hass, self._basemap_spec.url, f"Rain radar {self._range} base map"
        )
        if self._basemap is None:
            self._basemap_failed_at = time.monotonic()
        return self._basemap

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest radar image, on NEA's base map where there is one.

        The presigned URL changes on every poll, so the cache is keyed by the
        capture timestamp (falling back to the URL if NEA omits it), plus whether
        the base map was available.
        """
        radar = self._radar
        if radar is None:
            return self._cached_image
        basemap = await self._async_get_basemap()
        key = (radar.timestamp or radar.image_url, basemap is not None)
        if key == self._cached_key and self._cached_image is not None:
            return self._cached_image

        overlay = await _download_image(
            self.hass, radar.image_url, f"Rain radar {self._range}"
        )
        if overlay is None:
            return self._cached_image

        image, content_type = overlay, "image/png"
        if basemap is not None and self._basemap_spec is not None:
            try:
                image = await self.hass.async_add_executor_job(
                    composite_radar, basemap, overlay, self._basemap_spec
                )
                content_type = "image/jpeg"
            except OSError as err:  # PIL.UnidentifiedImageError is an OSError
                _LOGGER.warning(
                    "Could not draw rain radar %s on its base map: %s", self._range, err
                )
                # A corrupt base map would fail every time: fetch it again later.
                self._basemap = None
                self._basemap_failed_at = time.monotonic()
                key = (key[0], False)

        self.content_type = content_type
        self._cached_key = key
        self._cached_image = image
        return image

    @property
    def device_info(self) -> DeviceInfo:
        # Same device as the weather entities and readings sensors.
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_weather")},
            name="Weather",
            manufacturer="Singapore",
            model="NEA Weather",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.nea.gov.sg",
        )
