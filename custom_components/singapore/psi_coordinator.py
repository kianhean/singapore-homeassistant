"""Data coordinator for Singapore NEA Pollutant Standards Index (PSI) readings."""

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

PSI_URL = "https://api-open.data.gov.sg/v2/real-time/api/psi"
# NEA publishes PSI hourly; polling every 30 minutes picks up a new hour promptly
# without hammering data.gov.sg.
UPDATE_INTERVAL = timedelta(minutes=30)

PSI_REGIONS: Final[tuple[str, ...]] = ("north", "south", "east", "west", "central")

# Label locations NEA uses for each region; used when the payload's
# regionMetadata is missing a region.
DEFAULT_REGION_LOCATIONS: Final[dict[str, tuple[float, float]]] = {
    "north": (1.41803, 103.82),
    "south": (1.29587, 103.82),
    "east": (1.35735, 103.94),
    "west": (1.35735, 103.7),
    "central": (1.35735, 103.82),
}

# NEA PSI health bands (upper bound inclusive).
_PSI_BANDS: Final[tuple[tuple[float, str], ...]] = (
    (50, "good"),
    (100, "moderate"),
    (200, "unhealthy"),
    (300, "very_unhealthy"),
)
PSI_BAND_HAZARDOUS: Final = "hazardous"


def psi_band(value: float | None) -> str | None:
    """Map a 24-hour PSI value to NEA's descriptor band."""
    if value is None:
        return None
    for upper, band in _PSI_BANDS:
        if value <= upper:
            return band
    return PSI_BAND_HAZARDOUS


@dataclass
class PsiData:
    """Parsed PSI payload.

    ``readings`` maps every numeric reading key in the payload (e.g.
    ``psi_twenty_four_hourly``, ``pm25_sub_index``) to a dict of region -> value.
    ``national`` holds the Singapore-wide figure per reading key: the API's
    ``national`` value when present (v1 payloads), else the highest regional
    value, matching how NEA headlines the PSI. ``region_locations`` maps each
    region to its (latitude, longitude) label location.
    """

    readings: dict[str, dict[str, float]] = field(default_factory=dict)
    national: dict[str, float] = field(default_factory=dict)
    timestamp: datetime | None = None
    region_locations: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_REGION_LOCATIONS)
    )


class PsiCoordinator(DataUpdateCoordinator[PsiData]):
    """Fetches and caches Singapore PSI readings from data.gov.sg."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore NEA PSI",
            update_interval=UPDATE_INTERVAL,
            # Parsed data is a dataclass, so unchanged polls skip state writes.
            always_update=False,
        )

    async def _async_update_data(self) -> PsiData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                PSI_URL, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"data.gov.sg PSI endpoint returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching PSI data (%s); using last known values", err
                )
                return self.data
            raise UpdateFailed(f"Error fetching PSI data: {err}") from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching PSI data (%s); using last known values", err
                )
                return self.data
            raise

        parsed = _parse_psi(payload)
        if "psi_twenty_four_hourly" not in parsed.national:
            raise UpdateFailed("No PSI readings found in data.gov.sg payload")
        return parsed


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_psi(payload: dict) -> PsiData:
    """Parse the PSI API payload.

    Supports both shapes:
    - v2: ``{"data": {"items": [{"timestamp", "readings": {key: {region: v}}}]}}``
    - v1: ``{"items": [...]}`` with an extra ``national`` region per reading.
    """
    if not isinstance(payload, dict):
        return PsiData()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    items = payload.get("items") or data.get("items")
    if not items or not isinstance(items[0], dict):
        return PsiData()
    item = items[0]
    raw_readings = item.get("readings")
    if not isinstance(raw_readings, dict):
        return PsiData()

    readings: dict[str, dict[str, float]] = {}
    national: dict[str, float] = {}
    for key, by_region in raw_readings.items():
        if not isinstance(by_region, dict):
            continue
        regional: dict[str, float] = {}
        for region in PSI_REGIONS:
            val = _to_float(by_region.get(region))
            if val is not None:
                regional[region] = val
        if regional:
            readings[key] = regional
        national_val = _to_float(by_region.get("national"))
        if national_val is None and regional:
            national_val = max(regional.values())
        if national_val is not None:
            national[key] = national_val

    return PsiData(
        readings=readings,
        national=national,
        timestamp=_parse_timestamp(item.get("timestamp")),
        region_locations=_parse_region_locations(
            data.get("regionMetadata") or payload.get("region_metadata")
        ),
    )


def _parse_region_locations(metadata) -> dict[str, tuple[float, float]]:
    """Read region label locations, falling back to NEA's known defaults."""
    locations = dict(DEFAULT_REGION_LOCATIONS)
    if not isinstance(metadata, list):
        return locations
    for row in metadata:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip().lower()
        loc = row.get("labelLocation") or row.get("label_location") or {}
        lat = _to_float(loc.get("latitude"))
        lon = _to_float(loc.get("longitude"))
        if name in locations and lat is not None and lon is not None:
            locations[name] = (lat, lon)
    return locations
