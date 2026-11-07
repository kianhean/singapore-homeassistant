"""Camera platform for LTA checkpoint traffic cameras (Woodlands & Tuas)."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SingaporeConfigEntry
from .const import DOMAIN
from .traffic_coordinator import CHECKPOINT_CAMERAS, TrafficImagesCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one camera entity per checkpoint traffic camera."""
    coordinator = entry.runtime_data.traffic
    async_add_entities(
        SingaporeCheckpointCamera(coordinator, entry.entry_id, camera_id)
        for camera_id in CHECKPOINT_CAMERAS
    )


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

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                cam.image_url, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "Traffic camera %s image returned HTTP %s",
                        self._camera_id,
                        response.status,
                    )
                    return self._cached_image
                image = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning(
                "Error fetching traffic camera %s image: %s", self._camera_id, err
            )
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
