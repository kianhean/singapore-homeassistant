"""SpAccountCoordinator: polls one SP Group e-account (usage, bills, meters).

Adapted from maci0/home-assistant-sp (MIT, Copyright (c) 2026 Marcel W.
Wysocki): https://github.com/maci0/home-assistant-sp. The protocol client,
models, mapper and statistics helpers are vendored unchanged under
``sp_account/``; this module is the Home Assistant glue, reworked so that each
e-account is a *config subentry* of the single ``singapore`` entry rather than
its own config entry.

Differences from the upstream coordinator:

- Credentials and tokens live on the subentry, so a refreshed session is
  persisted with ``async_update_subentry``.
- A rejected login cannot start a reauth flow (subentries have none, and a
  reauth on the parent entry would take every other Singapore sensor down).
  It raises ``UpdateFailed`` and opens a repair issue that points the user at
  the subentry's *Reconfigure* menu; a later successful poll deletes the issue.
- The first refresh runs in the background like the other optional
  coordinators, so an SP outage never blocks integration setup.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .coordinator import SPGroupCoordinator
from .sp_account.client import (
    AuthError,
    Session,
    SpGroupClient,
    UsageError,
    session_entry_data,
)
from .sp_account.const import (
    CONF_ACCESS_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    OAUTH_ERROR_REQUIRES_VERIFICATION,
    SENSOR_KEY_ELECTRICITY,
    SENSOR_KEY_GAS,
    SENSOR_KEY_LAST_BILL,
    SENSOR_KEY_WATER,
    SLOW_POLL_MS,
    STATISTIC_KEY_ELECTRICITY_COST,
    STATISTIC_NAME_BILL,
    STATISTIC_NAME_ELECTRICITY,
    STATISTIC_NAME_ELECTRICITY_COST,
    STATISTIC_NAME_GAS,
    UNIT_KWH,
    UNIT_M3,
    UNIT_SGD,
    UPDATE_INTERVAL,
    translated_error,
)
from .sp_account.history import (
    CumulativePoint,
    HasStart,
    cost_points,
    cumulative_points,
    external_statistic_id,
    monthly_bill_points,
    unimported,
)
from .sp_account.mapper import SensorSpec, UsageViewCache, extra_attributes
from .sp_account.models import PeriodReading, UsageReadings

_LOGGER = logging.getLogger(__name__)

SUBENTRY_TYPE_SP_ACCOUNT = "sp_account"
ISSUE_AUTH_FAILED = "sp_account_auth_failed"

# Statistic names are written to the database once and never translated, so
# they stay fixed English, like the vendored STATISTIC_NAME_* constants.
STATISTIC_NAME_WATER = "SP Group water"

_PointT = TypeVar("_PointT", bound=HasStart)


def auth_issue_id(subentry_id: str) -> str:
    """Repair issue id for one subentry's rejected credentials."""
    return f"{ISSUE_AUTH_FAILED}_{subentry_id}"


def session_from_data(data: Mapping[str, Any]) -> Session | None:
    """The stored session, or None when the subentry holds no usable tokens."""
    access = data.get(CONF_ACCESS_TOKEN)
    ident = data.get(CONF_ID_TOKEN)
    refresh = data.get(CONF_REFRESH_TOKEN)
    if isinstance(access, str) and isinstance(ident, str) and access and ident:
        return Session(
            access_token=access,
            id_token=ident,
            refresh_token=refresh if isinstance(refresh, str) else None,
        )
    return None


def same_tokens(a: Session | None, b: Session | None) -> bool:
    """Whether two sessions carry the same tokens (expiry/scope are derived)."""
    if a is None or b is None:
        return a is b
    return (a.access_token, a.id_token, a.refresh_token or None) == (
        b.access_token,
        b.id_token,
        b.refresh_token or None,
    )


def _cumulative(point: CumulativePoint) -> float:
    return point.cumulative


def _amount(point: PeriodReading) -> float:
    return point.amount


class SpAccountCoordinator(DataUpdateCoordinator[UsageReadings]):
    """Polls one SP e-account every 30 minutes and imports Energy statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        tariff: SPGroupCoordinator | None = None,
        client: SpGroupClient | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN} SP account {subentry.title}",
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.entry = entry
        self.subentry_id = subentry.subentry_id
        # The integration's own SP Group tariff scrape prices the cost series.
        self.tariff = tariff
        self.client = client or SpGroupClient(session=session_from_data(subentry.data))
        self.last_error: str | None = None
        self.last_poll_ms: int | None = None
        self.last_success: datetime | None = None
        self._stats_lock = asyncio.Lock()
        self._stats_task: asyncio.Task[None] | None = None
        self._stats_stopped = False
        self._imported_price: float | None = None
        self._imported_premise: str | None = None
        self._imported_points: dict[str, datetime] = {}
        self._spec_cache = UsageViewCache()
        self._now = self.client.clock.now()

    # ------------------------------------------------------------------
    # Subentry access
    # ------------------------------------------------------------------

    @property
    def subentry(self) -> ConfigSubentry | None:
        """The current subentry (its data changes as tokens are persisted)."""
        return self.entry.subentries.get(self.subentry_id)

    @property
    def title(self) -> str:
        subentry = self.subentry
        return subentry.title if subentry else self.subentry_id

    # ------------------------------------------------------------------
    # Sensor specs
    # ------------------------------------------------------------------

    @property
    def sensor_specs(self) -> tuple[SensorSpec, ...]:
        """The current sensor specs, built once per poll for every entity."""
        return self._spec_cache.specs(self.data, self.now)

    def spec_for(self, key: str) -> SensorSpec | None:
        """One spec by key, so an entity does not scan the list to find it."""
        return self._spec_cache.spec_for(self.data, self.now, key)

    @property
    def now(self) -> datetime:
        """The clock reading of the last successful poll (shared by every entity)."""
        return self._now

    def extra_attributes(self, key: str) -> dict[str, object]:
        usage = self.data
        if usage is None:
            return {}
        now = self.now
        return extra_attributes(usage, key, now, self._spec_cache.view(usage, now))

    @property
    def electricity_price(self) -> float | None:
        """SGD/kWh for the cost series: the scraped SP Group tariff (incl. GST).

        Upstream asked the user for a fixed price; this integration already
        scrapes the regulated tariff (in ¢/kWh), so it is used instead. None
        until the tariff has loaded, which skips the cost series.
        """
        data = self.tariff.data if self.tariff is not None else None
        if data is None or not data.electricity_price:
            return None
        return round(data.electricity_price / 100, 6)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_subentry_updated(self) -> None:
        """React to a subentry change without reloading the whole integration.

        Two things change the subentry: this coordinator persisting a refreshed
        session (nothing to do), and the reconfigure flow storing a new login,
        which replaces the client's session and polls at once.
        """
        subentry = self.subentry
        if subentry is None:
            return
        stored = session_from_data(subentry.data)
        if stored is not None and not same_tokens(stored, self.client.session):
            self.client = SpGroupClient(session=stored)
            await self.async_request_refresh()

    async def async_tariff_updated(self) -> None:
        """Re-price the cost series when the scraped tariff changes (quarterly)."""
        if self.electricity_price != self._imported_price:
            await self.async_import_billed_history()

    async def async_stop_stats_import(self) -> None:
        """Cancel an import still writing statistics when the entry goes away."""
        self._stats_stopped = True
        task = self._stats_task
        self._stats_task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_update_data(self) -> UsageReadings:
        started = time.monotonic()
        client = self.client
        try:
            usage: UsageReadings = await self.hass.async_add_executor_job(
                client.fetch_usage
            )
        except (AuthError, UsageError) as exc:
            self.last_error = str(exc)
            self.last_poll_ms = round((time.monotonic() - started) * 1000)
            _LOGGER.debug(
                "SP account poll for %s failed after %d ms: %s",
                self.title,
                self.last_poll_ms,
                exc,
            )
            raise self._update_error(exc) from exc
        self.last_error = None
        self.last_poll_ms = round((time.monotonic() - started) * 1000)
        self._now = client.clock.now()
        self.last_success = self._now
        if self.last_poll_ms > SLOW_POLL_MS:
            _LOGGER.warning(
                "SP account poll for %s took %d ms and still completed every read",
                self.title,
                self.last_poll_ms,
            )
        ir.async_delete_issue(self.hass, DOMAIN, auth_issue_id(self.subentry_id))
        self._persist_session_if_changed()
        self._schedule_stats_import(usage)
        return usage

    def _update_error(self, exc: AuthError | UsageError) -> UpdateFailed:
        """The coordinator failure a rejected read becomes.

        A verification requirement clears on its own; any other auth rejection
        is a verdict on the stored login, so a repair issue asks the user to
        sign in again from the subentry's reconfigure flow.
        """
        if isinstance(exc, AuthError):
            if exc.error_folded == OAUTH_ERROR_REQUIRES_VERIFICATION:
                return UpdateFailed(
                    str(exc), **translated_error("requires_verification", exc)
                )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                auth_issue_id(self.subentry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_AUTH_FAILED,
                translation_placeholders={"title": self.title},
            )
            return UpdateFailed(str(exc), **translated_error("auth_failed", exc))
        return UpdateFailed(str(exc), **translated_error("usage_failed", exc))

    def _persist_session_if_changed(self) -> None:
        session = self.client.session
        subentry = self.subentry
        if session is None or subentry is None:
            return
        data = subentry.data
        if same_tokens(session_from_data(data), session):
            return
        payload = {
            **data,
            **session_entry_data(session, data.get(CONF_USERNAME, "")),
        }
        if not session.refresh_token:
            payload.pop(CONF_REFRESH_TOKEN, None)
        self.hass.config_entries.async_update_subentry(
            self.entry, subentry, data=payload
        )

    # ------------------------------------------------------------------
    # Long-term statistics (Energy dashboard)
    # ------------------------------------------------------------------

    def _schedule_stats_import(self, usage: UsageReadings) -> None:
        # The poll's own readings are passed in: HA starts tasks eagerly, so
        # the import can run before the coordinator has stored them as data.
        if self._stats_stopped:
            return
        task = self._stats_task
        if task is not None and not task.done():
            return
        self._stats_task = self.hass.async_create_task(
            self.async_import_billed_history(usage)
        )

    async def async_import_billed_history(
        self, usage: UsageReadings | None = None
    ) -> None:
        """Write billed period totals into recorder long-term statistics."""
        if "recorder" not in self.hass.config.components:
            return
        async with self._stats_lock:
            usage = usage or self.data
            if usage is None:
                return
            try:
                await self._async_import_billed_history(usage)
            except Exception:  # noqa: BLE001 - background import must not crash
                _LOGGER.exception(
                    "failed to import SP account statistics for %s", self.title
                )

    def _imported_through(self, premise_id: str) -> dict[str, datetime]:
        if self._imported_premise != premise_id:
            self._imported_premise = premise_id
            self._imported_points = {}
        return self._imported_points

    def _add_external_statistics(
        self,
        premise_id: str,
        statistic_id: str,
        metadata: dict[str, object],
        points: Sequence[_PointT],
        value: Callable[[_PointT], float],
    ) -> None:
        from homeassistant.components.recorder.statistics import (
            async_add_external_statistics,
        )

        imported = self._imported_through(premise_id)
        fresh = unimported(points, imported.get(statistic_id))
        if not fresh:
            return
        stats = [
            {"start": point.start, "state": value(point), "sum": value(point)}
            for point in fresh
        ]
        async_add_external_statistics(self.hass, metadata, stats)
        imported[statistic_id] = fresh[-1].start

    async def _async_import_billed_history(self, usage: UsageReadings) -> None:
        from homeassistant.components.recorder.models.statistics import (
            StatisticMeanType,
            StatisticMetaData,
        )

        # HA 2025.10+ wants unit_class (and reports its absence); older
        # releases reject the unknown key outright.
        meta_keys = (
            StatisticMetaData.__required_keys__ | StatisticMetaData.__optional_keys__
        )

        def metadata(
            key: str, name: str, unit: str, unit_class: str | None
        ) -> dict[str, object]:
            meta: dict[str, object] = {
                "has_sum": True,
                "mean_type": StatisticMeanType.NONE,
                "name": name,
                "source": DOMAIN,
                "statistic_id": external_statistic_id(usage.premise_id, key),
                "unit_of_measurement": unit,
            }
            if "unit_class" in meta_keys:
                meta["unit_class"] = unit_class
            return meta

        price = self.electricity_price
        if usage.electricity is not None:
            periods = self._spec_cache.view(usage, self.now).graph
            points = cumulative_points(
                periods if periods else usage.electricity.periods
            )
            if points:
                self._add_external_statistics(
                    usage.premise_id,
                    external_statistic_id(usage.premise_id, SENSOR_KEY_ELECTRICITY),
                    metadata(
                        SENSOR_KEY_ELECTRICITY,
                        STATISTIC_NAME_ELECTRICITY,
                        UNIT_KWH,
                        "energy",
                    ),
                    points,
                    _cumulative,
                )
                cost_id = external_statistic_id(
                    usage.premise_id, STATISTIC_KEY_ELECTRICITY_COST
                )
                if price != self._imported_price:
                    # A new price must reach the points already written, so the
                    # watermark is dropped and the whole cost series re-sent.
                    self._imported_through(usage.premise_id).pop(cost_id, None)
                if price is not None:
                    self._add_external_statistics(
                        usage.premise_id,
                        cost_id,
                        metadata(
                            STATISTIC_KEY_ELECTRICITY_COST,
                            STATISTIC_NAME_ELECTRICITY_COST,
                            UNIT_SGD,
                            None,
                        ),
                        cost_points(points, price),
                        _cumulative,
                    )
        if usage.gas is not None:
            points = cumulative_points(usage.gas.periods)
            if points:
                self._add_external_statistics(
                    usage.premise_id,
                    external_statistic_id(usage.premise_id, SENSOR_KEY_GAS),
                    metadata(
                        SENSOR_KEY_GAS,
                        STATISTIC_NAME_GAS,
                        usage.gas.unit,
                        "energy" if usage.gas.unit == UNIT_KWH else "volume",
                    ),
                    points,
                    _cumulative,
                )
        # Not imported upstream: water is billed monthly, but a monthly series
        # still gives the Energy dashboard's water tracking ~13 months of
        # history (month/year views). Only m³, which the recorder can convert.
        if usage.water is not None and usage.water.unit == UNIT_M3:
            points = cumulative_points(usage.water.periods)
            if points:
                self._add_external_statistics(
                    usage.premise_id,
                    external_statistic_id(usage.premise_id, SENSOR_KEY_WATER),
                    metadata(SENSOR_KEY_WATER, STATISTIC_NAME_WATER, UNIT_M3, "volume"),
                    points,
                    _cumulative,
                )
        bill_points = monthly_bill_points(usage.bills)
        if bill_points:
            self._add_external_statistics(
                usage.premise_id,
                external_statistic_id(usage.premise_id, SENSOR_KEY_LAST_BILL),
                metadata(SENSOR_KEY_LAST_BILL, STATISTIC_NAME_BILL, UNIT_SGD, None),
                bill_points,
                _amount,
            )
        self._imported_price = price
