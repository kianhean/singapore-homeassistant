"""LTA DataMall carpark availability for malls and major developments.

``CarParkAvailabilityv2`` (https://datamall2.mytransport.sg/ltaodataservice/
CarParkAvailabilityv2, ``AccountKey`` header) lists the free lots of HDB, URA
and LTA carparks island-wide, 500 rows a page (``$skip``). The ``LTA`` agency's
rows are the malls and developments LTA collects for its electronic signboards
(Orchard, Marina, HarbourFront, Jurong Lake District: ION Orchard, VivoCity,
Suntec City, Jem and so on), whatever their operator. Each row is one lot type
of one carpark::

    {"CarParkID": "1", "Area": "Marina", "Development": "Suntec City",
     "Location": "1.29375 103.85718", "AvailableLots": 442, "LotType": "C",
     "Agency": "LTA"}

HDB rows are skipped (the HDB source covers them, with lot totals). The
options' ``mall_carparks`` list picks carparks by ``CarParkID`` or by a part of
the development's name; empty means the ``NEAREST_MALL_CARPARKS`` LTA carparks
nearest home. Only the tracked carparks are kept in the data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    SOURCE_MALL_CARPARK,
    lta_datamall_headers,
    mall_carpark_terms,
    scaled_interval,
)
from .dengue_coordinator import _haversine_km
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

CARPARK_AVAILABILITY_URL = (
    "https://datamall2.mytransport.sg/ltaodataservice/CarParkAvailabilityv2"
)
PAGE_SIZE = 500
MAX_PAGES = 20
UPDATE_INTERVAL = timedelta(minutes=5)
NEAREST_MALL_CARPARKS = 5

AGENCY_LTA = "LTA"
AGENCY_HDB = "HDB"
LOT_CAR = "C"
# LotType code -> attribute prefix.
LOT_TYPE_NAMES = {"C": "car", "Y": "motorcycle", "H": "heavy_vehicle"}


@dataclass(frozen=True, slots=True)
class MallCarpark:
    """One carpark's free lots, all lot types together."""

    key: str  # "<agency>_<carpark id>", lower-case: the unique id suffix
    carpark_id: str
    agency: str
    development: str
    area: str | None
    latitude: float | None
    longitude: float | None
    lots: dict[str, int]

    @property
    def car_lots(self) -> int | None:
        return self.lots.get(LOT_CAR)

    def distance_km(self, lat: float, lon: float) -> float | None:
        if self.latitude is None or self.longitude is None:
            return None
        return _haversine_km(lat, lon, self.latitude, self.longitude)


@dataclass
class MallCarparkData:
    """The tracked carparks, keyed by ``MallCarpark.key``, in options order."""

    carparks: dict[str, MallCarpark] = field(default_factory=dict)


def _location(value: object) -> tuple[float | None, float | None]:
    parts = str(value or "").split()
    if len(parts) != 2:
        return None, None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def parse_carparks(rows: list) -> dict[str, MallCarpark]:
    """Merge DataMall rows (one per lot type) into carparks; HDB rows skipped."""
    merged: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        agency = str(row.get("Agency") or "").strip().upper()
        carpark_id = str(row.get("CarParkID") or "").strip()
        if not agency or not carpark_id or agency == AGENCY_HDB:
            continue
        key = f"{agency}_{carpark_id}".lower()
        entry = merged.setdefault(
            key,
            {
                "carpark_id": carpark_id,
                "agency": agency,
                "development": str(row.get("Development") or carpark_id).strip(),
                "area": (str(row.get("Area") or "").strip() or None),
                "location": _location(row.get("Location")),
                "lots": {},
            },
        )
        if entry["location"] == (None, None):
            entry["location"] = _location(row.get("Location"))
        lot_type = str(row.get("LotType") or "").strip().upper() or LOT_CAR
        try:
            available = int(row.get("AvailableLots"))
        except (TypeError, ValueError):
            continue
        entry["lots"][lot_type] = entry["lots"].get(lot_type, 0) + available
    return {
        key: MallCarpark(
            key=key,
            carpark_id=value["carpark_id"],
            agency=value["agency"],
            development=value["development"],
            area=value["area"],
            latitude=value["location"][0],
            longitude=value["location"][1],
            lots=value["lots"],
        )
        for key, value in merged.items()
    }


def choose_carparks(
    carparks: dict[str, MallCarpark],
    terms: list[str],
    lat: float | None,
    lon: float | None,
    count: int = NEAREST_MALL_CARPARKS,
) -> dict[str, MallCarpark]:
    """The carparks to track.

    Each term matches a carpark whose ``CarParkID`` it equals or whose
    development name contains it (ignoring case); matches keep the terms'
    order. With no terms, the ``count`` LTA carparks nearest (lat, lon).
    """
    if terms:
        chosen: dict[str, MallCarpark] = {}
        for term in terms:
            folded = term.casefold()
            for carpark in carparks.values():
                if carpark.key in chosen:
                    continue
                if (
                    carpark.carpark_id.casefold() == folded
                    or folded in carpark.development.casefold()
                ):
                    chosen[carpark.key] = carpark
        return chosen
    if lat is None or lon is None:
        return {}
    located = sorted(
        (dist, carpark.key)
        for carpark in carparks.values()
        if carpark.agency == AGENCY_LTA
        and (dist := carpark.distance_km(lat, lon)) is not None
    )
    return {key: carparks[key] for _, key in located[:count]}


class _HttpError(Exception):
    """DataMall answered with an error status (transient)."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class MallCarparkCoordinator(DataUpdateCoordinator[MallCarparkData]):
    """Polls LTA DataMall carpark availability for malls and developments."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="LTA mall carpark availability",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_fetch_rows(self, headers: dict[str, str]) -> list:
        session = async_get_clientsession(self.hass)
        rows: list = []
        for page in range(MAX_PAGES):
            async with session.get(
                CARPARK_AVAILABILITY_URL,
                params={"$skip": str(page * PAGE_SIZE)},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise _HttpError(response.status)
                payload = await response.json(content_type=None)
            value = payload.get("value") if isinstance(payload, dict) else None
            if not isinstance(value, list):
                raise source_error(
                    ERROR_PAYLOAD, SOURCE_MALL_CARPARK, detail="missing value list"
                )
            rows.extend(value)
            if len(value) < PAGE_SIZE:
                break
        return rows

    async def _async_update_data(self) -> MallCarparkData:
        headers = lta_datamall_headers(self.config_entry)
        if not headers:
            raise source_error(
                ERROR_FETCH, SOURCE_MALL_CARPARK, error="LTA DataMall API key not set"
            )
        try:
            rows = await self._async_fetch_rows(headers)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching mall carparks (%s); using last known values", err
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_MALL_CARPARK, error=err) from err
        except _HttpError as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Mall carparks returned HTTP %s; using last known values",
                    err.status,
                )
                return self.data
            raise source_error(
                ERROR_HTTP, SOURCE_MALL_CARPARK, status=err.status
            ) from err
        carparks = parse_carparks(rows)
        if not carparks:
            raise source_error(
                ERROR_PAYLOAD, SOURCE_MALL_CARPARK, detail="no LTA or URA carparks"
            )
        config = self.hass.config
        return MallCarparkData(
            choose_carparks(
                carparks,
                mall_carpark_terms(self.config_entry),
                getattr(config, "latitude", None),
                getattr(config, "longitude", None),
            )
        )
