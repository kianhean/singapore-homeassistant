"""Data coordinator for Singapore MRT/LRT train service status."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from bs4 import BeautifulSoup
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

TRAIN_STATUS_URL = "https://www.mytransport.sg/trainstatus#"
UPDATE_INTERVAL = timedelta(minutes=5)
TRAIN_LINES: Final[tuple[str, ...]] = (
    "North-South Line",
    "East-West Line",
    "North East Line",
    "Circle Line",
    "Downtown Line",
    "Thomson-East Coast Line",
    "Bukit Panjang LRT",
    "Sengkang LRT",
    "Punggol LRT",
)
_LINE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "North-South Line": ("north-south line", "nsl"),
    "East-West Line": ("east-west line", "ewl"),
    "North East Line": ("north east line", "north-east line", "nel"),
    "Circle Line": ("circle line", "ccl"),
    "Downtown Line": ("downtown line", "dtl"),
    "Thomson-East Coast Line": ("thomson-east coast line", "tel"),
    "Bukit Panjang LRT": ("bukit panjang lrt", "bplrt"),
    "Sengkang LRT": ("sengkang lrt", "sklrt"),
    "Punggol LRT": ("punggol lrt", "pglrt"),
}


@dataclass
class TrainStatusData:
    """Parsed MRT/LRT network status."""

    status: str
    details: str
    line_statuses: dict[str, str]


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
            return _parse_train_status(html)
        except Exception as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching train status data (%s); using last known values",
                    err,
                )
                return self.data
            raise UpdateFailed(f"Error fetching train status data: {err}") from err


def _parse_train_status(html: str) -> TrainStatusData:
    """Parse overall train status from the MRT/LRT status page text."""
    soup = BeautifulSoup(html, "html.parser")
    raw_text = soup.get_text("\n", strip=True)
    text = soup.get_text(" ", strip=True)
    lowered = text.lower()

    if _looks_planned(lowered):
        status = "planned"
    elif _looks_disrupted(lowered):
        status = "disruption"
    else:
        status = "normal"

    details = _extract_detail(text, status)
    line_statuses = _extract_line_statuses(raw_text, status)
    return TrainStatusData(status=status, details=details, line_statuses=line_statuses)


_DISRUPTION_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bdisruption\b",
        r"\bservice alert\b",
        r"\bservice (?:delay|delays)\b",
        r"\bminor delay\b",
        r"\bmajor disruption\b",
        r"\bincident\b",
        r"\byellow\b",
        r"\borange\b",
    )
)

_PLANNED_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b[a-z]{2,5}-planned\b",
        r"\bplanned disruption(?:s)?\b",
        r"\bplanned maintenance\b",
        r"\bplanned train service adjustment(?:s)?\b",
        r"\bplanned train service\b",
        r"\bplanned(?:\s+\w+){0,3}\s+works?\b",
    )
)


def _looks_disrupted(text: str) -> bool:
    return any(pattern.search(text) for pattern in _DISRUPTION_PATTERNS)


def _looks_planned(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PLANNED_PATTERNS)


def _extract_detail(text: str, status: str) -> str:
    """Extract a compact detail snippet for attributes/debugging."""
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return "No detail available from source page"
    if status in {"planned", "disruption"}:
        return clean
    return clean[:240]


def _extract_line_statuses(text: str, network_status: str) -> dict[str, str]:
    """Extract per-line status from page text.

    Lines not explicitly mentioned with a disruption/planned keyword are
    assumed normal — the live page only shows status text for affected lines;
    unaffected lines show only a green checkmark with no text.

    If no per-line sentences match but the network has a non-normal status,
    it is treated as a full-network event and all lines are flagged.
    """
    line_statuses = {line: "normal" for line in TRAIN_LINES}
    sentences = [
        chunk.strip()
        for chunk in re.split(r"[\n.!?;]+", text)
        if chunk and chunk.strip()
    ]

    found_any = False
    for sentence in sentences:
        lowered = sentence.lower()
        sentence_status = _classify_sentence_status(lowered)
        if sentence_status is None:
            continue
        for line, aliases in _LINE_ALIASES.items():
            if any(alias in lowered for alias in aliases):
                line_statuses[line] = sentence_status
                found_any = True

    # No per-line sentences matched → full-network event; flag all lines.
    if network_status != "normal" and not found_any:
        for line in TRAIN_LINES:
            line_statuses[line] = network_status

    return line_statuses


def _classify_sentence_status(text: str) -> str | None:
    """Map a sentence to a canonical status."""
    if _looks_planned(text):
        return "planned"
    if (
        "normal" in text
        or "operating normally" in text
        or "no delays" in text
        or "no disruption" in text
    ):
        return "normal"
    if _looks_disrupted(text):
        return "disruption"
    return None
