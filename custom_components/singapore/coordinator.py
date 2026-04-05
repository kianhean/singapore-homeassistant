"""Data coordinator for Singapore SP Group tariffs."""

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

UNIT_ELECTRICITY = "¢/kWh"
UNIT_GAS = "¢/kWh"
UNIT_WATER = "SGD/m³"

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

_QUARTER_RANGES = {
    "Q1": ("1 January", "31 March"),
    "Q2": ("1 April", "30 June"),
    "Q3": ("1 July", "30 September"),
    "Q4": ("1 October", "31 December"),
}

# Abbreviated month → quarter (for "wef 1 Apr - 30 Jun 26" style dates)
_MONTH_ABBR_TO_Q = {
    "jan": "Q1",
    "feb": "Q1",
    "mar": "Q1",
    "apr": "Q2",
    "may": "Q2",
    "jun": "Q2",
    "jul": "Q3",
    "aug": "Q3",
    "sep": "Q3",
    "oct": "Q4",
    "nov": "Q4",
    "dec": "Q4",
}

_NETWORK_KEYWORDS = ("network", "transmission", "distribution", "grid")
_GAS_KEYWORDS = ("gas",)
_WATER_KEYWORDS = ("water",)


@dataclass
class TariffData:
    """SP Group tariff data for electricity, gas and water."""

    electricity_price: float  # total residential tariff, ¢/kWh
    network_cost: float  # electricity network component, ¢/kWh
    gas_price: float  # piped natural gas tariff, ¢/kWh
    water_price: float  # water tariff, SGD/m³
    quarter: str  # e.g. "Q1"
    year: int

    @property
    def solar_export_price(self) -> float:
        """Solar export price = electricity tariff minus network costs (¢/kWh).

        Solar panel owners exporting to the grid receive the energy component
        only; network charges do not apply to exported electricity.
        """
        return round(self.electricity_price - self.network_cost, 2)


class SPGroupCoordinator(DataUpdateCoordinator[TariffData]):
    """Fetches and caches SP Group tariff data (electricity, gas, water)."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="SP Group Tariffs",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> TariffData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                TARIFF_URL, headers=_HEADERS, timeout=30
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(f"SP Group returned HTTP {response.status}")
                html = await response.text()
            return _parse_tariff(html)
        except Exception as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching SP Group tariffs (%s); using last known values",
                    err,
                )
                return self.data
            raise UpdateFailed(f"Error fetching SP Group tariffs: {err}") from err


def _parse_tariff(html: str) -> TariffData:
    """Parse electricity, gas and water tariffs from SP Group HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Build a single text corpus: visible page text + all script tag content
    # (covers both classic server-rendered HTML and Next.js __NEXT_DATA__ JSON)
    page_text = soup.get_text(" ", strip=True)
    script_text = " ".join(
        (s.string or "") for s in soup.find_all("script") if s.string
    )
    full_text = page_text + " " + script_text

    quarter, year = _extract_quarter_year(full_text)

    # Strategy 1: SP Group banner format (current layout as of 2026):
    #   "29.72 cents/kWh 27.27 cents/kWh (w/o GST) ELECTRICITY TARIFF"
    # The with-GST price always precedes the w/o-GST price before the label.
    electricity_price = _extract_banner_cents_kwh(full_text, "ELECTRICITY")
    if electricity_price is None:
        # Strategy 2: generic keyword + float search (classic table layout)
        electricity_price = _extract_by_keywords(
            full_text, keywords=("total", "residential")
        )
    if electricity_price is None:
        raise UpdateFailed("Could not find electricity price on SP Group page")

    network_cost = _extract_by_keywords(full_text, keywords=_NETWORK_KEYWORDS)
    if network_cost is None:
        _LOGGER.warning(
            "Could not find network cost; solar export price will equal total tariff"
        )
        network_cost = 0.0

    gas_price = _extract_banner_cents_kwh(full_text, "GAS")
    if gas_price is None:
        gas_price = _extract_by_keywords(full_text, keywords=_GAS_KEYWORDS)
    if gas_price is None:
        _LOGGER.warning("Could not find gas tariff on SP Group page")
        gas_price = 0.0

    # Water is shown as "$1.56 or $1.97/m³" (two residential tiers).
    # Report the lower tier (≤40 m³) which covers most households.
    water_price = _extract_water_tiered(full_text)
    if water_price is None:
        water_price = _extract_by_keywords(full_text, keywords=_WATER_KEYWORDS)
    if water_price is None:
        _LOGGER.warning("Could not find water tariff on SP Group page")
        water_price = 0.0

    return TariffData(
        electricity_price=electricity_price,
        network_cost=network_cost,
        gas_price=gas_price,
        water_price=water_price,
        quarter=quarter,
        year=year,
    )


def _extract_banner_cents_kwh(text: str, label: str) -> float | None:
    """Parse SP Group's hero-banner format.

    Matches: ``XX.XX cents/kWh YY.YY cents/kWh (w/o GST) <LABEL> TARIFF``
    Returns the with-GST price (the first number).
    """
    pattern = (
        r"(\d{1,3}\.\d{2})\s+cents/kWh\s+"
        r"\d{1,3}\.\d{2}\s+cents/kWh\s+\(w/o\s+GST\)"
        r".{0,40}" + re.escape(label)
    )
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if m:
        val = _to_float(m.group(1))
        if val is not None and 0.1 < val < 200.0:
            return val
    return None


def _extract_water_tiered(text: str) -> float | None:
    """Parse SP Group's two-tier water tariff format.

    Matches: ``$X.XX or $Y.YY/m`` (with or without spaces around the slash).
    Returns the lower residential tier (≤40 m³, with GST).
    """
    m = re.search(r"\$(\d+\.\d+)\s+or\s+\$\d+\.\d+\s*/\s*m", text, re.IGNORECASE)
    if m:
        val = _to_float(m.group(1))
        if val is not None and 0.1 < val < 20.0:
            return val
    return None


def _extract_quarter_year(text: str) -> tuple[str, int]:
    """Return (quarter, year) by scanning text for SP Group date ranges."""
    # Full month name + 4-digit year: "1 January 2025 to 31 March 2025"
    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else 0

    for quarter, (start, _end) in _QUARTER_RANGES.items():
        if start.lower() in text.lower():
            return quarter, year

    # Abbreviated format: "wef 1 Apr - 30 Jun 26"
    # Capture the START month (→ quarter) and the trailing 2-digit year.
    abbr_match = re.search(
        r"wef\s+\d{1,2}\s+([A-Za-z]{3})"  # start: day + month abbr
        r"(?:\s*[-–]\s*\d{1,2}\s+[A-Za-z]{3})?"  # optional: "- 30 Jun"
        r"\s+(\d{2})\b",  # trailing 2-digit year
        text,
        re.IGNORECASE,
    )
    if abbr_match:
        month_abbr = abbr_match.group(1).lower()
        year_2d = int(abbr_match.group(2))
        year = 2000 + year_2d
        quarter = _MONTH_ABBR_TO_Q.get(month_abbr, "Unknown")
        return quarter, year

    # Generic month match fallback
    month_match = re.search(
        r"\b(\d{1,2})\s+(?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)\s+(20\d{2})",
        text,
        re.IGNORECASE,
    )
    if month_match:
        year = int(month_match.group(2))

    return "Unknown", year


def _extract_by_keywords(text: str, keywords: tuple[str, ...]) -> float | None:
    """Regex-search text for a float near any keyword.

    Designed for classic table/paragraph layouts where a keyword label sits
    directly adjacent to its value with no intervening digits.

    Tries two passes:
    - Forward:  keyword … number  (up to 80 non-digit chars)
    - Reverse:  number … keyword  (up to 60 non-digit chars)
    """
    kw_group = "(?:" + "|".join(re.escape(kw) for kw in keywords) + ")"

    # Forward pass
    for m in re.findall(
        kw_group + r"[^0-9]{0,80}?(\d{1,3}\.\d{1,2})", text, re.IGNORECASE
    ):
        val = _to_float(m)
        if val is not None and 0.1 < val < 200.0:
            return val

    # Reverse pass (number appears before its label)
    for m in re.findall(
        r"(\d{1,3}\.\d{1,2})[^0-9]{0,60}?" + kw_group, text, re.IGNORECASE
    ):
        val = _to_float(m)
        if val is not None and 0.1 < val < 200.0:
            return val

    return None


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None
