"""Data coordinator for Singapore MRT/LRT train service status."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

# AEM servlet that proxies to LTA DataMall TrainServiceAlerts.
# Must be called via POST with form-encoded body; page renders via JS so plain
# HTML scraping returns only a "Loading…" shell.
TRAIN_STATUS_URL = (
    "https://www.mytransport.sg"
    "/content/ltagov/en/map/train/jcr:content/left-menu/ltaDatamallAPI"
    ".ltaDatamallAPI.POST.html"
)
_TRAIN_STATUS_REFERER = "https://www.mytransport.sg/trainstatus#"

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

# Pre-compiled word-boundary patterns for each line's aliases so that short codes
# like "nel" don't match substrings like "tunnel".
_LINE_ALIAS_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    line: re.compile(
        "|".join(r"\b" + re.escape(alias) + r"\b" for alias in aliases),
        re.IGNORECASE,
    )
    for line, aliases in _LINE_ALIASES.items()
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
            async with session.post(
                TRAIN_STATUS_URL,
                data={"serviceName": "LTAGOVTrainServiceAlerts", "param": ""},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": _TRAIN_STATUS_REFERER,
                },
                timeout=30,
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"mytransport.sg train status returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
            return _parse_train_status(payload)
        except Exception as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching train status data (%s); using last known values",
                    err,
                )
                return self.data
            raise UpdateFailed(f"Error fetching train status data: {err}") from err


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


def _classify_message_status(content: str) -> str | None:
    """Map a message string to a canonical status."""
    lowered = content.lower()
    if _looks_planned(lowered):
        return "planned"
    if _looks_disrupted(lowered):
        return "disruption"
    return None


def _parse_train_status(data: dict) -> TrainStatusData:
    """Parse overall train status from the LTA DataMall TrainServiceAlerts JSON payload.

    Expected shape::

        {
          "value": {
            "Status": 1,
            "AffectedSegments": [{"Line": "NSL", ...}, ...],
            "Message": [{"Content": "21:00-CCL-Planned ...", "CreatedDate": "..."}, ...]
          }
        }

    ``AffectedSegments`` carries real-time disruptions; ``Message`` carries planned
    and informational notices.  Only messages that mention a known line code are
    used for per-line classification.
    """
    value: dict = (data.get("value") or {}) if isinstance(data, dict) else {}
    affected_segments: list[dict] = value.get("AffectedSegments") or []
    messages: list[dict] = value.get("Message") or []

    line_statuses: dict[str, str] = {line: "normal" for line in TRAIN_LINES}

    # Real-time disruptions from AffectedSegments
    for seg in affected_segments:
        seg_text = seg.get("Line", "") + " " + seg.get("Direction", "")
        for line, pattern in _LINE_ALIAS_PATTERNS.items():
            if pattern.search(seg_text):
                line_statuses[line] = "disruption"

    # Planned / informational notices from Message array
    content_parts: list[str] = []
    for msg in messages:
        content = msg.get("Content", "")
        if not content:
            continue
        content_parts.append(content)
        msg_status = _classify_message_status(content)
        if msg_status is None:
            continue
        for line, pattern in _LINE_ALIAS_PATTERNS.items():
            if pattern.search(content):
                # Never downgrade a line already marked disrupted
                if line_statuses[line] != "disruption":
                    line_statuses[line] = msg_status

    # Overall network status = worst per-line status
    statuses = set(line_statuses.values())
    if "disruption" in statuses:
        status = "disruption"
    elif "planned" in statuses:
        status = "planned"
    else:
        status = "normal"

    details = " | ".join(content_parts) if status != "normal" else ""
    return TrainStatusData(status=status, details=details, line_statuses=line_statuses)
