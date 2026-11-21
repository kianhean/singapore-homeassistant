"""Singapore average retail pump fuel prices from SingStat TableBuilder.

SingStat TableBuilder table M213761 "Average Retail Prices Of Selected
Consumer Items, Monthly" publishes monthly average retail prices key-free:
https://tablebuilder.singstat.gov.sg/api/table/tabledata/M213761?seriesNoORrowNo=81,82,83,85

Series 81, 82, 83 and 85 are 98-octane petrol, 95-octane petrol, 92-octane
petrol and diesel (SGD per litre).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import SOURCE_FUEL
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

FUEL_URL = (
    "https://tablebuilder.singstat.gov.sg/api/table/tabledata/M213761"
    "?seriesNoORrowNo=81,82,83,85"
)
FUEL_UPDATE_INTERVAL = timedelta(hours=12)
UNIT_FUEL = "SGD/L"

# Sensor key -> pattern matched against the SingStat row text. Order = sensor order.
FUEL_SERIES: dict[str, re.Pattern[str]] = {
    "ron92": re.compile(r"petrol,\s*92\s*octane", re.I),
    "ron95": re.compile(r"petrol,\s*95\s*octane", re.I),
    "ron98": re.compile(r"petrol,\s*98\s*octane", re.I),
    "diesel": re.compile(r"^diesel\b", re.I),
}
FUEL_SERIES_KEYS: tuple[str, ...] = tuple(FUEL_SERIES)


@dataclass
class FuelPrice:
    """Latest monthly average retail price of one fuel grade."""

    value: float
    period: str  # e.g. "2026 Sep"
    series: str  # SingStat row text


@dataclass
class FuelData:
    """Latest average retail prices for each fuel grade."""

    prices: dict[str, FuelPrice] = field(default_factory=dict)
    data_last_updated: str | None = None


def _latest(columns: list[dict[str, Any]]) -> tuple[str, float] | None:
    for column in reversed(columns or []):
        try:
            return str(column["key"]), float(column["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def parse_fuel_prices(payload: Any) -> FuelData:
    """Parse the SingStat TableBuilder JSON into latest fuel prices."""
    try:
        data = payload["Data"]
        rows = data["row"]
    except (KeyError, TypeError) as err:
        raise source_error(ERROR_PAYLOAD, SOURCE_FUEL, detail=repr(err)) from err

    prices: dict[str, FuelPrice] = {}
    for row in rows or []:
        text = " ".join(str(row.get("rowText", "")).split())
        for key, pattern in FUEL_SERIES.items():
            if key in prices or not pattern.search(text):
                continue
            if (latest := _latest(row.get("columns"))) is not None:
                period, value = latest
                prices[key] = FuelPrice(value=value, period=period, series=text)
            break

    if not prices:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_FUEL, detail="no known fuel price series"
        )
    return FuelData(prices=prices, data_last_updated=data.get("dataLastUpdated"))


class FuelPriceCoordinator(DataUpdateCoordinator[FuelData]):
    """Fetches monthly average Singapore pump fuel prices from SingStat."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="SingStat fuel prices",
            update_interval=FUEL_UPDATE_INTERVAL,
        )
        self.last_updated: datetime | None = None

    async def _async_update_data(self) -> FuelData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                FUEL_URL,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(ERROR_HTTP, SOURCE_FUEL, status=response.status)
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise source_error(ERROR_FETCH, SOURCE_FUEL, error=err) from err
        data = parse_fuel_prices(payload)
        self.last_updated = datetime.now(UTC)
        return data
