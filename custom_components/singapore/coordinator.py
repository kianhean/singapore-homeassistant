"""Data coordinator for Singapore electricity tariff."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from bs4 import BeautifulSoup

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

TARIFF_URL = "https://www.spgroup.com.sg/our-services/utilities/tariff-information"
UPDATE_INTERVAL = timedelta(hours=24)

# SP Group publishes prices in cents per kWh
UNIT = "¢/kWh"

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

# Quarter date ranges used by SP Group in page headings
_QUARTER_RANGES = {
    "Q1": ("1 January", "31 March"),
    "Q2": ("1 April", "30 June"),
    "Q3": ("1 July", "30 September"),
    "Q4": ("1 October", "31 December"),
}

# Row label keywords that identify the network cost component
_NETWORK_KEYWORDS = ("network", "transmission", "distribution", "grid")


@dataclass
class TariffData:
    """Electricity tariff data parsed from SP Group."""

    price: float          # total tariff, cents/kWh
    network_cost: float   # network component, cents/kWh
    quarter: str          # e.g. "Q1"
    year: int

    @property
    def solar_export_price(self) -> float:
        """Solar export price = total tariff minus network costs (cents/kWh).

        Solar panel owners exporting to the grid receive the energy component
        only; network charges are not paid on exported electricity.
        """
        return round(self.price - self.network_cost, 2)


class SPGroupCoordinator(DataUpdateCoordinator[TariffData]):
    """Fetches and caches SP Group electricity tariff data."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="SP Group Electricity Tariff",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> TariffData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                TARIFF_URL, headers=_HEADERS, timeout=30
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"SP Group returned HTTP {response.status}"
                    )
                html = await response.text()
        except Exception as err:
            raise UpdateFailed(f"Error fetching SP Group tariff: {err}") from err

        return _parse_tariff(html)


def _parse_tariff(html: str) -> TariffData:
    """Parse tariff price, network cost, quarter and year from SP Group HTML."""
    soup = BeautifulSoup(html, "html.parser")

    quarter, year = _extract_quarter_year(soup)

    price = _extract_row_value(soup, keywords=("total", "residential"))
    if price is None:
        raise UpdateFailed("Could not find electricity price on SP Group page")

    network_cost = _extract_row_value(soup, keywords=_NETWORK_KEYWORDS)
    if network_cost is None:
        _LOGGER.warning(
            "Could not find network cost on SP Group page; solar export price unavailable"
        )
        network_cost = 0.0

    return TariffData(price=price, network_cost=network_cost, quarter=quarter, year=year)


def _extract_quarter_year(soup: BeautifulSoup) -> tuple[str, int]:
    """Return (quarter, year) by scanning headings for date ranges."""
    text = soup.get_text(" ", strip=True)

    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else 0

    for quarter, (start, _end) in _QUARTER_RANGES.items():
        if start.lower() in text.lower():
            return quarter, year

    month_match = re.search(
        r"\b(\d{1,2})\s+(?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)\s+(20\d{2})",
        text,
        re.IGNORECASE,
    )
    if month_match:
        year = int(month_match.group(2))

    return "Unknown", year


def _extract_row_value(
    soup: BeautifulSoup, keywords: tuple[str, ...]
) -> float | None:
    """
    Find a table row whose label matches any of `keywords` and return its
    numeric value (the last numeric cell in the row).

    Falls back to a regex scan of the full page text.
    """
    # Strategy 1: table row keyword match
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            row_text = " ".join(c.get_text(strip=True) for c in cells).lower()
            if any(kw in row_text for kw in keywords):
                for cell in reversed(cells):
                    val = _to_float(cell.get_text(strip=True))
                    if val is not None and 0.5 < val < 100.0:
                        return val

    # Strategy 2: regex scan near the keyword
    text = soup.get_text(" ", strip=True)
    pattern = (
        r"(?:" + "|".join(re.escape(kw) for kw in keywords) + r")"
        r"[^0-9]{0,60}?(\d{1,3}\.\d{1,2})\s*(?:cents?)?"
    )
    for m in re.findall(pattern, text, re.IGNORECASE):
        val = _to_float(m)
        if val is not None and 0.5 < val < 100.0:
            return val

    return None


def _to_float(s: str) -> float | None:
    """Convert string to float, return None if not possible."""
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None
