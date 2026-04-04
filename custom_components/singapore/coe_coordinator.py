"""Data coordinator for Singapore COE (Certificate of Entitlement) results."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

COE_API_URL = (
    "https://data.gov.sg/api/action/datastore_search"
    "?resource_id=d_69b3380ad7e51aff3a7dcc84eba52b8a"
    "&limit=10"
    "&sort=month%20desc%2Cbidding_no%20desc"
)

UPDATE_INTERVAL = timedelta(hours=1)

UNIT_COE = "SGD"

# COE vehicle categories
COE_CATEGORIES = ("A", "B", "C", "D", "E")

_CATEGORY_NAMES = {
    "A": "Singapore COE Category A",
    "B": "Singapore COE Category B",
    "C": "Singapore COE Category C",
    "D": "Singapore COE Category D",
    "E": "Singapore COE Category E (Open)",
}

_CATEGORY_DESCRIPTIONS = {
    "A": "Cars up to 1600cc / 97kW (electric)",
    "B": "Cars above 1600cc / 97kW (electric)",
    "C": "Goods vehicles and buses",
    "D": "Motorcycles",
    "E": "Open (all except motorcycles)",
}


@dataclass
class CoeData:
    """Latest COE bidding results for all categories."""

    premiums: dict[str, int]  # category letter -> premium in SGD, e.g. {"A": 95501}
    month: str  # e.g. "2026-03"
    bidding_no: int  # 1 or 2 (two exercises per month)


class CoeCoordinator(DataUpdateCoordinator[CoeData]):
    """Fetches and caches the latest COE bidding results from data.gov.sg."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="COE Bidding Results",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> CoeData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(COE_API_URL, timeout=30) as response:
                if response.status != 200:
                    raise UpdateFailed(f"data.gov.sg returned HTTP {response.status}")
                payload = await response.json()
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching COE results: {err}") from err

        return _parse_coe(payload)


def _parse_coe(payload: dict) -> CoeData:
    """Parse COE API response and return the latest bidding exercise."""
    try:
        records = payload["result"]["records"]
    except (KeyError, TypeError) as err:
        raise UpdateFailed(f"Unexpected COE API response structure: {err}") from err

    if not records:
        raise UpdateFailed("COE API returned no records")

    # Records are sorted month desc, bidding_no desc — the first record gives
    # the most recent bidding exercise.
    latest_month = records[0]["month"]
    latest_bidding_no = int(records[0]["bidding_no"])

    premiums: dict[str, int] = {}
    for rec in records:
        if rec["month"] != latest_month or int(rec["bidding_no"]) != latest_bidding_no:
            break
        raw_class = str(rec.get("vehicle_class", "")).strip()
        # vehicle_class is stored as "Category A", "Category B", etc.
        cat = raw_class.replace("Category", "").strip().upper()
        if cat in COE_CATEGORIES:
            try:
                premiums[cat] = int(rec["premium"])
            except (ValueError, KeyError):
                _LOGGER.warning("Could not parse premium for COE category %s", cat)

    if not premiums:
        raise UpdateFailed("Could not parse any COE premiums from API response")

    return CoeData(
        premiums=premiums,
        month=latest_month,
        bidding_no=latest_bidding_no,
    )
