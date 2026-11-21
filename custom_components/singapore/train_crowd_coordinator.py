"""LTA DataMall real-time MRT/LRT station platform crowd density coordinator.

Uses the official LTA DataMall ``PCDRealTime`` endpoint with the user's
optional ``AccountKey`` header:
https://datamall2.mytransport.sg/ltaodataservice/PCDRealTime?TrainLine=<code>

LTA updates platform crowd density every 10 minutes during operating hours.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    SOURCE_TRAIN_CROWD,
    lta_datamall_headers,
    scaled_interval,
    train_crowd_stations,
)
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

PCD_REALTIME_URL = "https://datamall2.mytransport.sg/ltaodataservice/PCDRealTime"
UPDATE_INTERVAL = timedelta(minutes=10)

CROWD_LEVEL_MAP: dict[str, str] = {
    "l": "low",
    "m": "moderate",
    "h": "high",
}
CROWD_LEVEL_OPTIONS: list[str] = ["low", "moderate", "high"]

# Station code prefix -> LTA DataMall TrainLine query value.
STATION_PREFIX_TO_LINE: dict[str, str] = {
    "EW": "EWL",
    "CG": "CGL",
    "NS": "NSL",
    "NE": "NEL",
    "CC": "CCL",
    "CE": "CEL",
    "DT": "DTL",
    "TE": "TEL",
    "BP": "BPL",
    "SE": "SLRT",
    "SW": "SLRT",
    "STC": "SLRT",
    "PE": "PLRT",
    "PW": "PLRT",
    "PTC": "PLRT",
}
_STATION_PREFIX_RE = re.compile(r"^([A-Z]+)")


def station_to_train_line(station_code: str) -> str | None:
    """Return the LTA DataMall TrainLine code for a station code (e.g. EW13 -> EWL)."""
    match = _STATION_PREFIX_RE.match(station_code.strip().upper())
    if not match:
        return None
    prefix = match.group(1)
    return STATION_PREFIX_TO_LINE.get(prefix)


@dataclass
class StationCrowd:
    """Real-time platform crowd density for a single MRT/LRT station."""

    station: str
    line: str
    crowd_level: str | None  # "low", "moderate", "high", or None when NA
    start_time: str | None = None
    end_time: str | None = None


@dataclass
class TrainCrowdData:
    """Real-time platform crowd density keyed by station code."""

    stations: dict[str, StationCrowd] = field(default_factory=dict)


def parse_pcd_realtime(payload: Any, line: str) -> dict[str, StationCrowd]:
    """Parse one PCDRealTime JSON response into StationCrowd records."""
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
        raise source_error(
            ERROR_PAYLOAD, SOURCE_TRAIN_CROWD, detail="missing value list"
        )
    result: dict[str, StationCrowd] = {}
    for item in payload["value"]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("Station", "")).strip().upper()
        if not code:
            continue
        raw_level = str(item.get("CrowdLevel", "")).strip().lower()
        result[code] = StationCrowd(
            station=code,
            line=line,
            crowd_level=CROWD_LEVEL_MAP.get(raw_level),
            start_time=item.get("StartTime"),
            end_time=item.get("EndTime"),
        )
    return result


class TrainCrowdCoordinator(DataUpdateCoordinator[TrainCrowdData]):
    """Fetches real-time MRT/LRT station crowd density from LTA DataMall."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="LTA train crowd density",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
        )
        self.last_updated: datetime | None = None

    async def _async_update_data(self) -> TrainCrowdData:
        headers = lta_datamall_headers(self.config_entry)
        if not headers:
            raise source_error(
                ERROR_FETCH, SOURCE_TRAIN_CROWD, error="LTA DataMall API key not set"
            )

        stations = train_crowd_stations(self.config_entry)
        lines: list[str] = []
        for code in stations:
            if (line := station_to_train_line(code)) and line not in lines:
                lines.append(line)

        if not lines:
            return TrainCrowdData()

        session = async_get_clientsession(self.hass)
        combined: dict[str, StationCrowd] = {}
        for line in lines:
            try:
                async with session.get(
                    PCD_REALTIME_URL,
                    params={"TrainLine": line},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        raise source_error(
                            ERROR_HTTP, SOURCE_TRAIN_CROWD, status=response.status
                        )
                    payload = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise source_error(ERROR_FETCH, SOURCE_TRAIN_CROWD, error=err) from err
            combined.update(parse_pcd_realtime(payload, line))

        self.last_updated = datetime.now(UTC)
        return TrainCrowdData(stations=combined)
