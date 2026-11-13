"""Data coordinator for the NEA hourly UV index (data.gov.sg real-time API)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

UV_URL = "https://api-open.data.gov.sg/v2/real-time/api/uv"
# NEA publishes one reading per hour from 7 am to 7 pm, about 10 minutes after
# the hour; polling every 15 minutes picks each one up within the quarter hour.
UPDATE_INTERVAL = timedelta(minutes=15)

# NEA's UV exposure categories (from the WHO UV index scale), upper bound inclusive.
UV_EXPOSURE_BANDS: tuple[tuple[int, str], ...] = (
    (2, "low"),
    (5, "moderate"),
    (7, "high"),
    (10, "very_high"),
)
UV_EXPOSURE_OPTIONS = ["low", "moderate", "high", "very_high", "extreme"]

_SGT = timezone(timedelta(hours=8))


def uv_exposure(value: float | None) -> str | None:
    """Map a UV index to NEA's exposure category."""
    if value is None:
        return None
    for upper, band in UV_EXPOSURE_BANDS:
        if value <= upper:
            return band
    return "extreme"


@dataclass(frozen=True)
class UvReading:
    """The UV index for one hour."""

    hour: datetime
    value: int

    def as_dict(self) -> dict:
        return {"hour": self.hour.isoformat(), "value": self.value}


@dataclass
class UvData:
    """Today's hourly UV readings, oldest first."""

    readings: list[UvReading] = field(default_factory=list)

    @property
    def latest(self) -> UvReading | None:
        return self.readings[-1] if self.readings else None

    @property
    def peak(self) -> UvReading | None:
        # Earliest hour wins a tie, so the peak doesn't move once reached.
        return max(self.readings, key=lambda r: r.value, default=None)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_SGT)


def _parse_uv(payload: dict) -> UvData:
    """Parse hourly UV readings.

    Supports:
    - v2: ``data.records[0].index[{hour, value}]`` (newest first)
    - v1: ``items[0].index[{timestamp, value}]``

    Raises ``UpdateFailed`` if no record with an ``index`` list is present.
    """
    records = None
    if isinstance(payload, dict):
        data = payload.get("data")
        records = data.get("records") if isinstance(data, dict) else None
        if records is None:
            records = payload.get("items")
    if not isinstance(records, list) or not records:
        raise UpdateFailed("UV payload has no records")
    record = records[0] if isinstance(records[0], dict) else {}
    index = record.get("index")
    if not isinstance(index, list):
        raise UpdateFailed("UV payload has no index list")

    readings: dict[datetime, UvReading] = {}
    for row in index:
        if not isinstance(row, dict):
            continue
        hour = _parse_time(row.get("hour") or row.get("timestamp"))
        try:
            value = int(round(float(row.get("value"))))
        except (TypeError, ValueError):
            continue
        if hour is not None and value >= 0:
            readings[hour] = UvReading(hour=hour, value=value)
    return UvData(readings=sorted(readings.values(), key=lambda r: r.hour))


class UvCoordinator(DataUpdateCoordinator[UvData]):
    """Polls NEA's hourly UV index."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore NEA UV Index",
            update_interval=UPDATE_INTERVAL,
            # Parsed data is a dataclass, so unchanged polls skip state writes.
            always_update=False,
        )

    async def _async_update_data(self) -> UvData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                UV_URL, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"data.gov.sg UV index returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching UV index (%s); using last known values", err
                )
                return self.data
            raise UpdateFailed(f"Error fetching UV index: {err}") from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching UV index (%s); using last known values", err
                )
                return self.data
            raise
        return _parse_uv(payload)
