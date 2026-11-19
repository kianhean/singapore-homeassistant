"""Data coordinator for NEA hawker centre closures (cleaning and other works)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_HAWKER, data_gov_headers, scaled_interval
from .datagov import data_gov_get
from .dengue_coordinator import _haversine_km
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

HAWKER_DATASET_ID = "d_bda4baa634dd1cc7a6c7cad5f19e2d68"
HAWKER_URL = (
    "https://data.gov.sg/api/action/datastore_search"
    f"?resource_id={HAWKER_DATASET_ID}&limit=1000"
)
# NEA publishes the year's quarterly cleaning dates up front and amends them
# rarely; once a day is plenty.
UPDATE_INTERVAL = timedelta(hours=24)

# The calendar shows the closures of this many centres nearest home.
NEAREST_CENTRES = 5

CLOSURE_CLEANING = "cleaning"
CLOSURE_OTHER_WORKS = "other_works"

# (start field, end field, remarks field, kind) per closure in a row.
_CLOSURE_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    *(
        (
            f"q{q}_cleaningstartdate",
            f"q{q}_cleaningenddate",
            f"remarks_q{q}",
            CLOSURE_CLEANING,
        )
        for q in range(1, 5)
    ),
    (
        "other_works_startdate",
        "other_works_enddate",
        "remarks_other_works",
        CLOSURE_OTHER_WORKS,
    ),
)
# Placeholder values NEA uses for "no date" / "no remarks".
_BLANK = {"", "na", "nil", "tbc", "-", "n.a.", "n/a"}


@dataclass(frozen=True)
class HawkerClosure:
    """One closure; ``end`` is the last closed day (inclusive)."""

    kind: str
    start: date
    end: date
    remarks: str | None = None


@dataclass(frozen=True)
class HawkerCentre:
    """A hawker centre and its closures this year."""

    name: str
    address: str | None
    latitude: float | None
    longitude: float | None
    closures: tuple[HawkerClosure, ...] = ()

    def distance_km(self, lat: float, lon: float) -> float | None:
        if self.latitude is None or self.longitude is None:
            return None
        return _haversine_km(lat, lon, self.latitude, self.longitude)


@dataclass
class HawkerData:
    """Every hawker centre in NEA's closure dataset."""

    centres: list[HawkerCentre] = field(default_factory=list)

    def nearest(self, lat: float, lon: float, count: int) -> list[HawkerCentre]:
        """The ``count`` centres closest to a point (centres without a location last)."""
        located = [
            (dist, centre)
            for centre in self.centres
            if (dist := centre.distance_km(lat, lon)) is not None
        ]
        located.sort(key=lambda pair: pair[0])
        return [centre for _, centre in located[:count]]


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in _BLANK else text


def _parse_date(value: object) -> date | None:
    """NEA writes dates as ``d/m/yyyy`` (e.g. ``7/12/2026``)."""
    text = _text(value)
    if text is None:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_hawker(payload: dict) -> HawkerData:
    """Parse the CKAN ``datastore_search`` result (``result.records[]``).

    Raises ``UpdateFailed`` when there are no records (the dataset always
    lists every centre, so none means the API changed).
    """
    result = payload.get("result") if isinstance(payload, dict) else None
    records = result.get("records") if isinstance(result, dict) else None
    if not isinstance(records, list) or not records:
        raise source_error(ERROR_PAYLOAD, SOURCE_HAWKER, detail="no records")

    centres: list[HawkerCentre] = []
    for row in records:
        if not isinstance(row, dict) or not (name := _text(row.get("name"))):
            continue
        closures: list[HawkerClosure] = []
        for start_key, end_key, remarks_key, kind in _CLOSURE_FIELDS:
            start = _parse_date(row.get(start_key))
            if start is None:
                continue
            end = _parse_date(row.get(end_key)) or start
            if end < start:
                start, end = end, start
            closures.append(
                HawkerClosure(kind, start, end, _text(row.get(remarks_key)))
            )
        closures.sort(key=lambda c: (c.start, c.end))
        centres.append(
            HawkerCentre(
                name=name,
                address=_text(row.get("address_myenv")),
                latitude=_to_float(row.get("latitude_hc")),
                longitude=_to_float(row.get("longitude_hc")),
                closures=tuple(closures),
            )
        )
    return HawkerData(centres=centres)


class HawkerClosureCoordinator(DataUpdateCoordinator[HawkerData]):
    """Fetches NEA's hawker centre closure dates once a day."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore Hawker Centre Closures",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> HawkerData:
        session = async_get_clientsession(self.hass)
        try:
            async with data_gov_get(
                self.hass,
                session,
                HAWKER_URL,
                headers=data_gov_headers(self.config_entry),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(
                        ERROR_HTTP, SOURCE_HAWKER, status=response.status
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching hawker closures (%s); using last known values", err
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_HAWKER, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching hawker closures (%s); using last known values", err
                )
                return self.data
            raise
        return _parse_hawker(payload)
