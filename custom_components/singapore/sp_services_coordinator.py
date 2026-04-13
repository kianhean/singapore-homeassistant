"""Coordinator for SP Services household energy and water usage data."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from sp_services import (
    ApiError,
    AuthenticationError,
    SessionExpiredError,
    SpServicesClient,
    SpServicesError,
    UsageData,
    UsagePoint,
)

_LOGGER = logging.getLogger(__name__)

_UPDATE_INTERVAL = timedelta(hours=1)
_SGT = ZoneInfo("Asia/Singapore")

CONF_SP_TOKEN = "sp_token"
_STAT_SOURCE = "singapore"

# Period string formats emitted by the SP Services API, tried in order.
_PERIOD_FORMATS = [
    "%Y-%m-%d %H:%M",  # "2026-04-11 14:00"  hourly
    "%Y-%m-%d",  # "2026-04-11"         daily
    "%Y-%m",  # "2026-04"            monthly
    "%d/%m/%Y %H:%M",  # "11/04/2026 14:00"
    "%d/%m/%Y",  # "11/04/2026"
]


def _parse_period(period: str) -> datetime | None:
    """Parse a UsagePoint period string to a timezone-aware datetime in SGT."""
    period = period.strip()
    for fmt in _PERIOD_FORMATS:
        try:
            return datetime.strptime(period, fmt).replace(tzinfo=_SGT)
        except ValueError:
            continue
    _LOGGER.debug("Could not parse SP Services period string: %r", period)
    return None


def _build_statistics(points: list[UsagePoint]) -> list:
    """Convert a list of UsagePoints to StatisticData with a cumulative sum.

    The sum starts at 0 at the earliest data point. HA uses differences in the
    cumulative sum to compute consumption over any time window, so the absolute
    baseline is irrelevant as long as it is consistent across calls.
    """
    from homeassistant.components.recorder.models import StatisticData

    parsed: list[tuple[datetime, float]] = []
    for point in points:
        dt = _parse_period(point.period)
        if dt is not None and point.value is not None:
            parsed.append((dt, float(point.value)))

    parsed.sort(key=lambda x: x[0])

    stats: list[StatisticData] = []
    running_sum = 0.0
    for dt, value in parsed:
        running_sum += value
        stats.append(StatisticData(start=dt, state=value, sum=running_sum))
    return stats


def _stats_slot(dt: datetime | None) -> tuple | None:
    """Return (SGT date, 8-hour slot index) for a UTC datetime, or None.

    Slots correspond to midnight, 08:00, and 16:00 SGT so statistics are
    refreshed three times a day — early enough to catch SP's data updates.
    Returns None when dt is None so an uninitialised coordinator always pushes
    on its first successful fetch.
    """
    if dt is None:
        return None
    sgt = dt.astimezone(_SGT)
    return (sgt.date(), sgt.hour // 8)


def _account_slug(usage_data: UsageData, entry_id: str) -> str:
    raw = usage_data.account_no or entry_id
    return re.sub(r"[^a-z0-9]", "_", raw.lower()).strip("_")


class SpServicesCoordinator(DataUpdateCoordinator[UsageData]):
    """Fetches household electricity and water usage from SP Services portal."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._last_stats_push: datetime | None = None
        self._client: SpServicesClient | None = None
        super().__init__(
            hass,
            _LOGGER,
            name="SP Services Usage",
            update_interval=_UPDATE_INTERVAL,
        )

    async def async_close(self) -> None:
        """Close the persistent HTTP session."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _async_update_data(self) -> UsageData:
        token = self._entry.data.get(CONF_SP_TOKEN)
        if not token:
            raise UpdateFailed("No SP Services token in entry data")

        if self._client is None:
            self._client = SpServicesClient()

        try:
            data = await self._client.fetch_usage(token)
        except (SessionExpiredError, AuthenticationError) as err:
            # Drop the stale session so the next attempt starts fresh.
            await self.async_close()
            if isinstance(err, SessionExpiredError):
                raise ConfigEntryAuthFailed("SP Services session expired") from err
            raise ConfigEntryAuthFailed(str(err)) from err
        except (ApiError, SpServicesError) as err:
            raise UpdateFailed(f"SP Services error: {err}") from err

        now = datetime.now(tz=timezone.utc)
        if _stats_slot(now) != _stats_slot(self._last_stats_push):
            try:
                self._push_statistics(data)
                self._last_stats_push = now
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Failed to push SP Services statistics", exc_info=True)

        return data

    def _push_statistics(self, usage_data: UsageData) -> None:
        """Push SP Services history into HA long-term statistics (recorder).

        Electricity: hourly history preferred; daily history used as fallback.
        Water: monthly history only (finest granularity the API provides).
        Failures are logged and swallowed — statistics are best-effort.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.models import StatisticMetaData
            from homeassistant.components.recorder.statistics import (
                async_add_external_statistics,
            )
        except ImportError:
            _LOGGER.debug("Recorder not available; skipping statistics push")
            return

        if get_instance(self.hass) is None:
            return

        slug = _account_slug(usage_data, self._entry.entry_id)

        # ---- Electricity ---------------------------------------------------
        elec_points = (
            usage_data.electricity_hourly_history
            or usage_data.electricity_daily_history
        )
        if elec_points:
            elec_stats = _build_statistics(elec_points)
            if elec_stats:
                async_add_external_statistics(
                    self.hass,
                    StatisticMetaData(
                        has_mean=False,
                        has_sum=True,
                        name=f"SP Electricity ({slug})",
                        source=_STAT_SOURCE,
                        statistic_id=f"{_STAT_SOURCE}:sp_electricity_{slug}",
                        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                    ),
                    elec_stats,
                )

        # ---- Water (monthly granularity) -----------------------------------
        water_points = usage_data.water_monthly_history
        if water_points:
            water_stats = _build_statistics(water_points)
            if water_stats:
                async_add_external_statistics(
                    self.hass,
                    StatisticMetaData(
                        has_mean=False,
                        has_sum=True,
                        name=f"SP Water ({slug})",
                        source=_STAT_SOURCE,
                        statistic_id=f"{_STAT_SOURCE}:sp_water_{slug}",
                        unit_of_measurement=UnitOfVolume.CUBIC_METERS,
                    ),
                    water_stats,
                )
