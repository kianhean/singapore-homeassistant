"""Data coordinator for LTA taxi availability (free taxis island-wide)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_TAXI, data_gov_headers, scaled_interval
from .datagov import data_gov_get
from .dengue_coordinator import _haversine_km
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

TAXI_AVAILABILITY_URL = "https://api.data.gov.sg/v1/transport/taxi-availability"
# Upstream updates every 30 s; the payload is ~80 KB, so every 5 minutes.
UPDATE_INTERVAL = timedelta(minutes=5)

# Free taxis within this distance of home are the "taxis nearby" state; the
# 500 m and 2 km counts are attributes.
NEARBY_KM = 1.0


@dataclass(frozen=True)
class TaxiData:
    """Free taxis island-wide and around the Home Assistant home location.

    Positions are reduced to counts here (there are thousands), so the state
    machine and diagnostics stay small.
    """

    total: int
    within_500_m: int
    within_1_km: int
    within_2_km: int
    nearest_km: float | None
    timestamp: datetime | None

    @property
    def nearby(self) -> int:
        return self.within_1_km


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_taxis(payload: dict, home_lat: float, home_lon: float) -> TaxiData:
    """Parse the GeoJSON FeatureCollection (one MultiPoint feature).

    Coordinates are ``[lon, lat]``; ``properties.taxi_count`` is the total.
    Raises ``UpdateFailed`` when there is no feature with coordinates.
    """
    features = payload.get("features") if isinstance(payload, dict) else None
    feature = features[0] if isinstance(features, list) and features else None
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, list):
        raise source_error(ERROR_PAYLOAD, SOURCE_TAXI, detail="no taxi coordinates")
    props = (
        feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    )

    within = [0, 0, 0]  # 500 m, 1 km, 2 km
    nearest: float | None = None
    count = 0
    for point in coords:
        try:
            lon, lat = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        count += 1
        dist = _haversine_km(home_lat, home_lon, lat, lon)
        if nearest is None or dist < nearest:
            nearest = dist
        for i, radius in enumerate((0.5, NEARBY_KM, 2.0)):
            if dist <= radius:
                within[i] += 1
    total = props.get("taxi_count")
    return TaxiData(
        total=total if isinstance(total, int) else count,
        within_500_m=within[0],
        within_1_km=within[1],
        within_2_km=within[2],
        nearest_km=None if nearest is None else round(nearest, 3),
        timestamp=_parse_time(props.get("timestamp")),
    )


class TaxiAvailabilityCoordinator(DataUpdateCoordinator[TaxiData]):
    """Polls LTA's free-taxi positions and counts them around home."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore Taxi Availability",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> TaxiData:
        session = async_get_clientsession(self.hass)
        try:
            async with data_gov_get(
                self.hass,
                session,
                TAXI_AVAILABILITY_URL,
                headers=data_gov_headers(self.config_entry),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(ERROR_HTTP, SOURCE_TAXI, status=response.status)
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching taxi availability (%s); using last known values",
                    err,
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_TAXI, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching taxi availability (%s); using last known values",
                    err,
                )
                return self.data
            raise
        return _parse_taxis(
            payload, self.hass.config.latitude, self.hass.config.longitude
        )
