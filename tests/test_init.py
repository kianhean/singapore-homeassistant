"""Tests for integration constants, domain, and setup wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.singapore import DOMAIN, PLATFORMS, async_setup_entry
from custom_components.singapore.coe_coordinator import CoeCoordinator
from custom_components.singapore.coordinator import SPGroupCoordinator
from custom_components.singapore.holiday_coordinator import PublicHolidayCoordinator
from custom_components.singapore.psi_coordinator import PsiCoordinator
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
    assert "geo_location" in PLATFORMS


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

    calls = {"holiday": False, "train": False}

    async def _weather_fails(self):
        raise Exception("data.gov.sg down")

    async def _holiday_ok(self):
        calls["holiday"] = True
        return MagicMock()

    async def _train_ok(self):
        calls["train"] = True
        return MagicMock()

    with (
        patch.object(
            SPGroupCoordinator, "_async_update_data", AsyncMock(return_value=None)
        ),
        patch.object(SingaporeWeatherCoordinator, "_async_update_data", _weather_fails),
        patch.object(PublicHolidayCoordinator, "_async_update_data", _holiday_ok),
        patch.object(TrainStatusCoordinator, "_async_update_data", _train_ok),
        patch.object(
            CoeCoordinator, "_async_update_data", AsyncMock(return_value=MagicMock())
        ),
        patch.object(
            PsiCoordinator, "_async_update_data", AsyncMock(return_value=MagicMock())
        ),
    ):
        with pytest.raises(Exception, match="first refresh failed"):
            await async_setup_entry(hass, entry)

    # The healthy coordinators must have completed their first refresh
    # despite the weather coordinator failing.
    assert calls == {"holiday": True, "train": True}
    hass.config_entries.async_forward_entry_setups.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_tariff_failure_does_not_block_setup():
    """An unreachable SP Group site must not keep the integration from loading.

    Regression test for issue #44: a TLS handshake failure against
    www.spgroup.com.sg raised ConfigEntryNotReady and took down every other
    entity. The tariff first refresh now runs in the background instead.
    """
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = ConfigEntry()
    entry.async_create_background_task = MagicMock(
        side_effect=lambda _hass, coro, _name: coro.close()
    )

    tariff_refresh = AsyncMock(side_effect=Exception("SSL handshake failed"))
    with (
        patch.object(
            SPGroupCoordinator, "async_config_entry_first_refresh", tariff_refresh
        ),
        patch.object(
            SingaporeWeatherCoordinator,
            "_async_update_data",
            AsyncMock(return_value=MagicMock()),
        ),
        patch.object(
            PublicHolidayCoordinator,
            "_async_update_data",
            AsyncMock(return_value=MagicMock()),
        ),
        patch.object(
            TrainStatusCoordinator,
            "_async_update_data",
            AsyncMock(return_value=MagicMock()),
        ),
    ):
        assert await async_setup_entry(hass, entry) is True

    tariff_refresh.assert_not_called()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    names = [c.args[2] for c in entry.async_create_background_task.call_args_list]
    assert "singapore_sp_group_tariff_initial_refresh" in names


async def test_setup_runs_background_refreshes_coe_timer_and_sp_accounts(caplog):
    """Background first refreshes, the 19:30 COE timer, SP accounts + repricing."""
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.helpers import event

    from custom_components import singapore
    from custom_components.singapore.sp_account_coordinator import (
        SUBENTRY_TYPE_SP_ACCOUNT,
        SpAccountCoordinator,
    )

    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    tasks = []
    hass.async_create_task = lambda coro, *a, **k: tasks.append(coro)
    entry = ConfigEntry()
    entry.subentries = {
        "sub1": ConfigSubentry(
            data={"username": "me@example.com"},
            subentry_type=SUBENTRY_TYPE_SP_ACCOUNT,
            title="me",
            subentry_id="sub1",
        ),
        "other": ConfigSubentry(
            data={}, subentry_type="something_else", title="x", subentry_id="other"
        ),
    }
    background = []
    entry.async_create_background_task = lambda _hass, coro, name: background.append(
        (name, coro)
    )
    event.async_track_time_change.reset_mock()
    ok = AsyncMock(return_value=MagicMock())
    with (
        patch.object(SingaporeWeatherCoordinator, "_async_update_data", ok),
        patch.object(PublicHolidayCoordinator, "_async_update_data", ok),
        patch.object(TrainStatusCoordinator, "_async_update_data", ok),
        patch.object(
            CoeCoordinator, "_async_update_data", AsyncMock(side_effect=OSError("x"))
        ),
        patch.object(SpAccountCoordinator, "async_tariff_updated", AsyncMock()),
        patch.object(singapore, "async_remove_stale"),
    ):
        assert await async_setup_entry(hass, entry) is True
        assert list(entry.runtime_data.sp_accounts) == ["sub1"]

        # A failed background refresh logs and carries on.
        names = dict(background)
        await names["singapore_coe_initial_refresh"]
        assert "Initial COE refresh failed" in caplog.text
        for name, coro in background:
            if name != "singapore_coe_initial_refresh":
                coro.close()

        # The COE timer refreshes the COE coordinator.
        refresh_coe = event.async_track_time_change.call_args.args[1]
        await refresh_coe(None)
        kwargs = event.async_track_time_change.call_args.kwargs
        assert (kwargs["hour"], kwargs["minute"]) == (19, 30)

        # A tariff update re-prices every SP account.
        entry.runtime_data.tariff.async_update_listeners()
        assert len(tasks) == 1
        await tasks[0]
        SpAccountCoordinator.async_tariff_updated.assert_awaited_once()


async def test_unload_stops_sp_imports_and_remove_clears_issues():
    from homeassistant.helpers import issue_registry as ir

    from custom_components.singapore import (
        SingaporeData,
        async_remove_entry,
        async_unload_entry,
    )

    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    account = MagicMock()
    account.async_stop_stats_import = AsyncMock()
    entry = ConfigEntry()
    entry.runtime_data = SingaporeData(
        **dict.fromkeys(
            (
                "tariff",
                "coe",
                "weather",
                "holiday",
                "train",
                "psi",
                "flood",
                "traffic",
                "dengue",
                "lightning",
                "uv",
                "radar",
                "school",
                "wbgt",
                "warnings",
                "tides",
                "hawker",
                "traffic_incidents",
                "fx",
                "interest",
                "carpark",
                "taxi",
                "capitaland",
                "train_crowd",
                "ev_chargers",
                "mall_carpark",
                "prayer",
            )
        ),
        sp_accounts={"sub1": account},
    )
    assert await async_unload_entry(hass, entry) is True
    account.async_stop_stats_import.assert_awaited_once()

    entry.subentries = {"sub1": MagicMock()}
    ir.async_delete_issue.reset_mock()
    await async_remove_entry(hass, entry)
    deleted = [c.args[2] for c in ir.async_delete_issue.call_args_list]
    assert "sp_account_auth_failed_sub1" in deleted
