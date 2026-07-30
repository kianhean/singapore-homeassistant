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


def _patch_all_coordinators():
    """Make every coordinator's first refresh succeed."""
    from datetime import datetime, timezone

    from custom_components.singapore.sp_usage_client import UsageData
    from custom_components.singapore.usage_coordinator import SPUsageCoordinator

    ok = AsyncMock(return_value=MagicMock())
    usage_ok = AsyncMock(
        return_value=UsageData(
            account_no="8949049293",
            last_updated=datetime(2026, 4, 12, tzinfo=timezone.utc),
            electricity_today_kwh=19.967,
        )
    )
    return [
        patch.object(SPGroupCoordinator, "_async_update_data", ok),
        patch.object(SingaporeWeatherCoordinator, "_async_update_data", ok),
        patch.object(PublicHolidayCoordinator, "_async_update_data", ok),
        patch.object(TrainStatusCoordinator, "_async_update_data", ok),
        patch.object(CoeCoordinator, "_async_update_data", ok),
        patch.object(SPUsageCoordinator, "_async_update_data", usage_ok),
    ]


@pytest.mark.asyncio
async def test_setup_without_sp_credentials_skips_usage_coordinator():
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = ConfigEntry()

    patches = _patch_all_coordinators()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert await async_setup_entry(hass, entry) is True

    assert entry.runtime_data.usage is None


@pytest.mark.asyncio
async def test_setup_with_sp_credentials_creates_usage_coordinator():
    import asyncio

    from custom_components.singapore.usage_coordinator import (
        CONF_SP_ACCOUNT_NO,
        CONF_SP_REFRESH_TOKEN,
        SPUsageCoordinator,
    )

    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = ConfigEntry(
        data={CONF_SP_REFRESH_TOKEN: "rt", CONF_SP_ACCOUNT_NO: "8949049293"}
    )

    patches = _patch_all_coordinators()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert await async_setup_entry(hass, entry) is True
        # Let the backgrounded first refresh run.
        await asyncio.sleep(0)

    assert isinstance(entry.runtime_data.usage, SPUsageCoordinator)
    assert entry.runtime_data.usage.account_no == "8949049293"
