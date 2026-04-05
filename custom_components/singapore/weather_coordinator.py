"""Data coordinator for Singapore NEA 2-hour area forecasts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

WEATHER_URL = "https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast"
UPDATE_INTERVAL = timedelta(minutes=30)

_READINGS_ENDPOINTS = {
    "temperature": "https://api.data.gov.sg/v1/environment/air-temperature",
    "humidity": "https://api.data.gov.sg/v1/environment/relative-humidity",
    "wind_speed": "https://api.data.gov.sg/v1/environment/wind-speed",
    "wind_bearing": "https://api.data.gov.sg/v1/environment/wind-direction",
    "precipitation": "https://api.data.gov.sg/v1/environment/rainfall",
}


@dataclass
class WeatherReadings:
    """Realtime readings (collection 1459) aggregated across stations."""

    temperature: float | None = None
    humidity: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    precipitation: float | None = None


@dataclass
class WeatherAreaData:
    """Per-area weather forecast data."""

    area: str
    condition_text: str
    valid_start: datetime
    valid_end: datetime


@dataclass
class WeatherData:
    """Parsed weather payload for all areas."""

    areas: dict[str, WeatherAreaData]
    updated_at: datetime | None
    readings: WeatherReadings


class SingaporeWeatherCoordinator(DataUpdateCoordinator[WeatherData]):
    """Fetches and caches Singapore 2-hour area weather forecasts."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Singapore NEA Weather",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> WeatherData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(WEATHER_URL, timeout=30) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"data.gov.sg weather endpoint returned HTTP {response.status}"
                    )
                payload = await response.json()
            parsed = _parse_weather(payload)
            if not parsed.areas:
                raise UpdateFailed("No area forecasts found in weather payload")

            parsed.readings = await _fetch_aggregated_readings(session)
            return parsed
        except Exception as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching weather data (%s); using last known values", err
                )
                return self.data
            raise UpdateFailed(f"Error fetching weather data: {err}") from err


async def _fetch_aggregated_readings(session) -> WeatherReadings:
    values: dict[str, float | None] = {}
    for key, url in _READINGS_ENDPOINTS.items():
        values[key] = await _fetch_reading_average(session, url, key)
    return WeatherReadings(**values)


async def _fetch_reading_average(session, url: str, metric_key: str) -> float | None:
    """Return station-average reading for a metric endpoint.

    Handles common payload variants where the metric value may be located as:
    - items[0].readings[].value
    - data.items[0].readings[].value
    - data.readings[]
    """
    try:
        async with session.get(url, timeout=20) as response:
            if response.status != 200:
                _LOGGER.debug("Skipping %s due to HTTP %s", metric_key, response.status)
                return None
            payload = await response.json()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Skipping %s due to fetch error: %s", metric_key, err)
        return None

    rows = _extract_readings_rows(payload)
    numeric_values: list[float] = []
    for row in rows:
        val = _to_float(row.get("value"))
        if val is not None:
            numeric_values.append(val)

    if not numeric_values:
        return None
    return round(sum(numeric_values) / len(numeric_values), 2)


def _extract_readings_rows(payload: dict) -> list[dict]:
    items = payload.get("items") or payload.get("data", {}).get("items") or []
    if items and isinstance(items[0], dict):
        rows = items[0].get("readings")
        if isinstance(rows, list):
            return rows

    rows = payload.get("data", {}).get("readings")
    if isinstance(rows, list):
        return rows

    return []


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_weather(payload: dict) -> WeatherData:
    """Parse weather API payload.

    Supports both common shapes:
    - Legacy v1: items[0].forecasts[{area, forecast}] + valid_period
    - Newer shape: data.records[0].periods[0].regions map
    """
    updated_at = _parse_iso_datetime(payload.get("api_info", {}).get("status"))

    areas: dict[str, WeatherAreaData] = {}

    # Shape A: items[] list with forecasts[] objects containing area/forecast.
    items = payload.get("items") or payload.get("data", {}).get("items") or []
    if items:
        item0 = items[0]
        valid_period = item0.get("valid_period") or item0.get("validPeriod") or {}
        start = _parse_iso_datetime(valid_period.get("start"))
        end = _parse_iso_datetime(valid_period.get("end"))

        if start is None:
            start = _parse_iso_datetime(item0.get("timestamp"))
        if start is None:
            start = datetime.now(timezone.utc)
        if end is None:
            end = start + timedelta(hours=2)

        for row in item0.get("forecasts", []):
            area = (row.get("area") or "").strip()
            cond = (row.get("forecast") or "").strip()
            if area and cond:
                areas[area] = WeatherAreaData(
                    area=area,
                    condition_text=cond,
                    valid_start=start,
                    valid_end=end,
                )

        if areas:
            return WeatherData(
                areas=areas,
                updated_at=updated_at,
                readings=WeatherReadings(),
            )

    # Shape B: data.records[0].periods[0].regions mapping.
    records = payload.get("data", {}).get("records", [])
    if records:
        record0 = records[0]
        periods = record0.get("periods", [])
        if periods:
            period0 = periods[0]
            time_period = period0.get("timePeriod") or period0.get("time_period") or {}
            start = _parse_iso_datetime(time_period.get("start")) or datetime.now(
                timezone.utc
            )
            end = _parse_iso_datetime(time_period.get("end")) or (
                start + timedelta(hours=2)
            )
            regions = period0.get("regions", {})
            for area, cond in regions.items():
                area_name = str(area).strip().title()
                condition = str(cond).strip()
                if area_name and condition:
                    areas[area_name] = WeatherAreaData(
                        area=area_name,
                        condition_text=condition,
                        valid_start=start,
                        valid_end=end,
                    )

    return WeatherData(areas=areas, updated_at=updated_at, readings=WeatherReadings())
