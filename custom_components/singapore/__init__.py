"""Singapore integration: tariffs, COE, weather, PSI, trains, holidays, SP usage."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .coe_coordinator import CoeCoordinator
from .const import DOMAIN, SUBENTRY_SP_ACCOUNT
from .coordinator import SPGroupCoordinator
from .holiday_coordinator import PublicHolidayCoordinator
from .psi_coordinator import PsiCoordinator
from .sp_services_coordinator import SpServicesCoordinator
from .train_coordinator import TrainStatusCoordinator
from .weather_coordinator import SingaporeWeatherCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["DOMAIN", "SingaporeConfigEntry", "SingaporeData"]

PLATFORMS = [Platform.SENSOR, Platform.WEATHER, Platform.CALENDAR]

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
    # SP Services account coordinators, keyed by config subentry id.
    sp_accounts: dict[str, SpServicesCoordinator] = field(default_factory=dict)


type SingaporeConfigEntry = ConfigEntry[SingaporeData]


async def async_setup_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Set up the integration and kick off the first data fetch."""
    tariff_coordinator = SPGroupCoordinator(hass, entry)
    weather_coordinator = SingaporeWeatherCoordinator(hass, entry)
    holiday_coordinator = PublicHolidayCoordinator(hass, entry)
    train_coordinator = TrainStatusCoordinator(hass, entry)
    coe_coordinator = CoeCoordinator(hass, entry)
    psi_coordinator = PsiCoordinator(hass, entry)

    # Fetch independent data sources concurrently to speed up setup.
    # return_exceptions=True lets every coordinator finish its first refresh
    # even if a sibling fails, instead of orphaning them mid-flight when
    # gather() propagates the first exception.
    results = await asyncio.gather(
        tariff_coordinator.async_config_entry_first_refresh(),
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

    sp_accounts: dict[str, SpServicesCoordinator] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_SP_ACCOUNT:
            continue
        sp_coordinator = SpServicesCoordinator(hass, entry, subentry)
        sp_accounts[subentry.subentry_id] = sp_coordinator
        entry.async_on_unload(sp_coordinator.async_close)
        # SP's private API can be slow or down; never block setup on it.
        entry.async_create_background_task(
            hass,
            sp_coordinator.async_refresh(),
            f"singapore_sp_initial_refresh_{subentry.subentry_id}",
        )

    entry.runtime_data = SingaporeData(
        tariff=tariff_coordinator,
        coe=coe_coordinator,
        weather=weather_coordinator,
        holiday=holiday_coordinator,
        train=train_coordinator,
        psi=psi_coordinator,
        sp_accounts=sp_accounts,
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _sp_subentry_ids(entry: SingaporeConfigEntry) -> set[str]:
    return {
        subentry.subentry_id
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_SP_ACCOUNT
    }


async def _async_entry_updated(
    hass: HomeAssistant, entry: SingaporeConfigEntry
) -> None:
    """React to SP account subentries being added, removed or reconfigured.

    The coordinators write the renewed session back to their subentry about
    once an hour, which also lands here; that must not reload the entry.
    """
    sp_accounts = entry.runtime_data.sp_accounts
    if _sp_subentry_ids(entry) != set(sp_accounts):
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    for subentry_id, coordinator in sp_accounts.items():
        await coordinator.async_subentry_updated(entry.subentries[subentry_id])
