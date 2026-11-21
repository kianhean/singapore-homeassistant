"""Singapore interest rates (SORA, T-bill and SGS bond yields) from SingStat.

SingStat TableBuilder table M700071 "Current Banks Interest Rates (End Of
Period), Monthly" republishes MAS data key-free:
https://tablebuilder.singstat.gov.sg/api/table/tabledata/M700071

Values are end-of-month, so they change once a month (SingStat updates the
table mid-month for the previous month).
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

from .const import SOURCE_INTEREST
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

SINGSTAT_URL = "https://tablebuilder.singstat.gov.sg/api/table/tabledata/M700071"
INTEREST_UPDATE_INTERVAL = timedelta(hours=12)

# Sensor key -> pattern matched against the SingStat row text. Order = sensor order.
INTEREST_SERIES: dict[str, re.Pattern[str]] = {
    "sora": re.compile(r"^singapore overnight rate average$", re.I),
    "sora_3m": re.compile(r"compounded .*sora.*3[- ]month", re.I),
    "tbill_1y": re.compile(r"1-year treasury bills? yield", re.I),
    "sgs_2y": re.compile(r"2-year bond yield", re.I),
    "sgs_5y": re.compile(r"5-year bond yield", re.I),
    "sgs_10y": re.compile(r"10-year bond yield", re.I),
    "sgs_15y": re.compile(r"15-year bond yield", re.I),
    "sgs_20y": re.compile(r"20-year bond yield", re.I),
}
INTEREST_SERIES_KEYS: tuple[str, ...] = tuple(INTEREST_SERIES)


@dataclass
class InterestRate:
    """Latest value of one series."""

    value: float
    period: str  # e.g. "2026 Oct"
    series: str  # SingStat row text


@dataclass
class InterestData:
    """Latest value of each series found in the table."""

    rates: dict[str, InterestRate] = field(default_factory=dict)
    data_last_updated: str | None = None  # SingStat's "dataLastUpdated"


def _latest(columns: list[dict[str, Any]]) -> tuple[str, float] | None:
    for column in reversed(columns or []):
        try:
            return str(column["key"]), float(column["value"])
        except (KeyError, TypeError, ValueError):
            continue  # "na" / blank months
    return None


def parse_singstat(payload: Any) -> InterestData:
    """Parse the TableBuilder JSON into the latest value of each series."""
    try:
        data = payload["Data"]
        rows = data["row"]
    except (KeyError, TypeError) as err:
        raise source_error(ERROR_PAYLOAD, SOURCE_INTEREST, detail=repr(err)) from err

    rates: dict[str, InterestRate] = {}
    for row in rows or []:
        text = " ".join(str(row.get("rowText", "")).split())
        for key, pattern in INTEREST_SERIES.items():
            if key in rates or not pattern.search(text):
                continue
            if (latest := _latest(row.get("columns"))) is not None:
                period, value = latest
                rates[key] = InterestRate(value=value, period=period, series=text)
            break

    if not rates:
        raise source_error(
            ERROR_PAYLOAD, SOURCE_INTEREST, detail="no known interest rate series"
        )
    return InterestData(rates=rates, data_last_updated=data.get("dataLastUpdated"))


class InterestRateCoordinator(DataUpdateCoordinator[InterestData]):
    """Fetches MAS interest rates via SingStat TableBuilder."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="SingStat interest rates",
            update_interval=INTEREST_UPDATE_INTERVAL,
        )
        self.last_updated: datetime | None = None

    async def _async_update_data(self) -> InterestData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                SINGSTAT_URL,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(
                        ERROR_HTTP, SOURCE_INTEREST, status=response.status
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise source_error(ERROR_FETCH, SOURCE_INTEREST, error=err) from err
        data = parse_singstat(payload)
        self.last_updated = datetime.now(UTC)
        return data
