"""Data coordinator for HDB carpark lot availability (data.gov.sg).

Two datasets are combined:

- *Carpark Availability* (``api.data.gov.sg/v1/transport/carpark-availability``),
  polled: lots available / total per carpark number and lot type, every minute
  upstream. It carries no location.
- *HDB Carpark Information* (CKAN ``d_23f946fa557947f93a8043bbef41dd09``),
  fetched once a day: address, type, free/night parking and SVY21 coordinates
  (converted to WGS84 here), used to name the carparks and find the ones
  nearest home.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import SOURCE_CARPARK, carpark_numbers, data_gov_headers, scaled_interval
from .datagov import data_gov_get
from .dengue_coordinator import _haversine_km
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

CARPARK_AVAILABILITY_URL = "https://api.data.gov.sg/v1/transport/carpark-availability"
CARPARK_INFO_DATASET_ID = "d_23f946fa557947f93a8043bbef41dd09"
CARPARK_INFO_URL = (
    "https://data.gov.sg/api/action/datastore_search"
    f"?resource_id={CARPARK_INFO_DATASET_ID}&limit=5000"
)
# Upstream refreshes every minute, but the payload covers all ~2,000 carparks
# (~300 KB, not compressed), so poll every 5 minutes.
UPDATE_INTERVAL = timedelta(minutes=5)
INFO_MAX_AGE = timedelta(hours=24)
# Carparks tracked when none are chosen in the options.
NEAREST_CARPARKS = 3

# Lot type codes in the availability feed.
LOT_CAR = "C"
LOT_MOTORCYCLE = "Y"
LOT_HEAVY = "H"
LOT_TYPE_NAMES = {LOT_CAR: "car", LOT_MOTORCYCLE: "motorcycle", LOT_HEAVY: "heavy"}

_SGT = timezone(timedelta(hours=8))

# --- SVY21 (EPSG:3414) -> WGS84 ---------------------------------------------
# Transverse Mercator on WGS84, after the Singapore Land Authority's
# parameters (the widely used cgcai/SVY21 port of the SLA formulas).
_A = 6378137.0
_F = 1 / 298.257223563
_ORIGIN_LAT = 1.366666
_ORIGIN_LON = 103.833333
_FALSE_NORTHING = 38744.572
_FALSE_EASTING = 28001.642
_K = 1.0
_B = _A * (1 - _F)
_E2 = 2 * _F - _F * _F
_E4 = _E2 * _E2
_E6 = _E4 * _E2
_A0 = 1 - _E2 / 4 - 3 * _E4 / 64 - 5 * _E6 / 256
_A2 = 3.0 / 8.0 * (_E2 + _E4 / 4 + 15 * _E6 / 128)
_A4 = 15.0 / 256.0 * (_E4 + 3 * _E6 / 4)
_A6 = 35 * _E6 / 3072


def _meridian_distance(lat: float) -> float:
    rad = math.radians(lat)
    return _A * (
        _A0 * rad
        - _A2 * math.sin(2 * rad)
        + _A4 * math.sin(4 * rad)
        - _A6 * math.sin(6 * rad)
    )


def svy21_to_wgs84(northing: float, easting: float) -> tuple[float, float]:
    """Convert SVY21 grid coordinates (metres) to (latitude, longitude)."""
    n = (_A - _B) / (_A + _B)
    n2, n3, n4 = n**2, n**3, n**4
    g = _A * (1 - n) * (1 - n2) * (1 + 9 * n2 / 4 + 225 * n4 / 64) * (math.pi / 180)
    m_prime = _meridian_distance(_ORIGIN_LAT) + (northing - _FALSE_NORTHING) / _K
    sigma = m_prime * math.pi / (180 * g)
    lat_p = (
        sigma
        + (3 * n / 2 - 27 * n3 / 32) * math.sin(2 * sigma)
        + (21 * n2 / 16 - 55 * n4 / 32) * math.sin(4 * sigma)
        + (151 * n3 / 96) * math.sin(6 * sigma)
        + (1097 * n4 / 512) * math.sin(8 * sigma)
    )
    sin2 = math.sin(lat_p) ** 2
    rho = _A * (1 - _E2) / (1 - _E2 * sin2) ** 1.5
    nu = _A / math.sqrt(1 - _E2 * sin2)
    psi = nu / rho
    psi2, psi3, psi4 = psi**2, psi**3, psi**4
    t = math.tan(lat_p)
    t2, t4, t6 = t**2, t**4, t**6
    e_prime = easting - _FALSE_EASTING
    x = e_prime / (_K * nu)
    x3, x5, x7 = x**3, x**5, x**7

    lat_factor = t / (_K * rho)
    lat = (
        lat_p
        - lat_factor * (e_prime * x / 2)
        + lat_factor * (e_prime * x3 / 24) * (-4 * psi2 + 9 * psi * (1 - t2) + 12 * t2)
        - lat_factor
        * (e_prime * x5 / 720)
        * (
            8 * psi4 * (11 - 24 * t2)
            - 12 * psi3 * (21 - 71 * t2)
            + 15 * psi2 * (15 - 98 * t2 + 15 * t4)
            + 180 * psi * (5 * t2 - 3 * t4)
            + 360 * t4
        )
        + lat_factor
        * (e_prime * x7 / 40320)
        * (1385 - 3633 * t2 + 4095 * t4 + 1575 * t6)
    )
    sec = 1 / math.cos(lat_p)
    lon = (
        math.radians(_ORIGIN_LON)
        + x * sec
        - (x3 * sec / 6) * (psi + 2 * t2)
        + (x5 * sec / 120)
        * (-4 * psi3 * (1 - 6 * t2) + psi2 * (9 - 68 * t2) + 72 * psi * t2 + 24 * t4)
        - (x7 * sec / 5040) * (61 + 662 * t2 + 1320 * t4 + 720 * t6)
    )
    return math.degrees(lat), math.degrees(lon)


# --- Data ----------------------------------------------------------------------


@dataclass(frozen=True)
class CarparkInfo:
    """Static details of an HDB carpark."""

    number: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    car_park_type: str | None = None
    parking_system: str | None = None
    short_term_parking: str | None = None
    free_parking: str | None = None
    night_parking: str | None = None
    gantry_height: float | None = None

    def distance_km(self, lat: float, lon: float) -> float | None:
        if self.latitude is None or self.longitude is None:
            return None
        return _haversine_km(lat, lon, self.latitude, self.longitude)


@dataclass(frozen=True)
class LotCount:
    """Lots available / total for one lot type."""

    available: int
    total: int


@dataclass(frozen=True)
class CarparkAvailability:
    """One carpark's lot counts per lot type (``LOT_CAR`` etc.)."""

    number: str
    lots: dict[str, LotCount]
    updated: datetime | None = None


@dataclass
class CarparkData:
    """Availability for the tracked carparks, plus their details."""

    # Carpark numbers to show, in order (chosen in the options, else nearest home).
    tracked: list[str] = field(default_factory=list)
    availability: dict[str, CarparkAvailability] = field(default_factory=dict)
    info: dict[str, CarparkInfo] = field(default_factory=dict)
    timestamp: datetime | None = None
    # Total carparks in the feed (diagnostics / sanity).
    carpark_count: int = 0


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # update_datetime has no offset; it is Singapore time.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_SGT)


def _parse_availability(
    payload: dict,
) -> tuple[dict[str, CarparkAvailability], datetime | None]:
    """Parse ``items[0].carpark_data[]``.

    Each carpark has ``carpark_number``, ``update_datetime`` and
    ``carpark_info[{lot_type, lots_available, total_lots}]``. Raises
    ``UpdateFailed`` when there is no carpark list.
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    item = items[0] if isinstance(items, list) and items else None
    rows = item.get("carpark_data") if isinstance(item, dict) else None
    if not isinstance(rows, list) or not rows:
        raise source_error(ERROR_PAYLOAD, SOURCE_CARPARK, detail="no carpark_data")

    carparks: dict[str, CarparkAvailability] = {}
    for row in rows:
        if not isinstance(row, dict) or not (
            number := _text(row.get("carpark_number"))
        ):
            continue
        lots: dict[str, LotCount] = {}
        for info in row.get("carpark_info") or ():
            if not isinstance(info, dict):
                continue
            lot_type = _text(info.get("lot_type"))
            available = _to_int(info.get("lots_available"))
            total = _to_int(info.get("total_lots"))
            if lot_type and available is not None and total is not None:
                # Some carparks list a lot type twice; add them up.
                if (seen := lots.get(lot_type)) is not None:
                    available, total = seen.available + available, seen.total + total
                lots[lot_type] = LotCount(available, total)
        carparks[number.upper()] = CarparkAvailability(
            number.upper(), lots, _parse_time(row.get("update_datetime"))
        )
    return carparks, _parse_time(item.get("timestamp"))


def _parse_info(payload: dict) -> dict[str, CarparkInfo]:
    """Parse the HDB carpark information CKAN result (SVY21 -> WGS84)."""
    result = payload.get("result") if isinstance(payload, dict) else None
    records = result.get("records") if isinstance(result, dict) else None
    if not isinstance(records, list) or not records:
        raise source_error(ERROR_PAYLOAD, SOURCE_CARPARK, detail="no carpark records")
    info: dict[str, CarparkInfo] = {}
    for row in records:
        if not isinstance(row, dict) or not (number := _text(row.get("car_park_no"))):
            continue
        x = _to_float(row.get("x_coord"))
        y = _to_float(row.get("y_coord"))
        lat = lon = None
        if x is not None and y is not None and x > 0 and y > 0:
            lat, lon = svy21_to_wgs84(y, x)
        info[number.upper()] = CarparkInfo(
            number=number.upper(),
            address=_text(row.get("address")),
            latitude=None if lat is None else round(lat, 6),
            longitude=None if lon is None else round(lon, 6),
            car_park_type=_text(row.get("car_park_type")),
            parking_system=_text(row.get("type_of_parking_system")),
            short_term_parking=_text(row.get("short_term_parking")),
            free_parking=_text(row.get("free_parking")),
            night_parking=_text(row.get("night_parking")),
            gantry_height=_to_float(row.get("gantry_height")),
        )
    return info


def nearest_carparks(
    info: dict[str, CarparkInfo],
    lat: float,
    lon: float,
    count: int,
    available: set[str] | None = None,
) -> list[str]:
    """The ``count`` carpark numbers closest to a point.

    With ``available``, only carparks that report availability are considered
    (the information dataset lists some carparks the live feed never has).
    """
    located = [
        (dist, number)
        for number, carpark in info.items()
        if (available is None or number in available)
        and (dist := carpark.distance_km(lat, lon)) is not None
    ]
    located.sort()
    return [number for _, number in located[:count]]


class CarparkCoordinator(DataUpdateCoordinator[CarparkData]):
    """Polls HDB carpark availability for the chosen (or nearest) carparks."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore HDB Carpark Availability",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )
        self._chosen = carpark_numbers(config_entry)
        self._info: dict[str, CarparkInfo] = {}
        self._info_fetched: datetime | None = None

    async def _get_json(self, url: str) -> dict:
        session = async_get_clientsession(self.hass)
        async with data_gov_get(
            self.hass,
            session,
            url,
            headers=data_gov_headers(self.config_entry),
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status != 200:
                raise source_error(ERROR_HTTP, SOURCE_CARPARK, status=response.status)
            return await response.json(content_type=None)

    async def _async_refresh_info(self) -> None:
        """Re-read carpark details when missing or a day old (failures are soft)."""
        now = dt_util.utcnow()
        if (
            self._info
            and self._info_fetched
            and now - self._info_fetched < INFO_MAX_AGE
        ):
            return
        try:
            self._info = _parse_info(await self._get_json(CARPARK_INFO_URL))
            self._info_fetched = now
        except (aiohttp.ClientError, TimeoutError, UpdateFailed) as err:
            # Availability still works without it; only names, locations and
            # the "nearest home" choice need it. Retried on the next poll.
            _LOGGER.warning("Could not fetch HDB carpark information: %s", err)

    async def _async_update_data(self) -> CarparkData:
        await self._async_refresh_info()
        try:
            payload = await self._get_json(CARPARK_AVAILABILITY_URL)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching carpark availability (%s); using last known values",
                    err,
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_CARPARK, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching carpark availability (%s); using last known values",
                    err,
                )
                return self.data
            raise

        availability, timestamp = _parse_availability(payload)
        if self._chosen:
            tracked = list(self._chosen)
        else:
            tracked = nearest_carparks(
                self._info,
                self.hass.config.latitude,
                self.hass.config.longitude,
                NEAREST_CARPARKS,
                set(availability),
            )
        return CarparkData(
            tracked=tracked,
            availability={n: availability[n] for n in tracked if n in availability},
            info={n: self._info[n] for n in tracked if n in self._info},
            timestamp=timestamp,
            carpark_count=len(availability),
        )
