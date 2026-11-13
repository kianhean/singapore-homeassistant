"""Data coordinator for NEA weather (rain) radar images from data.gov.sg."""

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

RADAR_URL = "https://api-open.data.gov.sg/v2/real-time/api/weather-radar-images/{range}"
# NEA publishes a new radar image every 5 minutes. The image URLs are presigned
# and expire ~20 minutes after the API call, so polling also keeps them fresh.
UPDATE_INTERVAL = timedelta(minutes=5)

# Radar range (API path segment) -> entity name suffix.
RADAR_RANGES: Final[dict[str, str]] = {
    "70km": "70 km",
    "240km": "240 km",
    "480km": "480 km",
}


@dataclass
class RadarImage:
    """Latest radar image for one range."""

    range: str
    image_url: str
    timestamp: datetime | None
    url_expires_at: datetime | None = None
    # Bounding box of the image in EPSG:4326.
    north: float | None = None
    south: float | None = None
    east: float | None = None
    west: float | None = None
    center_latitude: float | None = None
    center_longitude: float | None = None


@dataclass
class RadarData:
    """Latest radar images keyed by range (``70km`` / ``240km`` / ``480km``)."""

    images: dict[str, RadarImage] = field(default_factory=dict)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_radar(payload: dict, radar_range: str) -> RadarImage:
    """Parse one weather-radar-images response.

    Expected shape::

        {"code": 0, "data": {
            "projection": {"center": {"longitude": 103.97, "latitude": 1.35}},
            "boundaryBox": {"upperLeft": {"longitude": .., "latitude": ..},
                            "lowerRight": {"longitude": .., "latitude": ..}},
            "records": [{"timestamp": "...", "image": {"url": "https://...png",
                                                       "urlExpiresAt": "..."}}]}}

    Raises ``UpdateFailed`` when there is no record with an image URL.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list) or not records:
        raise UpdateFailed(f"Radar {radar_range} payload has no records")
    record = records[0]
    image = record.get("image") if isinstance(record, dict) else None
    url = image.get("url") if isinstance(image, dict) else None
    if not isinstance(url, str) or not url:
        raise UpdateFailed(f"Radar {radar_range} payload has no image URL")

    box = data.get("boundaryBox") or {}
    upper_left = box.get("upperLeft") or {}
    lower_right = box.get("lowerRight") or {}
    center = (data.get("projection") or {}).get("center") or {}
    return RadarImage(
        range=radar_range,
        image_url=url,
        timestamp=_parse_datetime(record.get("timestamp")),
        url_expires_at=_parse_datetime(image.get("urlExpiresAt")),
        north=_float(upper_left.get("latitude")),
        south=_float(lower_right.get("latitude")),
        east=_float(lower_right.get("longitude")),
        west=_float(upper_left.get("longitude")),
        center_latitude=_float(center.get("latitude")),
        center_longitude=_float(center.get("longitude")),
    )


class RadarCoordinator(DataUpdateCoordinator[RadarData]):
    """Polls the latest NEA radar image for each range."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore Rain Radar",
            update_interval=UPDATE_INTERVAL,
        )

    async def _fetch_range(
        self, session: aiohttp.ClientSession, radar_range: str
    ) -> dict:
        async with session.get(
            RADAR_URL.format(range=radar_range),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status != 200:
                raise UpdateFailed(
                    f"data.gov.sg radar {radar_range} returned HTTP {response.status}"
                )
            return await response.json(content_type=None)

    async def _async_update_data(self) -> RadarData:
        session = async_get_clientsession(self.hass)
        previous = self.data.images if self.data is not None else {}
        images: dict[str, RadarImage] = {}
        errors: list[str] = []

        # Sequential on purpose: data.gov.sg rate-limits bursts (HTTP 429).
        for radar_range in RADAR_RANGES:
            try:
                payload = await self._fetch_range(session, radar_range)
            except (aiohttp.ClientError, TimeoutError, UpdateFailed) as err:
                # Transient fetch problem: keep this range's last image, if any.
                errors.append(f"{radar_range}: {err}")
                if radar_range in previous:
                    images[radar_range] = previous[radar_range]
                continue
            # Parse failures propagate so a changed payload is reported.
            images[radar_range] = _parse_radar(payload, radar_range)

        if errors:
            if not images:
                raise UpdateFailed(f"Error fetching radar images: {'; '.join(errors)}")
            _LOGGER.warning(
                "Error fetching radar images (%s); using last known values",
                "; ".join(errors),
            )
        return RadarData(images=images)
