"""Singapore integration: tariffs, COE, weather, PSI, trains, holidays, floods, etc."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .carpark_coordinator import CarparkCoordinator
from .coe_coordinator import CoeCoordinator
from .const import (
    DOMAIN,
    SCHOOL_EVENT_KINDS,
    SOURCE_CARPARK,
    SOURCE_COE,
    SOURCE_DENGUE,
    SOURCE_FLOOD,
    SOURCE_HAWKER,
    SOURCE_HOLIDAY,
    SOURCE_LIGHTNING,
    SOURCE_PSI,
    SOURCE_RADAR,
    SOURCE_SCHOOL,
    SOURCE_TARIFF,
    SOURCE_TAXI,
    SOURCE_TRAFFIC,
    SOURCE_TRAIN,
    SOURCE_UV,
    SOURCE_WBGT,
    SOURCE_WEATHER,
    SOURCES,
    carpark_numbers,
    enabled_school_events,
    enabled_sources,
)
from .coordinator import SPGroupCoordinator
from .dengue_coordinator import DengueClusterCoordinator
from .entity import (
    DEVICES,
    device_identifier,
    entity_source,
    school_translation_key,
)
from .flood_coordinator import FloodAlertCoordinator
from .hawker_coordinator import HawkerClosureCoordinator
from .health import async_clear_source_issues, async_setup_source_health
from .holiday_coordinator import PublicHolidayCoordinator
from .lightning_coordinator import LightningCoordinator
from .psi_coordinator import PsiCoordinator
from .radar_coordinator import RadarCoordinator
from .school_coordinator import SchoolCalendarCoordinator
from .sp_account_coordinator import (
    SUBENTRY_TYPE_SP_ACCOUNT,
    SpAccountCoordinator,
    auth_issue_id,
)
from .taxi_coordinator import TaxiAvailabilityCoordinator
from .traffic_coordinator import TrafficImagesCoordinator
from .train_coordinator import TrainStatusCoordinator
from .uv_coordinator import UvCoordinator
from .wbgt_coordinator import WbgtCoordinator
from .weather_coordinator import SingaporeWeatherCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["DOMAIN", "SingaporeConfigEntry", "SingaporeData"]

PLATFORMS = [
    Platform.SENSOR,
    Platform.WEATHER,
    Platform.CALENDAR,
    Platform.CAMERA,
    Platform.EVENT,
]

# COE results are published after each bidding exercise; refresh daily at 19:30.
_COE_REFRESH_HOUR = 19
_COE_REFRESH_MINUTE = 30


@dataclass
class SingaporeData:
    """Runtime data stored on the config entry.

    A coordinator is None when its data source is turned off in the options.
    """

    tariff: SPGroupCoordinator | None
    coe: CoeCoordinator | None
    weather: SingaporeWeatherCoordinator | None
    holiday: PublicHolidayCoordinator | None
    train: TrainStatusCoordinator | None
    psi: PsiCoordinator | None
    flood: FloodAlertCoordinator | None
    traffic: TrafficImagesCoordinator | None
    dengue: DengueClusterCoordinator | None
    lightning: LightningCoordinator | None
    uv: UvCoordinator | None
    radar: RadarCoordinator | None
    school: SchoolCalendarCoordinator | None
    wbgt: WbgtCoordinator | None
    hawker: HawkerClosureCoordinator | None
    carpark: CarparkCoordinator | None
    taxi: TaxiAvailabilityCoordinator | None
    # One per SP Group e-account subentry, keyed by subentry id.
    sp_accounts: dict[str, SpAccountCoordinator] = field(default_factory=dict)
    # The options this setup used; a change to them reloads the entry.
    options: dict[str, Any] = field(default_factory=dict)

    def coordinators(self) -> dict[str, DataUpdateCoordinator]:
        """The enabled data sources' coordinators, keyed by source."""
        return {
            source: coordinator
            for source in SOURCES
            if (coordinator := getattr(self, source)) is not None
        }


type SingaporeConfigEntry = ConfigEntry[SingaporeData]

# Coordinator class per data source (the key is also the SingaporeData field).
_COORDINATORS: dict[str, type[DataUpdateCoordinator]] = {
    SOURCE_TARIFF: SPGroupCoordinator,
    SOURCE_COE: CoeCoordinator,
    SOURCE_WEATHER: SingaporeWeatherCoordinator,
    SOURCE_HOLIDAY: PublicHolidayCoordinator,
    SOURCE_TRAIN: TrainStatusCoordinator,
    SOURCE_PSI: PsiCoordinator,
    SOURCE_FLOOD: FloodAlertCoordinator,
    SOURCE_TRAFFIC: TrafficImagesCoordinator,
    SOURCE_DENGUE: DengueClusterCoordinator,
    SOURCE_LIGHTNING: LightningCoordinator,
    SOURCE_UV: UvCoordinator,
    SOURCE_RADAR: RadarCoordinator,
    SOURCE_SCHOOL: SchoolCalendarCoordinator,
    SOURCE_WBGT: WbgtCoordinator,
    SOURCE_HAWKER: HawkerClosureCoordinator,
    SOURCE_CARPARK: CarparkCoordinator,
    SOURCE_TAXI: TaxiAvailabilityCoordinator,
}

# Weather, holidays and train status are the core sources: setup waits for
# their first refresh (and retries if it fails). Every other source loads in
# the background, so an outage there never blocks setup. SP Group tariffs are
# scraped from a third-party site that can be unreachable for long stretches
# (e.g. TLS handshake failures, issue #44), so they are in the background too.
_BLOCKING_SOURCES = (SOURCE_WEATHER, SOURCE_HOLIDAY, SOURCE_TRAIN)


async def async_setup_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Set up the integration and kick off the first data fetch."""
    enabled = enabled_sources(entry)
    coordinators: dict[str, DataUpdateCoordinator] = {
        source: cls(hass, entry)
        for source, cls in _COORDINATORS.items()
        if source in enabled
    }

    # Fetch the core sources concurrently to speed up setup.
    # return_exceptions=True lets every coordinator finish its first refresh
    # even if a sibling fails, instead of orphaning them mid-flight when
    # gather() propagates the first exception.
    results = await asyncio.gather(
        *(
            coordinators[source].async_config_entry_first_refresh()
            for source in _BLOCKING_SOURCES
            if source in coordinators
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result

    async def _initial_refresh(coordinator, label: str) -> None:
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            _LOGGER.warning(
                "Initial %s refresh failed; continuing setup and retrying later", label
            )

    for source, coordinator in coordinators.items():
        if source in _BLOCKING_SOURCES:
            continue
        label = _BACKGROUND_LABELS[source]
        entry.async_create_background_task(
            hass,
            _initial_refresh(coordinator, label),
            f"singapore_{label.lower().replace(' ', '_')}_initial_refresh",
        )

    if (coe_coordinator := coordinators.get(SOURCE_COE)) is not None:

        async def _refresh_coe(_now) -> None:
            await coe_coordinator.async_refresh()

        unsub_coe = async_track_time_change(
            hass,
            _refresh_coe,
            hour=_COE_REFRESH_HOUR,
            minute=_COE_REFRESH_MINUTE,
            second=0,
        )
        entry.async_on_unload(unsub_coe)

    # SP Group e-accounts (config subentries). Like the other optional sources,
    # their first poll runs in the background so an SP outage or an expired
    # login never blocks setup; sensors appear once the first poll succeeds.
    # With SP Group tariffs turned off there is no price, so the cost series is
    # skipped (usage is still imported).
    tariff_coordinator = coordinators.get(SOURCE_TARIFF)
    sp_accounts: dict[str, SpAccountCoordinator] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SP_ACCOUNT:
            continue
        sp_coordinator = SpAccountCoordinator(
            hass, entry, subentry, tariff=tariff_coordinator
        )
        sp_accounts[subentry.subentry_id] = sp_coordinator
        entry.async_create_background_task(
            hass,
            _initial_refresh(sp_coordinator, "SP account"),
            f"singapore_sp_account_{subentry.subentry_id}_initial_refresh",
        )

    if sp_accounts and tariff_coordinator is not None:

        @callback
        def _tariff_updated() -> None:
            # A new quarter's tariff re-prices each account's cost series.
            for account in sp_accounts.values():
                hass.async_create_task(account.async_tariff_updated())

        entry.async_on_unload(tariff_coordinator.async_add_listener(_tariff_updated))

    entry.runtime_data = SingaporeData(
        **{source: coordinators.get(source) for source in SOURCES},
        sp_accounts=sp_accounts,
        options=dict(entry.options),
    )
    async_setup_source_health(hass, entry, coordinators)
    async_remove_stale(hass, entry, enabled)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def async_remove_stale(
    hass: HomeAssistant, entry: SingaporeConfigEntry, enabled: set[str]
) -> None:
    """Remove the entities and devices of data sources turned off in the options.

    Without this they would linger as "no longer provided by the integration"
    until deleted by hand. A device shared by several sources (Weather, NEA)
    stays while any of them is on; only the turned-off source's entities go.
    SP account entities belong to subentries, which Home Assistant cleans up
    itself when a subentry is deleted.
    """
    # MOE school calendars unticked in the options go too, source on or not.
    school_off = {
        school_translation_key(kind)
        for kind in SCHOOL_EVENT_KINDS
        if kind not in enabled_school_events(entry)
    }
    # Carparks dropped from the options' list go too. (In "nearest home" mode
    # the set is only known after the first poll; those stay until deleted.)
    chosen_carparks = {
        f"{entry.entry_id}_carpark_{number.lower()}"
        for number in carpark_numbers(entry)
    }
    entity_registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if getattr(reg_entry, "config_subentry_id", None) is not None:
            continue
        source = entity_source(reg_entry.domain, reg_entry.translation_key)
        if source is None:
            continue
        if (
            source not in enabled
            or reg_entry.translation_key in school_off
            or (
                reg_entry.translation_key == "carpark"
                and chosen_carparks
                and reg_entry.unique_id not in chosen_carparks
            )
        ):
            _LOGGER.debug("Removing %s: its source is turned off", reg_entry.entity_id)
            entity_registry.async_remove(reg_entry.entity_id)

    # Looked up through the entry's devices: async_get_device(identifiers=...)
    # is deprecated in 2026.10 and its replacement does not exist in 2025.4.
    stale_devices = {
        device_identifier(entry.entry_id, key)
        for key, device in DEVICES.items()
        if not any(source in enabled for source in device.sources)
    }
    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        if device_entry.identifiers & stale_devices:
            device_registry.async_update_device(
                device_entry.id, remove_config_entry_id=entry.entry_id
            )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SingaporeConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Let the user delete a device this integration no longer provides.

    The service devices of enabled sources and the SP accounts' premises are
    refused (they would come straight back); anything else may go.
    """
    enabled = enabled_sources(entry)
    provided = {
        device_identifier(entry.entry_id, key)
        for key, device in DEVICES.items()
        if any(source in enabled for source in device.sources)
    }
    for account in entry.runtime_data.sp_accounts.values():
        if (usage := account.data) is not None:
            provided.add((DOMAIN, f"{entry.entry_id}_sp_{usage.premise_id}"))
    return not (device_entry.identifiers & provided)


_BACKGROUND_LABELS = {
    SOURCE_TARIFF: "SP Group tariff",
    SOURCE_COE: "COE",
    SOURCE_PSI: "PSI",
    SOURCE_FLOOD: "flood alert",
    SOURCE_TRAFFIC: "traffic camera",
    SOURCE_DENGUE: "dengue cluster",
    SOURCE_LIGHTNING: "lightning",
    SOURCE_UV: "UV index",
    SOURCE_RADAR: "rain radar",
    SOURCE_SCHOOL: "MOE school calendar",
    SOURCE_WBGT: "heat stress",
    SOURCE_HAWKER: "hawker closure",
    SOURCE_CARPARK: "carpark availability",
    SOURCE_TAXI: "taxi availability",
}


async def _async_entry_updated(
    hass: HomeAssistant, entry: SingaporeConfigEntry
) -> None:
    """Reload when the options or the set of SP accounts changed.

    The SP coordinators persist refreshed tokens on their subentry, which also
    fires this listener, so a reload on every change would restart the whole
    integration twice an hour. Only a changed set of accounts needs one.
    """
    if dict(entry.options) != entry.runtime_data.options:
        # A data source was turned on/off, or the API key or polling changed.
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    accounts = entry.runtime_data.sp_accounts
    current = {
        subentry_id
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_SP_ACCOUNT
    }
    if current != set(accounts):
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    for coordinator in accounts.values():
        await coordinator.async_subentry_updated()


async def async_unload_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Unload a config entry."""
    for coordinator in entry.runtime_data.sp_accounts.values():
        await coordinator.async_stop_stats_import()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> None:
    """Clear this integration's repair issues when it is removed."""
    async_clear_source_issues(hass)
    for subentry_id in entry.subentries:
        ir.async_delete_issue(hass, DOMAIN, auth_issue_id(subentry_id))
