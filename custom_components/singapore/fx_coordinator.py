"""Foreign exchange rates against SGD from the ECB daily reference rates.

The European Central Bank publishes euro reference rates for ~30 currencies
(SGD included) each working day around 16:00 CET, key-free:
https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml

Every rate is quoted per EUR, so an SGD cross is ``SGD/EUR ÷ X/EUR``: the
price in SGD of one unit of X (or of 100 units for small-unit currencies such
as JPY, the way Singapore banks quote them).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import SOURCE_FX
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
# The ECB publishes once a working day; a few polls a day pick it up promptly
# without hammering the endpoint.
FX_UPDATE_INTERVAL = timedelta(hours=4)

UNIT_SGD = "SGD"

# Quoted per 100 units (as Singapore banks and MAS do).
PER_100_CURRENCIES = frozenset({"JPY", "KRW", "IDR", "INR", "PHP", "THB", "HUF", "ISK"})


def fx_quote_units(currency: str) -> int:
    """How many units of ``currency`` a sensor's SGD price is for."""
    return 100 if currency in PER_100_CURRENCIES else 1


@dataclass
class FxData:
    """Latest SGD prices of foreign currencies."""

    date: str  # ECB reference date, e.g. "2026-11-20"
    # Currency code -> SGD per unit (per 100 units for PER_100_CURRENCIES).
    rates: dict[str, float] = field(default_factory=dict)


def parse_ecb(xml_text: str) -> FxData:
    """Parse the ECB eurofxref XML into SGD crosses."""
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - trusted ECB source
    except ET.ParseError as err:
        raise source_error(ERROR_PAYLOAD, SOURCE_FX, detail=repr(err)) from err

    day = None
    per_eur: dict[str, float] = {"EUR": 1.0}
    for cube in root.iter():
        if not cube.tag.endswith("Cube"):
            continue
        if (time := cube.get("time")) is not None:
            day = time
        currency, rate = cube.get("currency"), cube.get("rate")
        if currency and rate:
            try:
                per_eur[currency.upper()] = float(rate)
            except ValueError:
                _LOGGER.debug("Skipping unparsable ECB rate %s=%s", currency, rate)

    sgd = per_eur.get("SGD")
    if day is None or not sgd:
        raise source_error(ERROR_PAYLOAD, SOURCE_FX, detail="no SGD reference rate")

    rates = {
        code: round(sgd / value * fx_quote_units(code), 6)
        for code, value in per_eur.items()
        if code != "SGD" and value > 0
    }
    return FxData(date=day, rates=rates)


class FxCoordinator(DataUpdateCoordinator[FxData]):
    """Fetches the ECB reference rates and derives SGD crosses."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="ECB FX rates",
            update_interval=FX_UPDATE_INTERVAL,
        )
        self.last_updated: datetime | None = None

    async def _async_update_data(self) -> FxData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                ECB_URL, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise source_error(ERROR_HTTP, SOURCE_FX, status=response.status)
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise source_error(ERROR_FETCH, SOURCE_FX, error=err) from err
        data = parse_ecb(text)
        self.last_updated = datetime.now(UTC)
        return data
