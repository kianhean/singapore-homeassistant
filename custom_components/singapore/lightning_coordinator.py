"""Data coordinator for NEA lightning observations (data.gov.sg real-time API)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .dengue_coordinator import _haversine_km

_LOGGER = logging.getLogger(__name__)

LIGHTNING_URL = "https://api-open.data.gov.sg/v2/real-time/api/weather?api=lightning"
LIGHTNING_DATASET_PAGE_URL = (
    "https://data.gov.sg/datasets/d_08238953fe0f6dd13f10714ebfbcb9f9/view"
)
# MSS's Lightning Detection System publishes to data.gov.sg every 2 minutes.
UPDATE_INTERVAL = timedelta(minutes=2)
# Each response only holds the latest 2-minute observation, so strikes are kept
# across polls and reported for this rolling window.
STRIKE_WINDOW = timedelta(minutes=30)

# NEA's "type" codes.
STRIKE_TYPES = {"G": "cloud_to_ground", "C": "cloud_to_cloud"}

_SGT = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class LightningStrike:
    """One detected lightning stroke."""

    time: datetime
    latitude: float
    longitude: float
    type: str  # "cloud_to_ground" / "cloud_to_cloud" / "unknown"

    def as_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "type": self.type,
        }


@dataclass
class LightningData:
    """Strikes within STRIKE_WINDOW, newest first."""

    strikes: list[LightningStrike] = field(default_factory=list)
    last_observation: datetime | None = None

    @property
    def cloud_to_ground(self) -> int:
        return sum(1 for s in self.strikes if s.type == "cloud_to_ground")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_SGT)


def _parse_lightning(payload: dict) -> tuple[list[LightningStrike], datetime | None]:
    """Parse strikes and the newest observation time from the v2 payload.

    Shape: ``data.records[].item.readings[]`` with ``location.latitude`` /
    ``location.longitude`` (strings), ``type`` (``G`` / ``C``) and ``datetime``.
    No lightning means empty ``readings``. Raises ``UpdateFailed`` if the
    envelope has no ``records`` list.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise UpdateFailed("Lightning payload has no data.records list")

    strikes: list[LightningStrike] = []
    latest: datetime | None = None
    for record in records:
        if not isinstance(record, dict):
            continue
        observed = _parse_time(record.get("datetime"))
        if observed and (latest is None or observed > latest):
            latest = observed
        item = record.get("item") or {}
        readings = item.get("readings") if isinstance(item, dict) else None
        for reading in readings or []:
            if not isinstance(reading, dict):
                continue
            location = reading.get("location") or {}
            try:
                lat = float(location.get("latitude"))
                lon = float(location.get("longitude"))
            except (TypeError, ValueError):
                continue
            when = _parse_time(reading.get("datetime")) or observed
            if when is None:
                continue
            strikes.append(
                LightningStrike(
                    time=when,
                    latitude=lat,
                    longitude=lon,
                    type=STRIKE_TYPES.get(str(reading.get("type")), "unknown"),
                )
            )
    return strikes, latest


def _merge_strikes(
    known: set[LightningStrike], new: list[LightningStrike], now: datetime
) -> set[LightningStrike]:
    """Add new strikes and drop any older than STRIKE_WINDOW."""
    cutoff = now - STRIKE_WINDOW
    return {s for s in (known | set(new)) if s.time >= cutoff}


def strike_distance_km(lat: float, lon: float, strike: LightningStrike) -> float:
    """Great-circle distance from a point to a strike."""
    return _haversine_km(lat, lon, strike.latitude, strike.longitude)


def nearest_strike(
    lat: float, lon: float, data: LightningData
) -> tuple[LightningStrike, float] | None:
    """Return (strike, distance_km) for the strike closest to the point."""
    best: tuple[LightningStrike, float] | None = None
    for strike in data.strikes:
        dist = strike_distance_km(lat, lon, strike)
        if best is None or dist < best[1]:
            best = (strike, dist)
    return best


class LightningCoordinator(DataUpdateCoordinator[LightningData]):
    """Polls NEA lightning observations and keeps a rolling window of strikes."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore NEA Lightning",
            update_interval=UPDATE_INTERVAL,
            # Parsed data is a dataclass, so unchanged polls skip state writes.
            always_update=False,
        )
        self._strikes: set[LightningStrike] = set()

    async def _async_update_data(self) -> LightningData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                LIGHTNING_URL, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(
                        f"data.gov.sg lightning returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching lightning (%s); using last known values", err
                )
                return self._snapshot(self.data.last_observation)
            raise UpdateFailed(f"Error fetching lightning: {err}") from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching lightning (%s); using last known values", err
                )
                return self._snapshot(self.data.last_observation)
            raise

        strikes, latest = _parse_lightning(payload)
        self._strikes = _merge_strikes(self._strikes, strikes, datetime.now(UTC))
        return self._snapshot(latest)

    def _snapshot(self, latest: datetime | None) -> LightningData:
        # Re-apply the window even on a failed poll so old strikes age out.
        self._strikes = _merge_strikes(self._strikes, [], datetime.now(UTC))
        return LightningData(
            strikes=sorted(self._strikes, key=lambda s: s.time, reverse=True),
            last_observation=latest,
        )
