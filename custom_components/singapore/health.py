"""Repair issues for data sources that keep failing.

Transient network errors don't count here: by convention the coordinators
serve their last-known data through those. A coordinator only reports a failed
update when a source answered with something it could not use (the site's
markup or the API's payload changed) or when nothing has loaded yet. If that
lasts a day, the user gets a repair card naming the source, instead of
entities that just sit unavailable.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import datetime, timedelta
from functools import cache
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN, ISSUE_TRACKER_URL, SOURCE_NAMES, SOURCES

# How long a source must keep failing before the repair issue appears. A day
# covers the daily sources (tariff, holidays, COE) failing twice in a row.
FAILING_THRESHOLD = timedelta(hours=24)
CHECK_INTERVAL = timedelta(hours=1)
_MAX_ERROR_CHARS = 200


def issue_id(source: str) -> str:
    """The repair issue id for a failing data source."""
    return f"source_failing_{source}"


@cache
def _create_issue_params() -> frozenset[str]:
    """The keyword arguments the running HA's async_create_issue takes."""
    try:
        return frozenset(inspect.signature(ir.async_create_issue).parameters)
    except (TypeError, ValueError):
        return frozenset()


def issue_owner(entry_id: str, subentry_id: str | None = None) -> dict[str, Any]:
    """Tie a repair issue to its config entry (and subentry) where HA can.

    HA 2026.11 added ``config_entry_id`` and 2026.12 ``config_subentry_id``:
    an issue created with them is deleted along with the entry or subentry.
    Older releases (back to the 2025.4 minimum) don't take them, so each is
    only passed when the running HA's ``async_create_issue`` accepts it.
    """
    params = _create_issue_params()
    owner: dict[str, Any] = {}
    if "config_entry_id" in params:
        owner["config_entry_id"] = entry_id
        if subentry_id is not None and "config_subentry_id" in params:
            owner["config_subentry_id"] = subentry_id
    return owner


class SourceHealthMonitor:
    """Tracks how long each enabled source has been failing."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinators: Mapping[str, DataUpdateCoordinator],
        entry_id: str | None = None,
    ) -> None:
        self.hass = hass
        self.coordinators = coordinators
        self.entry_id = entry_id
        self.failing_since: dict[str, datetime] = {}

    @callback
    def async_check(self, now: datetime | None = None) -> None:
        """Open or clear each source's repair issue."""
        now = now or dt_util.utcnow()
        for source, coordinator in self.coordinators.items():
            if coordinator.last_update_success:
                # Also clears an issue raised before a reload (a no-op if none).
                self.failing_since.pop(source, None)
                ir.async_delete_issue(self.hass, DOMAIN, issue_id(source))
                continue
            since = self.failing_since.setdefault(source, now)
            if now - since < FAILING_THRESHOLD:
                continue
            error = repr(coordinator.last_exception or "unknown error")
            owner = issue_owner(self.entry_id) if self.entry_id else {}
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id(source),
                **owner,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                learn_more_url=ISSUE_TRACKER_URL,
                translation_key="source_failing",
                translation_placeholders={
                    "source": SOURCE_NAMES[source],
                    "error": error[:_MAX_ERROR_CHARS],
                    "issue_tracker": ISSUE_TRACKER_URL,
                },
            )


@callback
def async_setup_source_health(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinators: Mapping[str, DataUpdateCoordinator],
) -> SourceHealthMonitor:
    """Check the enabled sources hourly; clear the disabled sources' issues."""
    for source in SOURCES:
        if source not in coordinators:
            ir.async_delete_issue(hass, DOMAIN, issue_id(source))
    monitor = SourceHealthMonitor(hass, coordinators, entry.entry_id)
    entry.async_on_unload(
        async_track_time_interval(hass, monitor.async_check, CHECK_INTERVAL)
    )
    return monitor


@callback
def async_clear_source_issues(hass: HomeAssistant) -> None:
    """Delete every data source repair issue (the entry is being removed)."""
    for source in SOURCES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(source))
