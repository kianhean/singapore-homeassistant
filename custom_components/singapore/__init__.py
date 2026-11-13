"""Singapore integration: tariffs, COE, weather, PSI, trains, holidays, floods, etc."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

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
from .traffic_coordinator import TrafficImagesCoordinator
from .train_coordinator import TrainStatusCoordinator
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
    # traffic cameras, dengue clusters and lightning are supplementary. None of
    # them may block setup: their entities show unavailable until data arrives.
    for coordinator, label in (
        (tariff_coordinator, "SP Group tariff"),
        (flood_coordinator, "flood alert"),
        (traffic_coordinator, "traffic camera"),
        (dengue_coordinator, "dengue cluster"),
        (lightning_coordinator, "lightning"),
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
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SingaporeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
