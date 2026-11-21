"""Data coordinator for NEA weather warnings and regional advisories (nea.gov.sg)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_WARNINGS, scaled_interval
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

WARNINGS_URL = "https://www.nea.gov.sg/api/Warning/GetAllWarning/"
UPDATE_INTERVAL = timedelta(minutes=5)

_SGT = timezone(timedelta(hours=8))
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MAGNITUDE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s+magnitude\s+earthquake\b", re.IGNORECASE
)
_INACTIVE_VALUES = {"", "nil", "none", "n/a", "na", "-"}


@dataclass(frozen=True, slots=True)
class WeatherAdvisory:
    """One category from NEA's GetAllWarning endpoint."""

    category: str
    text: str | None
    active: bool
    issued: datetime | None = None
    expires: datetime | None = None
    link: str | None = None

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "active": self.active,
            "text": self.text,
            "issued": self.issued.isoformat() if self.issued else None,
            "expires": self.expires.isoformat() if self.expires else None,
            "link": self.link,
        }


@dataclass
class WeatherWarningsData:
    """Parsed NEA weather warnings and regional advisories."""

    advisories: dict[str, WeatherAdvisory] = field(default_factory=dict)

    @property
    def active_advisories(self) -> list[WeatherAdvisory]:
        return [a for a in self.advisories.values() if a.active]

    @property
    def heavy_rain(self) -> WeatherAdvisory | None:
        return self.advisories.get("Heavy Rain")

    @property
    def earthquake(self) -> WeatherAdvisory | None:
        return self.advisories.get("Earthquake")

    @property
    def earthquake_magnitude(self) -> float | None:
        eq = self.earthquake
        if eq is None or not eq.text:
            return None
        match = _MAGNITUDE_RE.search(eq.text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_SGT)


def _clean_text(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = _HTML_TAG_RE.sub(" ", raw)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    if cleaned.lower() in _INACTIVE_VALUES:
        return None
    return cleaned


def _parse_warnings(payload: object) -> WeatherWarningsData:
    """Parse NEA's GetAllWarning JSON array.

    Expected shape::

        [
          {
            "title": "Heavy Rain",
            "warning": "NIL" | "Heavy rain expected over ...",
            "link": "/weather/rain-areas",
            "issuedDateTime": "2026-11-18T16:37:00",
            "expiryDateTime": "2026-11-18T18:37:00"
          },
          ...
        ]
    """
    if not isinstance(payload, list) or not payload:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_WARNINGS, detail="expected non-empty JSON list"
        )

    advisories: dict[str, WeatherAdvisory] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        text = _clean_text(item.get("warning")) or _clean_text(item.get("issueText"))
        link = item.get("link")
        if isinstance(link, str) and link.startswith("/"):
            link = f"https://www.nea.gov.sg{link}"
        elif not isinstance(link, str) or not link:
            link = None
        advisories[title] = WeatherAdvisory(
            category=title,
            text=text,
            active=text is not None,
            issued=_parse_datetime(item.get("issuedDateTime")),
            expires=_parse_datetime(item.get("expiryDateTime")),
            link=link,
        )

    if not advisories:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_WARNINGS, detail="no warning categories parsed"
        )
    return WeatherWarningsData(advisories=advisories)


class WeatherWarningsCoordinator(DataUpdateCoordinator[WeatherWarningsData]):
    """Polls NEA weather warnings and regional advisories from nea.gov.sg."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore NEA Weather Warnings",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> WeatherWarningsData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                WARNINGS_URL,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(
                        ERROR_HTTP, SOURCE_WARNINGS, status=response.status
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching NEA weather warnings (%s); using last known values",
                    err,
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_WARNINGS, error=err) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching NEA weather warnings (%s); using last known values",
                    err,
                )
                return self.data
            raise

        return _parse_warnings(payload)
