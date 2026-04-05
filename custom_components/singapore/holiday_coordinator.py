"""Data coordinator for Singapore public holidays (MOM)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

PUBLIC_HOLIDAYS_URL = "https://www.mom.gov.sg/employment-practices/public-holidays"
UPDATE_INTERVAL = timedelta(hours=24)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}


@dataclass(frozen=True)
class PublicHoliday:
    """Single Singapore public holiday."""

    name: str
    day: date


class PublicHolidayCoordinator(DataUpdateCoordinator[list[PublicHoliday]]):
    """Fetches and caches Singapore public holidays from MOM."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Singapore Public Holidays",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> list[PublicHoliday]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                PUBLIC_HOLIDAYS_URL, headers=_HEADERS, timeout=30
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(f"MOM returned HTTP {response.status}")
                html = await response.text()
            return _parse_public_holidays(html)
        except Exception as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching MOM public holidays (%s); using last known values",
                    err,
                )
                return self.data
            raise UpdateFailed(f"Error fetching MOM public holidays: {err}") from err


def _parse_public_holidays(html: str) -> list[PublicHoliday]:
    """Parse public holidays from MOM HTML for current year and later."""
    soup = BeautifulSoup(html, "html.parser")
    current_year = datetime.now().year

    holidays: list[PublicHoliday] = []
    seen: set[tuple[str, date]] = set()

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue

        name = cells[0].get_text(" ", strip=True)
        date_text = cells[1].get_text(" ", strip=True)

        if not name or "holiday" in name.lower() and "date" in date_text.lower():
            continue

        parsed_date = _extract_date(date_text)
        if parsed_date is None:
            continue

        if parsed_date.year < current_year:
            continue

        item = PublicHoliday(name=name, day=parsed_date)
        key = (item.name, item.day)
        if key not in seen:
            holidays.append(item)
            seen.add(key)

    if not holidays:
        # Fallback for simplified HTML parsers in tests and for non-tabular pages.
        page_text = soup.get_text(" ", strip=True)
        for name, token in re.findall(
            r"([A-Za-z0-9'’()&/ -]{3,}?)\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
            page_text,
        ):
            cleaned_name = re.sub(r"\s+", " ", name).strip(" -,:")
            cleaned_name = re.sub(
                r"^(?:public holidays?\s+)?holiday\s+date\s+",
                "",
                cleaned_name,
                flags=re.IGNORECASE,
            )
            cleaned_name = re.sub(
                r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+",
                "",
                cleaned_name,
                flags=re.IGNORECASE,
            )
            if not cleaned_name or cleaned_name.lower() in {"holiday", "date"}:
                continue
            if "public holidays" in cleaned_name.lower():
                continue
            parsed_date = _extract_date(token)
            if parsed_date is None or parsed_date.year < current_year:
                continue

            item = PublicHoliday(name=cleaned_name, day=parsed_date)
            key = (item.name, item.day)
            if key not in seen:
                holidays.append(item)
                seen.add(key)

    holidays.sort(key=lambda h: (h.day, h.name))
    if not holidays:
        raise UpdateFailed("Could not find public holiday rows on MOM page")
    return holidays


def _extract_date(text: str) -> date | None:
    """Extract a date from a human-readable date cell."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    patterns = [
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b",  # 31 Mar 2026 / 31 March 2026
        r"\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b",  # March 31, 2026
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue

        token = match.group(0)
        for fmt in ("%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(token, fmt).date()
            except ValueError:
                continue

    return None
