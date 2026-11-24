"""LTA DataMall EV charger availability coordinator.

Uses the official LTA DataMall EV charging endpoints with the user's optional
``AccountKey`` header:
- ``EVChargingPoints?PostalCode=<code>`` when postal codes are configured
- ``EVCBatch`` (pre-signed JSON link) filtered to chargers near the Home
  Assistant home location when no postal codes are configured

LTA updates EV charger availability every 5 minutes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    SOURCE_EV_CHARGERS,
    ev_postal_codes,
    lta_datamall_headers,
    scaled_interval,
)
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

EV_CHARGING_POINTS_URL = (
    "https://datamall2.mytransport.sg/ltaodataservice/EVChargingPoints"
)
EV_BATCH_URL = "https://datamall2.mytransport.sg/ltaodataservice/EVCBatch"
UPDATE_INTERVAL = timedelta(minutes=5)
NEARBY_RADIUS_KM = 2.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class EvLocation:
    """Aggregated EV charger availability at one location."""

    location_id: str
    name: str
    address: str
    available: int
    total: int
    operators: list[str] = field(default_factory=list)
    plug_types: list[str] = field(default_factory=list)
    max_power_kw: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float | None = None


@dataclass
class EvChargerData:
    """Summary of tracked or nearby EV charging locations."""

    available_chargers: int = 0
    total_chargers: int = 0
    locations: list[EvLocation] = field(default_factory=list)
    nearest_available: EvLocation | None = None


def _is_available(status: Any) -> bool:
    return status in (1, "1")


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unwrap(payload: Any) -> Any:
    """Strip DataMall's OData envelope (``{"odata.metadata": ..., "value": ...}``)."""
    if isinstance(payload, dict) and "value" in payload:
        return payload["value"]
    return payload


def batch_link(payload: Any) -> str | None:
    """Return the download link from an ``EVCBatch`` answer.

    DataMall answers ``{"value": [{"Link": "..."}]}``, like its other batch
    endpoints; a bare ``{"Link": ...}`` or ``{"value": {"Link": ...}}`` is
    accepted too.
    """
    value = _unwrap(payload)
    if isinstance(value, list):
        value = value[0] if value else None
    link = value.get("Link") if isinstance(value, dict) else None
    return link if isinstance(link, str) and link else None


def parse_ev_locations(
    payload: Any,
    *,
    home_lat: float | None = None,
    home_lon: float | None = None,
    max_distance_km: float | None = None,
) -> list[EvLocation]:
    """Parse an LTA DataMall ``evLocationsData`` payload.

    Accepts the bare object and DataMall's ``{"value": {...}}`` envelope.
    """
    payload = _unwrap(payload)
    if not isinstance(payload, dict) or not isinstance(
        payload.get("evLocationsData"), list
    ):
        raise source_error(
            ERROR_PAYLOAD, SOURCE_EV_CHARGERS, detail="missing evLocationsData list"
        )

    locations: list[EvLocation] = []
    for raw in payload["evLocationsData"]:
        if not isinstance(raw, dict):
            continue
        lat = _parse_float(raw.get("latitude"))
        # LTA's official schema spells this field "longtitude"; accept both.
        lon = _parse_float(raw.get("longtitude", raw.get("longitude")))
        distance: float | None = None
        if (
            home_lat is not None
            and home_lon is not None
            and lat is not None
            and lon is not None
        ):
            distance = _haversine_km(home_lat, home_lon, lat, lon)
            if max_distance_km is not None and distance > max_distance_km:
                continue

        available = 0
        total = 0
        operators: list[str] = []
        plug_types: list[str] = []
        max_kw: float | None = None

        for cp in raw.get("chargingPoints") or []:
            if not isinstance(cp, dict):
                continue
            if (op := str(cp.get("operator", "")).strip()) and op not in operators:
                operators.append(op)
            plugs = cp.get("plugTypes") or []
            if not plugs:
                total += 1
                if _is_available(cp.get("status")):
                    available += 1
                continue
            for plug in plugs:
                if not isinstance(plug, dict):
                    continue
                if (
                    pt := str(plug.get("plugType", "")).strip()
                ) and pt not in plug_types:
                    plug_types.append(pt)
                speed = _parse_float(plug.get("chargingSpeed"))
                if speed is not None and (max_kw is None or speed > max_kw):
                    max_kw = speed
                ev_ids = plug.get("evIds") or []
                if ev_ids:
                    for ev_id in ev_ids:
                        if not isinstance(ev_id, dict):
                            continue
                        total += 1
                        if _is_available(ev_id.get("status")):
                            available += 1
                else:
                    total += 1
                    if _is_available(cp.get("status")):
                        available += 1

        locations.append(
            EvLocation(
                location_id=str(raw.get("locationId", "")),
                name=str(raw.get("name", "")).strip(),
                address=str(raw.get("address", "")).strip(),
                available=available,
                total=total,
                operators=operators,
                plug_types=plug_types,
                max_power_kw=max_kw,
                latitude=lat,
                longitude=lon,
                distance_km=round(distance, 2) if distance is not None else None,
            )
        )

    locations.sort(
        key=lambda loc: (
            loc.distance_km if loc.distance_km is not None else float("inf"),
            loc.name,
        )
    )
    return locations


def summarize_ev_locations(locations: list[EvLocation]) -> EvChargerData:
    """Build EvChargerData summary from parsed locations."""
    available_total = sum(loc.available for loc in locations)
    total = sum(loc.total for loc in locations)
    nearest_avail = next((loc for loc in locations if loc.available > 0), None)
    return EvChargerData(
        available_chargers=available_total,
        total_chargers=total,
        locations=locations,
        nearest_available=nearest_avail,
    )


class EvChargerCoordinator(DataUpdateCoordinator[EvChargerData]):
    """Fetches EV charger availability from LTA DataMall."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="LTA EV charger availability",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
        )
        self.last_updated: datetime | None = None

    async def _async_update_data(self) -> EvChargerData:
        headers = lta_datamall_headers(self.config_entry)
        if not headers:
            raise source_error(
                ERROR_FETCH, SOURCE_EV_CHARGERS, error="LTA DataMall API key not set"
            )

        session = async_get_clientsession(self.hass)
        home_lat = getattr(self.hass.config, "latitude", None)
        home_lon = getattr(self.hass.config, "longitude", None)
        postal_codes = ev_postal_codes(self.config_entry)

        locations: list[EvLocation] = []
        if postal_codes:
            for postal in postal_codes:
                try:
                    async with session.get(
                        EV_CHARGING_POINTS_URL,
                        params={"PostalCode": postal},
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        if response.status != 200:
                            raise source_error(
                                ERROR_HTTP, SOURCE_EV_CHARGERS, status=response.status
                            )
                        payload = await response.json(content_type=None)
                except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                    raise source_error(
                        ERROR_FETCH, SOURCE_EV_CHARGERS, error=err
                    ) from err
                locations.extend(
                    parse_ev_locations(payload, home_lat=home_lat, home_lon=home_lon)
                )
        else:
            try:
                async with session.get(
                    EV_BATCH_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        raise source_error(
                            ERROR_HTTP, SOURCE_EV_CHARGERS, status=response.status
                        )
                    meta = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise source_error(ERROR_FETCH, SOURCE_EV_CHARGERS, error=err) from err

            link = batch_link(meta)
            if not link:
                raise source_error(
                    ERROR_PAYLOAD, SOURCE_EV_CHARGERS, detail="missing batch Link"
                )

            try:
                async with session.get(
                    link, timeout=aiohttp.ClientTimeout(total=60)
                ) as batch_resp:
                    if batch_resp.status != 200:
                        raise source_error(
                            ERROR_HTTP, SOURCE_EV_CHARGERS, status=batch_resp.status
                        )
                    payload = await batch_resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise source_error(ERROR_FETCH, SOURCE_EV_CHARGERS, error=err) from err

            locations = parse_ev_locations(
                payload,
                home_lat=home_lat,
                home_lon=home_lon,
                max_distance_km=NEARBY_RADIUS_KM,
            )

        self.last_updated = datetime.now(UTC)
        return summarize_ev_locations(locations)
