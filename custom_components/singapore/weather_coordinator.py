"""Data coordinator for Singapore NEA 2-hour area forecasts."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import niquests
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

WEATHER_URL = "https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast"
FOUR_DAY_URL = "https://api-open.data.gov.sg/v2/real-time/api/four-day-outlook"
UPDATE_INTERVAL = timedelta(minutes=10)

_SGT = timezone(timedelta(hours=8))

_READINGS_ENDPOINTS = {
    "temperature": "https://api.data.gov.sg/v1/environment/air-temperature",
    "humidity": "https://api.data.gov.sg/v1/environment/relative-humidity",
    "wind_speed": "https://api.data.gov.sg/v1/environment/wind-speed",
    "wind_bearing": "https://api.data.gov.sg/v1/environment/wind-direction",
    "precipitation": "https://api.data.gov.sg/v1/environment/rainfall",
}

_READINGS_CONCURRENCY = 2  # cap parallel requests to avoid 429s
_MAX_RETRIES = 3  # retry attempts on 429
_RETRY_BACKOFF_BASE = 2  # base backoff in seconds (doubles per attempt)


_WIND_DIRECTION_DEGREES: dict[str, float] = {
    "N": 0.0,
    "NNE": 22.5,
    "NE": 45.0,
    "ENE": 67.5,
    "E": 90.0,
    "ESE": 112.5,
    "SE": 135.0,
    "SSE": 157.5,
    "S": 180.0,
    "SSW": 202.5,
    "SW": 225.0,
    "WSW": 247.5,
    "W": 270.0,
    "WNW": 292.5,
    "NW": 315.0,
    "NNW": 337.5,
    "CALM": 0.0,
    "VARIABLE": 0.0,
}


def _wind_direction_to_degrees(text: str | None) -> float | None:
    if not text:
        return None
    return _WIND_DIRECTION_DEGREES.get(text.strip().upper())


@dataclass
class WeatherReadings:
    """Realtime readings (collection 1459) aggregated across stations."""

    temperature: float | None = None
    humidity: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    precipitation: float | None = None


@dataclass
class FourDayForecastEntry:
    """One daily forecast from the 4-day outlook API."""

    date: datetime  # midnight SGT, timezone-aware
    condition_text: str
    temp_high: float | None = None
    temp_low: float | None = None
    humidity_high: float | None = None
    humidity_low: float | None = None
    wind_speed_low: float | None = None
    wind_speed_high: float | None = None
    wind_direction: str | None = None


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
    four_day_forecast: list[FourDayForecastEntry] | None = field(default=None)


class SingaporeWeatherCoordinator(DataUpdateCoordinator[WeatherData]):
    """Fetches and caches Singapore 2-hour area weather forecasts."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Singapore NEA Weather",
            update_interval=UPDATE_INTERVAL,
        )
        self._readings_sem = asyncio.Semaphore(_READINGS_CONCURRENCY)

    async def _async_update_data(self) -> WeatherData:
        try:
            async with niquests.AsyncSession() as session:
                two_hr_resp, four_day_resp = await asyncio.gather(
                    session.get(WEATHER_URL, timeout=30),
                    session.get(FOUR_DAY_URL, timeout=30),
                )
                if two_hr_resp.status_code != 200:
                    raise UpdateFailed(
                        f"data.gov.sg weather endpoint returned HTTP {two_hr_resp.status_code}"
                    )
                parsed = _parse_weather(two_hr_resp.json())
                if not parsed.areas:
                    raise UpdateFailed("No area forecasts found in weather payload")
                if four_day_resp.status_code == 200:
                    parsed.four_day_forecast = _parse_four_day(four_day_resp.json())
                else:
                    _LOGGER.warning(
                        "four-day-outlook returned HTTP %s; skipping daily forecast",
                        four_day_resp.status_code,
                    )
                parsed.readings = await _fetch_aggregated_readings(
                    session, self._readings_sem
                )
            return parsed
        except Exception as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching weather data (%s); using last known values", err
                )
                return self.data
            raise UpdateFailed(f"Error fetching weather data: {err}") from err


async def _fetch_with_retry(
    session: niquests.AsyncSession, url: str, timeout: int = 20
) -> niquests.Response:
    """GET url, retrying up to _MAX_RETRIES times on HTTP 429.

    Respects the Retry-After response header when present; falls back to
    exponential backoff (_RETRY_BACKOFF_BASE ** attempt seconds).
    """
    response = await session.get(url, timeout=timeout)
    for attempt in range(_MAX_RETRIES):
        if response.status_code != 429:
            return response
        retry_after = float(
            response.headers.get("Retry-After", _RETRY_BACKOFF_BASE**attempt)
        )
        _LOGGER.debug(
            "429 on %s; waiting %.1fs before retry %d/%d",
            url,
            retry_after,
            attempt + 1,
            _MAX_RETRIES,
        )
        await asyncio.sleep(retry_after)
        response = await session.get(url, timeout=timeout)
    return response  # last response (may still be 429 if all retries exhausted)


async def _fetch_aggregated_readings(
    session: niquests.AsyncSession, sem: asyncio.Semaphore
) -> WeatherReadings:
    keys = list(_READINGS_ENDPOINTS.keys())
    results = await asyncio.gather(
        *(
            _fetch_reading_average(session, sem, _READINGS_ENDPOINTS[k], k)
            for k in keys
        ),
        return_exceptions=True,
    )
    values: dict[str, float | None] = {}
    for key, result in zip(keys, results):
        if isinstance(result, BaseException):
            _LOGGER.debug("Reading %s raised %s; treating as None", key, result)
            values[key] = None
        else:
            values[key] = result
    return WeatherReadings(**values)


async def _fetch_reading_average(
    session: niquests.AsyncSession,
    sem: asyncio.Semaphore,
    url: str,
    metric_key: str,
) -> float | None:
    """Return station-average reading for a metric endpoint.

    Handles common payload variants where the metric value may be located as:
    - items[0].readings[].value
    - data.items[0].readings[].value
    - data.readings[]
    """
    async with sem:
        try:
            response = await _fetch_with_retry(session, url)
            if response.status_code != 200:
                _LOGGER.debug(
                    "Skipping %s due to HTTP %s", metric_key, response.status_code
                )
                return None
            payload = response.json()
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


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date_sgt(value: str | None) -> datetime | None:
    """Parse a bare YYYY-MM-DD string to timezone-aware midnight SGT."""
    if not value:
        return None
    try:
        d = date.fromisoformat(value)
        return datetime(d.year, d.month, d.day, tzinfo=_SGT)
    except (ValueError, TypeError):
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


def _parse_four_day(payload: dict) -> list[FourDayForecastEntry]:
    """Parse four-day-outlook API payload.

    Supports:
    - Shape A: {"items": [{"forecasts": [...]}]}
    - Shape B: {"data": {"records": [...]}}
    """
    # Shape A
    items = payload.get("items") or []
    if items and isinstance(items[0], dict):
        records_raw = items[0].get("forecasts") or []
    else:
        # Shape B
        records_raw = payload.get("data", {}).get("records") or []

    entries: list[FourDayForecastEntry] = []
    for rec in records_raw:
        dt = _parse_date_sgt(rec.get("date"))
        if dt is None:
            continue
        condition_text = (rec.get("forecast") or "").strip()
        if not condition_text:
            continue

        temp = rec.get("temperature") or {}
        rh = rec.get("relative_humidity") or rec.get("relativeHumidity") or {}
        wind = rec.get("wind") or {}
        wind_speed = wind.get("speed") or {}
        wind_dir = (wind.get("direction") or "").strip() or None

        entries.append(
            FourDayForecastEntry(
                date=dt,
                condition_text=condition_text,
                temp_high=_to_float(temp.get("high")),
                temp_low=_to_float(temp.get("low")),
                humidity_high=_to_float(rh.get("high")),
                humidity_low=_to_float(rh.get("low")),
                wind_speed_low=_to_float(wind_speed.get("low")),
                wind_speed_high=_to_float(wind_speed.get("high")),
                wind_direction=wind_dir,
            )
        )
    return entries


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
