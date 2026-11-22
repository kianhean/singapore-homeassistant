"""Pump prices from the fuel brands' own websites (Caltex, SPC, Shell).

Each brand publishes its pump prices differently:

- **Caltex** (https://www.caltex.com/sg/): the homepage's ``<fuelprice-content
  list="...">`` element holds a JSON list (HTML-escaped) of ``fuelName``,
  ``fuelPrice`` and ``discountFuelPrice`` (``"SGD 3.600"``), with "Last
  updated: 04 Nov 2026, 1:00 PM" beside it.
- **SPC** (latest-pump-price page): one ``<span class="pump-price"
  data-price="3.560">`` per grade, after an icon whose file name says the grade
  (``pump-prices-levo-95.png``, ``pump-prices-diesel.png``), and "Last updated
  on 16 Oct 2026 at 1130hrs".
- **Shell** (station price board): the page is an AEM app whose
  ``.model.json`` links a spreadsheet, ``fuel-price-update-singapore.xlsx``
  (the link changes with each update). Its first sheet lists each fuel with the
  listed pump price and two nett prices after discounts; the file's
  ``docProps/core.xml`` modified time is the update time. Read with the
  standard library (``zipfile`` + ElementTree) in the executor.

Esso's website refuses automated requests, and no official Sinopec Singapore
site was found, so neither is included. A brand that fails keeps its last
prices (or is left out); the update fails only when no brand has any.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import SOURCE_PUMP_PRICES, scaled_interval
from .errors import ERROR_FETCH, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

CALTEX_URL = "https://www.caltex.com/sg/"
SPC_URL = "https://www.spc.com.sg/our-business/spc-service-station/latest-pump-price/"
SHELL_MODEL_URL = (
    "https://www.shell.com.sg/fuels-oils-and-coolants/shell-fuels/"
    "shell-station-price-board.model.json"
)
UPDATE_INTERVAL = timedelta(hours=6)
UNIT_PUMP_PRICE = "SGD/L"

SGT = timezone(timedelta(hours=8))

BRAND_CALTEX = "caltex"
BRAND_SPC = "spc"
BRAND_SHELL = "shell"
BRANDS: tuple[str, ...] = (BRAND_CALTEX, BRAND_SPC, BRAND_SHELL)

# Grade keys (also the sensors' translation keys, pump_<grade>), in order.
GRADE_92 = "92"
GRADE_95 = "95"
GRADE_98 = "98"
GRADE_PREMIUM = "premium"  # Shell V-Power (above 98)
GRADE_DIESEL = "diesel"
GRADE_PREMIUM_DIESEL = "premium_diesel"
GRADES: tuple[str, ...] = (
    GRADE_92,
    GRADE_95,
    GRADE_98,
    GRADE_PREMIUM,
    GRADE_DIESEL,
    GRADE_PREMIUM_DIESEL,
)

_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")
_CALTEX_LIST_RE = re.compile(r'<fuelprice-content[^>]*\blist="([^"]*)"', re.S)
_CALTEX_UPDATED_RE = re.compile(
    r"Last updated:\s*(\d{1,2} \w{3} \d{4}),\s*(\d{1,2}:\d{2}\s*[AP]M)", re.I
)
_SPC_PRICE_RE = re.compile(
    r'pump-prices-([a-z0-9-]+)\.png.*?class="pump-price"[^>]*data-price="([^"]+)"',
    re.S,
)
_SPC_UPDATED_RE = re.compile(
    r"Last updated on (\d{1,2} \w{3} \d{4}) at (\d{4})\s*hrs", re.I
)
_SHELL_XLSX_RE = re.compile(r'https://[^"\s]+?\.xlsx')
_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_CELL_COLUMN_RE = re.compile(r"^([A-Z]+)")


@dataclass(frozen=True, slots=True)
class PumpPrice:
    """One grade's pump price at one brand (SGD per litre)."""

    grade: str
    product: str  # the brand's own name, e.g. "Shell FuelSave 95"
    price: float
    discounted: float | None = None  # Caltex's discounted / Shell's nett price


@dataclass
class BrandPrices:
    """One brand's prices, keyed by grade."""

    prices: dict[str, PumpPrice] = field(default_factory=dict)
    updated: datetime | None = None


@dataclass
class PumpPriceData:
    """The brands whose prices loaded, keyed by brand."""

    brands: dict[str, BrandPrices] = field(default_factory=dict)


def grade_of(name: str) -> str | None:
    """The grade key for a product or icon name (None: not a fuel we know)."""
    folded = name.casefold()
    if "diesel" in folded:
        return GRADE_PREMIUM_DIESEL if "v-power" in folded else GRADE_DIESEL
    if "v-power" in folded:
        return GRADE_PREMIUM
    for grade in (GRADE_92, GRADE_95, GRADE_98):
        if re.search(rf"(?<!\d){grade}(?!\d)", folded):
            return grade
    return None


def _price(value: object) -> float | None:
    match = _PRICE_RE.search(str(value or ""))
    if match is None:
        return None
    price = float(match.group(1))
    # A litre of fuel in Singapore costs a few dollars; anything else is junk.
    return price if 0 < price < 20 else None


def _add(prices: dict[str, PumpPrice], price: PumpPrice) -> None:
    # The first product listed for a grade wins (the brand's main one).
    prices.setdefault(price.grade, price)


def parse_caltex(page: str) -> BrandPrices:
    match = _CALTEX_LIST_RE.search(page)
    if match is None:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_PUMP_PRICES, detail="Caltex: no fuel price list"
        )
    try:
        rows = json.loads(html.unescape(match.group(1)))
    except ValueError as err:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_PUMP_PRICES, detail="Caltex: list is not JSON"
        ) from err
    prices: dict[str, PumpPrice] = {}
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        name = html.unescape(str(row.get("fuelName") or "")).strip()
        grade = grade_of(name)
        price = _price(row.get("fuelPrice"))
        if grade is None or price is None:
            continue
        _add(
            prices, PumpPrice(grade, name, price, _price(row.get("discountFuelPrice")))
        )
    updated = None
    if (when := _CALTEX_UPDATED_RE.search(page)) is not None:
        try:
            updated = datetime.strptime(
                f"{when.group(1)} {when.group(2).replace(' ', '')}", "%d %b %Y %I:%M%p"
            ).replace(tzinfo=SGT)
        except ValueError:
            updated = None
    return BrandPrices(prices, updated)


def parse_spc(page: str) -> BrandPrices:
    prices: dict[str, PumpPrice] = {}
    for icon, value in _SPC_PRICE_RE.findall(page):
        grade = grade_of(icon.replace("-", " "))
        price = _price(value)
        if grade is None or price is None:
            continue
        name = "SPC Diesel" if grade == GRADE_DIESEL else f"SPC LEVO {grade}"
        _add(prices, PumpPrice(grade, name, price))
    updated = None
    if (when := _SPC_UPDATED_RE.search(page)) is not None:
        try:
            updated = datetime.strptime(
                f"{when.group(1)} {when.group(2)}", "%d %b %Y %H%M"
            ).replace(tzinfo=SGT)
        except ValueError:
            updated = None
    return BrandPrices(prices, updated)


def shell_sheet_url(model: str) -> str | None:
    """The spreadsheet's link in Shell's page model."""
    match = _SHELL_XLSX_RE.search(model)
    return match.group(0) if match else None


def _sheet_rows(archive: zipfile.ZipFile) -> list[dict[str, str]]:
    names = archive.namelist()
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = [
            "".join(text.text or "" for text in item.iter(f"{{{_XLSX_NS['m']}}}t"))
            for item in root.findall("m:si", _XLSX_NS)
        ]
    sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    if not sheets:
        return []
    rows: list[dict[str, str]] = []
    for row in ET.fromstring(archive.read(sheets[0])).iter(f"{{{_XLSX_NS['m']}}}row"):
        cells: dict[str, str] = {}
        for cell in row.findall("m:c", _XLSX_NS):
            column = _CELL_COLUMN_RE.match(cell.get("r") or "")
            value = cell.find("m:v", _XLSX_NS)
            if column is None:
                continue
            if cell.get("t") == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{{{_XLSX_NS['m']}}}t"))
            elif value is None:
                continue
            elif cell.get("t") == "s":
                try:
                    text = shared[int(value.text or "")]
                except (ValueError, IndexError):
                    continue
            else:
                text = value.text or ""
            cells[column.group(1)] = text.strip()
        rows.append(cells)
    return rows


def parse_shell_sheet(content: bytes) -> BrandPrices:
    """Prices from Shell's spreadsheet: column A fuel, B listed, D best nett."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        rows = _sheet_rows(archive)
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as err:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_PUMP_PRICES, detail="Shell: not a spreadsheet"
        ) from err
    prices: dict[str, PumpPrice] = {}
    for cells in rows:
        name = " ".join((cells.get("A") or "").split())
        grade = grade_of(name)
        price = _price(cells.get("B"))
        if grade is None or price is None:
            continue
        # The last nett column has every discount; the one before, fewer.
        nett = _price(cells.get("D")) or _price(cells.get("C"))
        _add(prices, PumpPrice(grade, name, price, nett))
    updated = None
    try:
        core = ET.fromstring(archive.read("docProps/core.xml"))
        modified = core.find("{http://purl.org/dc/terms/}modified")
        if modified is not None and modified.text:
            updated = datetime.fromisoformat(modified.text.replace("Z", "+00:00"))
    except (KeyError, ET.ParseError, ValueError):
        updated = None
    return BrandPrices(prices, updated)


class PumpPriceCoordinator(DataUpdateCoordinator[PumpPriceData]):
    """Fetches pump prices from Caltex, SPC and Shell."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore Pump Prices",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    def _get(self, url: str):
        session = async_get_clientsession(self.hass)
        return session.get(url, timeout=aiohttp.ClientTimeout(total=30))

    async def _text(self, url: str) -> str:
        async with self._get(url) as response:
            response.raise_for_status()
            return await response.text()

    async def _caltex(self) -> BrandPrices:
        return parse_caltex(await self._text(CALTEX_URL))

    async def _spc(self) -> BrandPrices:
        return parse_spc(await self._text(SPC_URL))

    async def _shell(self) -> BrandPrices:
        url = shell_sheet_url(await self._text(SHELL_MODEL_URL))
        if url is None:
            raise source_error(
                ERROR_PAYLOAD, SOURCE_PUMP_PRICES, detail="Shell: no price sheet link"
            )
        async with self._get(url) as response:
            response.raise_for_status()
            content = await response.read()
        return await self.hass.async_add_executor_job(parse_shell_sheet, content)

    async def _async_update_data(self) -> PumpPriceData:
        fetchers = {
            BRAND_CALTEX: self._caltex,
            BRAND_SPC: self._spc,
            BRAND_SHELL: self._shell,
        }
        results = await asyncio.gather(
            *(fetch() for fetch in fetchers.values()), return_exceptions=True
        )
        previous = self.data.brands if self.data is not None else {}
        brands: dict[str, BrandPrices] = {}
        errors: list[str] = []
        for brand, result in zip(fetchers, results, strict=True):
            if isinstance(result, BrandPrices) and result.prices:
                brands[brand] = result
                continue
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            reason = "no prices" if isinstance(result, BrandPrices) else str(result)
            errors.append(f"{brand}: {reason}")
            # A network error keeps the brand's last prices; a page that no
            # longer parses drops the brand, so its sensors go unavailable.
            transient = isinstance(result, (aiohttp.ClientError, TimeoutError))
            _LOGGER.warning(
                "Pump prices from %s failed (%s)%s",
                brand,
                reason,
                "; using last known prices" if transient and brand in previous else "",
            )
            if transient and brand in previous:
                brands[brand] = previous[brand]
        if not brands:
            raise source_error(ERROR_FETCH, SOURCE_PUMP_PRICES, error="; ".join(errors))
        return PumpPriceData(brands)
