"""NEA Beach Short-term Water Quality Information (weekly Enterococcus bands).

https://www.nea.gov.sg/beach-water-quality loads its table from
``https://www.nea.gov.sg/api/BeachWaterQuality/GetNeaData/<timestamp>`` (the
timestamp is a cache buster: Unix seconds rounded down to 5 minutes; the API
answers 400 without one). The answer::

    {"data": [{"Week": "09/11/2026 - 15/11/2026",
               "Areas": [{"Area": "Changi Beach 1", "Description": "...",
                          "AdvisoryOnMap": "", "Banding": "1"}, ...]}, ...],
     "areas": [{"Area": "Changi Beach 1", "Points": [{"Lat": "1.39",
                "Lng": "103.98"}, ...]}, ...],
     "banding": {"Band1": {"AdvisoryHeader": "...", "AdvisoryText": "..."},
                 "Band2": ..., "Band3": ..., "BandAdvisory": ...},
     "guide": "<html>"}

``data`` holds the last three weeks, newest first, for 19 sampling areas on the
seven beaches NEA monitors. ``Banding`` is ``1`` (normal, EC <= 200), ``2``
(elevated), ``3`` (high) or ``Advisory`` (bacteria advisory); ``Grey`` means no
result. Areas are grouped into beaches (Sentosa = Siloso, Palawan, Tanjong);
each beach reports its worst area. Lab results take a week, so NEA updates
weekly; the coordinator polls every 6 hours.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_BEACHES, scaled_interval
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

BEACH_API_URL = "https://www.nea.gov.sg/api/BeachWaterQuality/GetNeaData/{timestamp}"
BEACH_PAGE_URL = "https://www.nea.gov.sg/beach-water-quality"
UPDATE_INTERVAL = timedelta(hours=6)

BAND_NORMAL = "normal"
BAND_ELEVATED = "elevated"
BAND_HIGH = "high"
BAND_ADVISORY = "advisory"
# Sensor options, best to worst (a beach reports its worst area).
BAND_OPTIONS: list[str] = [BAND_NORMAL, BAND_ELEVATED, BAND_HIGH, BAND_ADVISORY]
_BANDS = {
    "1": BAND_NORMAL,
    "2": BAND_ELEVATED,
    "3": BAND_HIGH,
    "advisory": BAND_ADVISORY,
}
_BAND_KEYS = {
    BAND_NORMAL: "Band1",
    BAND_ELEVATED: "Band2",
    BAND_HIGH: "Band3",
    BAND_ADVISORY: "BandAdvisory",
}

# Beach key -> (name, area-name pattern). Order = sensor order.
BEACHES: dict[str, tuple[str, re.Pattern[str]]] = {
    "changi": ("Changi", re.compile(r"^changi", re.I)),
    "east_coast": ("East Coast", re.compile(r"^east coast", re.I)),
    "pasir_ris": ("Pasir Ris", re.compile(r"^pasir ris", re.I)),
    "punggol": ("Punggol", re.compile(r"^punggol", re.I)),
    "seletar": ("Seletar Island", re.compile(r"^seletar", re.I)),
    "sembawang": ("Sembawang Park", re.compile(r"^sembawang", re.I)),
    "sentosa": ("Sentosa", re.compile(r"^(siloso|palawan|tanjong|sentosa)", re.I)),
}

_TAG_RE = re.compile(r"<[^>]+>")
_WEEK_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})")


def _text(value: object) -> str:
    """Plain text from NEA's HTML snippets (footnote marks dropped)."""
    cleaned = re.sub(r"<sup>.*?</sup>", "", str(value or ""), flags=re.S)
    return " ".join(html.unescape(_TAG_RE.sub(" ", cleaned)).split())


@dataclass(frozen=True, slots=True)
class BeachArea:
    """One sampling area's band in one week."""

    area: str
    description: str
    band: str | None  # a BAND_OPTIONS value; None for no result ("Grey")


@dataclass(frozen=True, slots=True)
class Beach:
    """One beach's latest week: its worst area band and every area."""

    key: str
    name: str
    band: str | None
    areas: tuple[BeachArea, ...]
    latitude: float | None
    longitude: float | None
    previous_bands: tuple[str | None, ...] = ()  # the weeks before, newest first


@dataclass
class BeachData:
    """The latest week's bands, per beach."""

    week_start: date | None
    week_end: date | None
    beaches: dict[str, Beach] = field(default_factory=dict)
    # Band -> (header, text) from NEA's advisories.
    advisories: dict[str, tuple[str, str]] = field(default_factory=dict)


def beach_of(area: str) -> str | None:
    """The beach key a sampling area belongs to."""
    for key, (_name, pattern) in BEACHES.items():
        if pattern.search(area.strip()):
            return key
    return None


def _band(value: object) -> str | None:
    return _BANDS.get(str(value or "").strip().casefold())


def _worst(bands: list[str | None]) -> str | None:
    known = [b for b in bands if b is not None]
    return max(known, key=BAND_OPTIONS.index) if known else None


def _week(value: object) -> tuple[date | None, date | None]:
    match = _WEEK_RE.search(str(value or ""))
    if match is None:
        return None, None
    try:
        start, end = (datetime.strptime(d, "%d/%m/%Y").date() for d in match.groups())
    except ValueError:
        return None, None
    return start, end


def _centres(areas: object) -> dict[str, tuple[float, float]]:
    """Each beach's centre: the mean of its areas' outline points."""
    points: dict[str, list[tuple[float, float]]] = {}
    for item in areas if isinstance(areas, list) else ():
        if (
            not isinstance(item, dict)
            or (key := beach_of(str(item.get("Area") or ""))) is None
        ):
            continue
        for point in item.get("Points") or ():
            try:
                points.setdefault(key, []).append(
                    (float(point["Lat"]), float(point["Lng"]))
                )
            except (KeyError, TypeError, ValueError):
                continue
    return {
        key: (
            round(sum(p[0] for p in pts) / len(pts), 5),
            round(sum(p[1] for p in pts) / len(pts), 5),
        )
        for key, pts in points.items()
        if pts
    }


def _week_areas(week: object) -> dict[str, list[BeachArea]]:
    by_beach: dict[str, list[BeachArea]] = {}
    rows = week.get("Areas") if isinstance(week, dict) else None
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        name = _text(row.get("Area"))
        if (key := beach_of(name)) is None:
            _LOGGER.debug("Beach water quality: unknown area %r", name)
            continue
        by_beach.setdefault(key, []).append(
            BeachArea(name, _text(row.get("Description")), _band(row.get("Banding")))
        )
    return by_beach


def parse_beaches(payload: object) -> BeachData:
    weeks = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(weeks, list) or not weeks:
        raise source_error(ERROR_PAYLOAD, SOURCE_BEACHES, detail="no weeks")
    latest = _week_areas(weeks[0])
    if not latest:
        raise source_error(ERROR_PAYLOAD, SOURCE_BEACHES, detail="no known beaches")
    earlier = [_week_areas(week) for week in weeks[1:]]
    centres = _centres(payload.get("areas"))
    beaches = {
        key: Beach(
            key=key,
            name=name,
            band=_worst([a.band for a in latest[key]]),
            areas=tuple(latest[key]),
            latitude=centres.get(key, (None, None))[0],
            longitude=centres.get(key, (None, None))[1],
            previous_bands=tuple(
                _worst([a.band for a in week.get(key, [])]) for week in earlier
            ),
        )
        for key, (name, _pattern) in BEACHES.items()
        if key in latest
    }
    advisories: dict[str, tuple[str, str]] = {}
    banding = payload.get("banding")
    for band, band_key in _BAND_KEYS.items():
        entry = banding.get(band_key) if isinstance(banding, dict) else None
        if isinstance(entry, dict):
            advisories[band] = (
                _text(entry.get("AdvisoryHeader")),
                _text(entry.get("AdvisoryText")),
            )
    start, end = _week(weeks[0].get("Week") if isinstance(weeks[0], dict) else None)
    return BeachData(start, end, beaches, advisories)


class BeachWaterQualityCoordinator(DataUpdateCoordinator[BeachData]):
    """Fetches NEA's weekly beach water quality bands."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore Beach Water Quality",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> BeachData:
        url = BEACH_API_URL.format(timestamp=int(time.time()) // 300 * 300)
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise source_error(
                        ERROR_HTTP, SOURCE_BEACHES, status=response.status
                    )
                try:
                    payload = await response.json(content_type=None)
                except ValueError as err:
                    raise source_error(
                        ERROR_PAYLOAD, SOURCE_BEACHES, detail="not JSON"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching beach water quality (%s); using last known values",
                    err,
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_BEACHES, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching beach water quality (%s); using last known values",
                    err,
                )
                return self.data
            raise
        return parse_beaches(payload)
