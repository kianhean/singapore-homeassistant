"""Data coordinator for NEA Wet Bulb Globe Temperature (heat stress) readings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_WBGT, data_gov_headers, scaled_interval
from .datagov import data_gov_get
from .dengue_coordinator import _haversine_km
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

WBGT_URL = "https://api-open.data.gov.sg/v2/real-time/api/weather?api=wbgt"
# NEA publishes a reading per station every 15 minutes.
UPDATE_INTERVAL = timedelta(minutes=15)

# NEA's heat stress categories: below 31 °C low, 31 to below 33 moderate,
# 33 and above high. The payload carries the category; this is the fallback.
HEAT_STRESS_OPTIONS = ["low", "moderate", "high"]
_HEAT_STRESS_BANDS: tuple[tuple[float, str], ...] = ((31.0, "low"), (33.0, "moderate"))

_SGT = timezone(timedelta(hours=8))


def heat_stress(value: float | None) -> str | None:
    """NEA's heat stress category for a WBGT reading (°C)."""
    if value is None:
        return None
    for below, band in _HEAT_STRESS_BANDS:
        if value < below:
            return band
    return "high"


@dataclass(frozen=True)
class WbgtReading:
    """One station's WBGT reading."""

    station_id: str
    name: str
    town: str | None
    latitude: float
    longitude: float
    wbgt: float
    heat_stress: str

    def distance_km(self, lat: float, lon: float) -> float:
        return _haversine_km(lat, lon, self.latitude, self.longitude)


@dataclass
class WbgtData:
    """The latest WBGT reading at every station."""

    readings: list[WbgtReading] = field(default_factory=list)
    timestamp: datetime | None = None

    def nearest(self, lat: float, lon: float) -> tuple[WbgtReading, float] | None:
        """(reading, distance_km) for the station closest to a point."""
        best: tuple[WbgtReading, float] | None = None
        for reading in self.readings:
            dist = reading.distance_km(lat, lon)
            if best is None or dist < best[1]:
                best = (reading, dist)
        return best

    @property
    def highest(self) -> WbgtReading | None:
        return max(self.readings, key=lambda r: r.wbgt, default=None)


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_SGT)


def _parse_wbgt(payload: dict) -> WbgtData:
    """Parse ``data.records[0].item.readings[]``.

    Each reading has ``station{id, name, townCenter}``, ``location{latitude,
    longitude}`` (strings), ``wbgt`` (string, °C) and ``heatStress``
    (``Low`` / ``Moderate`` / ``High``). Raises ``UpdateFailed`` when there is
    no readings list; an empty one is valid.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list) or not records:
        raise source_error(ERROR_PAYLOAD, SOURCE_WBGT, detail="no records")
    record = records[0] if isinstance(records[0], dict) else {}
    item = record.get("item") if isinstance(record.get("item"), dict) else {}
    rows = item.get("readings")
    if not isinstance(rows, list):
        raise source_error(ERROR_PAYLOAD, SOURCE_WBGT, detail="no readings list")

    readings: list[WbgtReading] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = row.get("station") if isinstance(row.get("station"), dict) else {}
        location = row.get("location") if isinstance(row.get("location"), dict) else {}
        lat = _to_float(location.get("latitude"))
        lon = _to_float(location.get("longitude"))
        value = _to_float(row.get("wbgt"))
        if lat is None or lon is None or value is None:
            continue
        stress = str(row.get("heatStress") or "").strip().lower()
        if stress not in HEAT_STRESS_OPTIONS:
            stress = heat_stress(value) or "low"
        readings.append(
            WbgtReading(
                station_id=str(station.get("id") or ""),
                name=str(station.get("name") or station.get("id") or ""),
                town=station.get("townCenter") or None,
                latitude=lat,
                longitude=lon,
                wbgt=value,
                heat_stress=stress,
            )
        )
    return WbgtData(readings=readings, timestamp=_parse_time(record.get("datetime")))


class WbgtCoordinator(DataUpdateCoordinator[WbgtData]):
    """Polls NEA's WBGT station readings."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore NEA Heat Stress",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> WbgtData:
        session = async_get_clientsession(self.hass)
        try:
            async with data_gov_get(
                self.hass,
                session,
                WBGT_URL,
                headers=data_gov_headers(self.config_entry),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(ERROR_HTTP, SOURCE_WBGT, status=response.status)
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching WBGT (%s); using last known values", err
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_WBGT, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching WBGT (%s); using last known values", err
                )
                return self.data
            raise
        return _parse_wbgt(payload)
