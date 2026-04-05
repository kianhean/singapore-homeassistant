"""Data coordinator for Singapore MRT/LRT train service status."""

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

TRAIN_STATUS_URL = "https://www.mytransport.sg/trainstatus#"
UPDATE_INTERVAL = timedelta(minutes=5)


@dataclass
class TrainStatusData:
    """Parsed MRT/LRT network status."""

    status: str
    details: str


class TrainStatusCoordinator(DataUpdateCoordinator[TrainStatusData]):
    """Fetches and caches train-status data from mytransport.sg."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Singapore MRT/LRT Train Status",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> TrainStatusData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(TRAIN_STATUS_URL, timeout=30) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"mytransport.sg train status returned HTTP {response.status}"
                    )
                html = await response.text()
        except Exception as err:
            raise UpdateFailed(f"Error fetching train status data: {err}") from err

        return _parse_train_status(html)


def _parse_train_status(html: str) -> TrainStatusData:
    """Parse overall train status from the MRT/LRT status page text."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    lowered = text.lower()

    if "planned disruption" in lowered or "planned maintenance" in lowered:
        status = "planned"
    elif _looks_disrupted(lowered):
        status = "disruption"
    else:
        status = "normal"

    details = _extract_detail(text)
    return TrainStatusData(status=status, details=details)


def _looks_disrupted(text: str) -> bool:
    disruption_patterns = (
        r"\bdisruption\b",
        r"\bservice alert\b",
        r"\bservice (?:delay|delays)\b",
        r"\bminor delay\b",
        r"\bmajor disruption\b",
        r"\bincident\b",
        r"\byellow\b",
        r"\borange\b",
    )
    return any(
        re.search(pattern, text, re.IGNORECASE) for pattern in disruption_patterns
    )


def _extract_detail(text: str) -> str:
    """Extract a compact detail snippet for attributes/debugging."""
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return "No detail available from source page"
    return clean[:240]
