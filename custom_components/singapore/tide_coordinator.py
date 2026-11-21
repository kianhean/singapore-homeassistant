"""Data coordinator for NEA tide timings (nea.gov.sg)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_TIDES
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

TIDES_URL = "https://www.nea.gov.sg/corporate-functions/weather/tide-timings"
UPDATE_INTERVAL = timedelta(hours=24)

_SGT = timezone(timedelta(hours=8))
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_YEAR_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d{2})\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"^(\d{2})(\d{2})$")

TIDE_STATE_HIGH = "high"
TIDE_STATE_LOW = "low"
TIDE_STATE_OPTIONS = [TIDE_STATE_HIGH, TIDE_STATE_LOW]


@dataclass(frozen=True, slots=True)
class TideEntry:
    """A single high or low tide event."""

    time: datetime
    height: float
    state: str  # "high" or "low"

    def as_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "height_m": self.height,
            "tide": self.state,
        }


@dataclass
class TideData:
    """Chronological tide predictions from NEA."""

    tides: list[TideEntry] = field(default_factory=list)

    def next_tide(self, now: datetime) -> TideEntry | None:
        """The first tide at or after ``now`` (or the last one if all are past)."""
        for tide in self.tides:
            if tide.time >= now:
                return tide
        return self.tides[-1] if self.tides else None

    def next_of_state(self, now: datetime, state: str) -> TideEntry | None:
        """The next high or low tide at or after ``now``."""
        for tide in self.tides:
            if tide.state == state and tide.time >= now:
                return tide
        return None


def _parse_tide_html(html: str, default_year: int | None = None) -> TideData:
    """Parse NEA's server-rendered tide-timings HTML tables."""
    soup = BeautifulSoup(html, "html.parser")
    if default_year is None:
        default_year = datetime.now(_SGT).year

    # Map tab box ID (e.g. "Nov") to (month, year) from the tab buttons ("Nov 2026").
    tab_dates: dict[str, tuple[int, int]] = {}
    for btn in soup.select(".tab__nav-item"):
        box = (btn.get("data-box") or "").strip()
        text = btn.get_text(" ", strip=True)
        match = _MONTH_YEAR_RE.search(text)
        if box and match:
            month_num = _MONTHS[match.group(1)[:3].lower()]
            tab_dates[box] = (month_num, int(match.group(2)))

    tides: list[TideEntry] = []
    for tab in soup.select(".tab__item"):
        box = (tab.get("data-box") or tab.get("id") or "").strip()
        month_year = tab_dates.get(box)
        if month_year is None:
            month_num = _MONTHS.get(box[:3].lower())
            if month_num is None:
                continue
            month_year = (month_num, default_year)
        month, year = month_year

        current_day: int | None = None
        for row in tab.select("table tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) == 4:
                try:
                    current_day = int(cells[0])
                except ValueError:
                    continue
                time_str, height_str, hl_str = cells[1], cells[2], cells[3]
            elif len(cells) == 3 and current_day is not None:
                time_str, height_str, hl_str = cells[0], cells[1], cells[2]
            else:
                continue

            time_match = _TIME_RE.match(time_str.zfill(4))
            if not time_match:
                continue
            try:
                hour, minute = int(time_match.group(1)), int(time_match.group(2))
                height = round(float(height_str), 2)
                when = datetime(year, month, current_day, hour, minute, tzinfo=_SGT)
            except ValueError:
                continue
            hl_upper = hl_str.upper()
            if hl_upper == "H":
                state = TIDE_STATE_HIGH
            elif hl_upper == "L":
                state = TIDE_STATE_LOW
            else:
                continue
            tides.append(TideEntry(time=when, height=height, state=state))

    if not tides:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_TIDES, detail="no tide rows found in page"
        )
    tides.sort(key=lambda t: t.time)
    return TideData(tides=tides)


class TideCoordinator(DataUpdateCoordinator[TideData]):
    """Fetches and parses NEA tide timings from nea.gov.sg."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore NEA Tide Timings",
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )

    async def _async_update_data(self) -> TideData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                TIDES_URL,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(ERROR_HTTP, SOURCE_TIDES, status=response.status)
                html = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching NEA tide timings (%s); using last known values",
                    err,
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_TIDES, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching NEA tide timings (%s); using last known values",
                    err,
                )
                return self.data
            raise

        return await self.hass.async_add_executor_job(_parse_tide_html, html)
