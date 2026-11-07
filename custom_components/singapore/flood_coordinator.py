"""Data coordinator for PUB flood alerts (data.gov.sg real-time API)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

FLOOD_ALERTS_URL = "https://api-open.data.gov.sg/v2/real-time/api/weather/flood-alerts"
# PUB publishes an observation snapshot every 2 minutes.
UPDATE_INTERVAL = timedelta(minutes=2)

# Every PUB alert should be followed by a "Cancel" message once the flood
# subsides. If that Cancel is ever missed (e.g. HA was offline when it was
# published), drop the alert after this long so it doesn't stay active forever.
ALERT_MAX_AGE = timedelta(hours=3)

_SGT = timezone(timedelta(hours=8))


@dataclass
class FloodAlert:
    """A single active PUB flash-flood alert."""

    identifier: str
    issued: datetime
    headline: str
    description: str
    area: str
    instruction: str
    severity: str
    urgency: str
    latitude: float | None = None
    longitude: float | None = None

    def as_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "issued": self.issued.isoformat(),
            "headline": self.headline,
            "description": self.description,
            "area": self.area,
            "instruction": self.instruction,
            "severity": self.severity,
            "urgency": self.urgency,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass
class FloodAlertData:
    """Currently active flood alerts, newest first."""

    alerts: list[FloodAlert] = field(default_factory=list)
    last_observation: datetime | None = None


@dataclass
class _FloodMessage:
    """One parsed CAP message (Alert or Cancel) from the API."""

    identifier: str
    msg_type: str
    references: str
    sent: datetime
    alerts: list[FloodAlert]


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SGT)
    return parsed


def _parse_reading(identifier: str, sent: datetime, reading: dict) -> FloodAlert:
    area = reading.get("area") or {}
    circle = area.get("circle") or []
    lat = lon = None
    if isinstance(circle, list) and len(circle) >= 2:
        try:
            lat, lon = float(circle[0]), float(circle[1])
        except (TypeError, ValueError):
            lat = lon = None
    return FloodAlert(
        identifier=identifier,
        issued=sent,
        headline=str(reading.get("headline") or "Flash Flood Alert"),
        description=str(reading.get("description") or ""),
        area=str(area.get("areaDesc") or ""),
        instruction=str(reading.get("instruction") or ""),
        severity=str(reading.get("severity") or ""),
        urgency=str(reading.get("urgency") or ""),
        latitude=lat,
        longitude=lon,
    )


def _parse_flood_messages(payload: dict) -> tuple[list[_FloodMessage], datetime | None]:
    """Parse the flood-alerts payload into Alert/Cancel messages, oldest first.

    Expected shape::

        {"code": 0, "data": {"records": [
            {"datetime": "...", "item": {
                "identifier": "...", "msgType": "Alert" | "Cancel",
                "references": "sender, identifier, sent", "readings": [...]}}
        ]}}

    Records with empty ``readings`` and no ``msgType`` are routine "nothing to
    report" observations and are skipped. Raises ``UpdateFailed`` if the payload
    doesn't have the expected envelope at all.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise UpdateFailed("Unexpected flood-alerts payload shape")
    records = payload["data"].get("records")
    if not isinstance(records, list):
        raise UpdateFailed("Flood-alerts payload has no records list")

    messages: list[_FloodMessage] = []
    latest: datetime | None = None
    for record in records:
        if not isinstance(record, dict):
            continue
        sent = _parse_datetime(record.get("datetime"))
        if sent is None:
            continue
        if latest is None or sent > latest:
            latest = sent
        item = record.get("item") or {}
        msg_type = str(item.get("msgType") or "")
        readings = item.get("readings") or []
        if not msg_type and not readings:
            continue
        identifier = str(item.get("identifier") or "")
        if not identifier:
            # Without an identifier we can't pair it with its Cancel; synthesise
            # a stable one from the timestamp + first description.
            first_desc = readings[0].get("description", "") if readings else ""
            identifier = f"{sent.isoformat()}|{first_desc}"
        messages.append(
            _FloodMessage(
                identifier=identifier,
                msg_type=msg_type or "Alert",
                references=str(item.get("references") or ""),
                sent=sent,
                alerts=[
                    _parse_reading(identifier, sent, r)
                    for r in readings
                    if isinstance(r, dict)
                ],
            )
        )
    messages.sort(key=lambda m: m.sent)
    return messages, latest


def _apply_messages(
    active: dict[str, list[FloodAlert]],
    messages: list[_FloodMessage],
    now: datetime,
) -> dict[str, list[FloodAlert]]:
    """Fold Alert/Cancel messages into the active-alert map and expire stale ones."""
    active = dict(active)
    cancelled: set[str] = set()
    for msg in messages:
        if msg.msg_type.lower() == "cancel":
            cancelled.add(msg.identifier)
            for ident in [i for i in active if i in msg.references]:
                active.pop(ident)
                cancelled.add(ident)
        elif msg.identifier not in cancelled and msg.alerts:
            active[msg.identifier] = msg.alerts
    return {
        ident: alerts
        for ident, alerts in active.items()
        if alerts and now - alerts[0].issued < ALERT_MAX_AGE
    }


class FloodAlertCoordinator(DataUpdateCoordinator[FloodAlertData]):
    """Polls PUB flood alerts and tracks which ones are still active."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore PUB Flood Alerts",
            update_interval=UPDATE_INTERVAL,
            # Parsed data is a dataclass, so unchanged polls skip state writes.
            always_update=False,
        )
        self._active: dict[str, list[FloodAlert]] = {}

    async def _async_update_data(self) -> FloodAlertData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                FLOOD_ALERTS_URL, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"data.gov.sg flood alerts returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching flood alerts (%s); using last known values", err
                )
                return self.data
            raise UpdateFailed(f"Error fetching flood alerts: {err}") from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching flood alerts (%s); using last known values", err
                )
                return self.data
            raise

        messages, latest = _parse_flood_messages(payload)
        self._active = _apply_messages(self._active, messages, datetime.now(UTC))
        alerts = sorted(
            (a for group in self._active.values() for a in group),
            key=lambda a: a.issued,
            reverse=True,
        )
        return FloodAlertData(alerts=alerts, last_observation=latest)
