"""Tests for integration constants, domain, and setup wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.singapore import DOMAIN, PLATFORMS, async_setup_entry
from custom_components.singapore.coe_coordinator import CoeCoordinator
from custom_components.singapore.coordinator import SPGroupCoordinator
from custom_components.singapore.holiday_coordinator import PublicHolidayCoordinator
from custom_components.singapore.train_coordinator import TrainStatusCoordinator
from custom_components.singapore.weather_coordinator import (
    SingaporeWeatherCoordinator,
)


def test_domain():
    assert DOMAIN == "singapore"


def test_platforms_include_weather():
    assert "weather" in PLATFORMS


def test_platforms_include_calendar():
    assert "calendar" in PLATFORMS


@pytest.mark.asyncio
async def test_async_setup_entry_raises_and_does_not_orphan_siblings():
    """One coordinator failing must not cancel the others mid-flight.

    Regression test: asyncio.gather() without return_exceptions=True used
    to propagate the first failure immediately, leaving the remaining
    coordinators' first-refresh coroutines running in the background after
    setup had already aborted. return_exceptions=True lets every
    coordinator finish before the (first) exception is re-raised.
    """
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = ConfigEntry()

    calls = {"weather": False, "holiday": False, "train": False}

    async def _tariff_fails(self):
        raise Exception("SP Group site down")

    async def _weather_ok(self):
        calls["weather"] = True
        return MagicMock()

    async def _holiday_ok(self):
        calls["holiday"] = True
        return MagicMock()

    async def _train_ok(self):
        calls["train"] = True
        return MagicMock()

    with (
        patch.object(SPGroupCoordinator, "_async_update_data", _tariff_fails),
        patch.object(SingaporeWeatherCoordinator, "_async_update_data", _weather_ok),
        patch.object(PublicHolidayCoordinator, "_async_update_data", _holiday_ok),
        patch.object(TrainStatusCoordinator, "_async_update_data", _train_ok),
        patch.object(
            CoeCoordinator, "_async_update_data", AsyncMock(return_value=MagicMock())
        ),
    ):
        with pytest.raises(Exception, match="first refresh failed"):
            await async_setup_entry(hass, entry)

    # The three healthy coordinators must have completed their first
    # refresh despite the tariff coordinator failing.
    assert calls == {"weather": True, "holiday": True, "train": True}
    hass.config_entries.async_forward_entry_setups.assert_not_called()
