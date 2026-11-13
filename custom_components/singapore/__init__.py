"""Singapore integration: tariffs, COE, weather, PSI, trains, holidays, floods, etc."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .coe_coordinator import CoeCoordinator
from .const import DOMAIN
from .coordinator import SPGroupCoordinator
from .dengue_coordinator import DengueClusterCoordinator
from .flood_coordinator import FloodAlertCoordinator
from .holiday_coordinator import PublicHolidayCoordinator
from .lightning_coordinator import LightningCoordinator
from .psi_coordinator import PsiCoordinator
from .sp_account_coordinator import SUBENTRY_TYPE_SP_ACCOUNT, SpAccountCoordinator
from .traffic_coordinator import TrafficImagesCoordinator
from .train_coordinator import TrainStatusCoordinator
from .uv_coordinator import UvCoordinator
from .weather_coordinator import SingaporeWeatherCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["DOMAIN", "SingaporeConfigEntry", "SingaporeData"]

PLATFORMS = [Platform.SENSOR, Platform.WEATHER, Platform.CALENDAR, Platform.CAMERA]

# COE results are published after each bidding exercise; refresh daily at 19:30.
_COE_REFRESH_HOUR = 19
_COE_REFRESH_MINUTE = 30


@dataclass
class SingaporeData:
    """Runtime data stored on the config entry."""

    tariff: SPGroupCoordinator
    coe: CoeCoordinator
    weather: SingaporeWeatherCoordinator
    holiday: PublicHolidayCoordinator
    train: TrainStatusCoordinator
    psi: PsiCoordinator
    flood: FloodAlertCoordinator
    traffic: TrafficImagesCoordinator
    dengue: DengueClusterCoordinator
    lightning: LightningCoordinator
    uv: UvCoordinator
    # One per SP Group e-account subentry, keyed by subentry id.
    sp_accounts: dict[str, SpAccountCoordinator] = field(default_factory=dict)


type SingaporeConfigEntry = ConfigEntry[SingaporeData]


async def async_setup_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Set up the integration and kick off the first data fetch."""
    tariff_coordinator = SPGroupCoordinator(hass, entry)
    weather_coordinator = SingaporeWeatherCoordinator(hass, entry)
    holiday_coordinator = PublicHolidayCoordinator(hass, entry)
    train_coordinator = TrainStatusCoordinator(hass, entry)
    coe_coordinator = CoeCoordinator(hass, entry)
    psi_coordinator = PsiCoordinator(hass, entry)
    flood_coordinator = FloodAlertCoordinator(hass, entry)
    traffic_coordinator = TrafficImagesCoordinator(hass, entry)
    dengue_coordinator = DengueClusterCoordinator(hass, entry)
    lightning_coordinator = LightningCoordinator(hass, entry)
    uv_coordinator = UvCoordinator(hass, entry)

    # Fetch independent data sources concurrently to speed up setup.
    # return_exceptions=True lets every coordinator finish its first refresh
    # even if a sibling fails, instead of orphaning them mid-flight when
    # gather() propagates the first exception.
    results = await asyncio.gather(
        weather_coordinator.async_config_entry_first_refresh(),
        holiday_coordinator.async_config_entry_first_refresh(),
        train_coordinator.async_config_entry_first_refresh(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result

    async def _initial_refresh_coe() -> None:
        await coe_coordinator.async_refresh()
        if not coe_coordinator.last_update_success:
            _LOGGER.warning(
                "Initial COE refresh failed; continuing setup and retrying later"
            )

    # Don't block setup on COE API availability/rate limits.
    entry.async_create_background_task(
        hass, _initial_refresh_coe(), "singapore_coe_initial_refresh"
    )

    async def _initial_refresh_psi() -> None:
        await psi_coordinator.async_refresh()
        if not psi_coordinator.last_update_success:
            _LOGGER.warning(
                "Initial PSI refresh failed; continuing setup and retrying later"
            )

    # PSI is supplementary; don't fail the whole integration if it's unavailable.
    entry.async_create_background_task(
        hass, _initial_refresh_psi(), "singapore_psi_initial_refresh"
    )

    async def _initial_refresh(coordinator, label: str) -> None:
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            _LOGGER.warning(
                "Initial %s refresh failed; continuing setup and retrying later", label
            )

    # SP Group tariffs are scraped from a third-party site that can be unreachable
    # for long stretches (e.g. TLS handshake failures, issue #44); flood alerts,
    # traffic cameras, dengue clusters, lightning and UV are supplementary. None of
    # them may block setup: their entities show unavailable until data arrives.
    for coordinator, label in (
        (tariff_coordinator, "SP Group tariff"),
        (flood_coordinator, "flood alert"),
        (traffic_coordinator, "traffic camera"),
        (dengue_coordinator, "dengue cluster"),
        (lightning_coordinator, "lightning"),
        (uv_coordinator, "UV index"),
    ):
        entry.async_create_background_task(
            hass,
            _initial_refresh(coordinator, label),
            f"singapore_{label.lower().replace(' ', '_')}_initial_refresh",
        )

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
    sp_accounts: dict[str, SpAccountCoordinator] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SP_ACCOUNT:
            continue
        sp_coordinator = SpAccountCoordinator(hass, entry, subentry)
        sp_accounts[subentry.subentry_id] = sp_coordinator
        entry.async_create_background_task(
            hass,
            _initial_refresh(sp_coordinator, "SP account"),
            f"singapore_sp_account_{subentry.subentry_id}_initial_refresh",
        )

    entry.runtime_data = SingaporeData(
        tariff=tariff_coordinator,
        coe=coe_coordinator,
        weather=weather_coordinator,
        holiday=holiday_coordinator,
        train=train_coordinator,
        psi=psi_coordinator,
        flood=flood_coordinator,
        traffic=traffic_coordinator,
        dengue=dengue_coordinator,
        lightning=lightning_coordinator,
        uv=uv_coordinator,
        sp_accounts=sp_accounts,
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: SingaporeConfigEntry
) -> None:
    """Reload when an SP account was added or removed; else update in place.

    The SP coordinators persist refreshed tokens on their subentry, which also
    fires this listener, so a reload on every change would restart the whole
    integration twice an hour. Only a changed set of accounts needs one.
    """
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
