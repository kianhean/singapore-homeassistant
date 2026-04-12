"""Tests for SpServicesCoordinator."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.sp_services_coordinator import (
    CONF_SP_TOKEN,
    SpServicesCoordinator,
    _account_slug,
    _build_statistics,
    _parse_period,
)


def _make_entry(token: str | None = "tok123") -> MagicMock:
    entry = MagicMock()
    entry.data = {CONF_SP_TOKEN: token} if token else {}
    entry.entry_id = "test_entry_id"
    return entry


def _make_usage_data(
    elec_today=5.0,
    elec_month=120.0,
    water_today=0.3,
    water_month=8.5,
    elec_hourly=None,
    elec_daily=None,
    water_monthly=None,
):
    from sp_services import UsageData

    return UsageData(
        electricity_today_kwh=elec_today,
        electricity_month_kwh=elec_month,
        water_today_m3=water_today,
        water_month_m3=water_month,
        account_no="9999999",
        last_updated=datetime(2026, 4, 12, 10, 0),
        electricity_last_month_kwh=310.0,
        water_last_month_m3=15.2,
        electricity_hourly_history=elec_hourly,
        electricity_daily_history=elec_daily,
        water_monthly_history=water_monthly,
    )


def _make_points(*entries):
    """Create UsagePoint list from (period, value) tuples."""
    from sp_services import UsagePoint

    return [UsagePoint(period=p, value=v) for p, v in entries]


# ---------------------------------------------------------------------------
# _parse_period
# ---------------------------------------------------------------------------


def test_parse_period_hourly():
    dt = _parse_period("2026-04-11 14:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 4
    assert dt.hour == 14


def test_parse_period_daily():
    dt = _parse_period("2026-04-11")
    assert dt is not None
    assert dt.day == 11
    assert dt.hour == 0


def test_parse_period_monthly():
    dt = _parse_period("2026-04")
    assert dt is not None
    assert dt.month == 4
    assert dt.day == 1


def test_parse_period_unknown_returns_none():
    assert _parse_period("not a date") is None


# ---------------------------------------------------------------------------
# _build_statistics
# ---------------------------------------------------------------------------


def test_build_statistics_cumulative_sum():
    points = _make_points(
        ("2026-04-11", 3.0),
        ("2026-04-12", 5.0),
        ("2026-04-13", 2.0),
    )
    stats = _build_statistics(points)
    assert len(stats) == 3
    sums = [s["sum"] for s in stats]
    assert sums == [3.0, 8.0, 10.0]


def test_build_statistics_sorted_by_period():
    points = _make_points(
        ("2026-04-13", 2.0),
        ("2026-04-11", 3.0),
        ("2026-04-12", 5.0),
    )
    stats = _build_statistics(points)
    states = [s["state"] for s in stats]
    assert states == [3.0, 5.0, 2.0]  # sorted by date


def test_build_statistics_skips_unparseable():
    points = _make_points(("2026-04-11", 3.0), ("bad-date", 9.0), ("2026-04-12", 5.0))
    stats = _build_statistics(points)
    assert len(stats) == 2


# ---------------------------------------------------------------------------
# _account_slug
# ---------------------------------------------------------------------------


def test_account_slug_sanitises_account_no():
    usage = _make_usage_data()
    slug = _account_slug(usage, "fallback")
    assert slug == "9999999"


def test_account_slug_falls_back_to_entry_id():
    from sp_services import UsageData

    usage = UsageData(
        electricity_today_kwh=None,
        electricity_month_kwh=None,
        water_today_m3=None,
        water_month_m3=None,
        account_no=None,
        last_updated=datetime.now(),
    )
    slug = _account_slug(usage, "Entry-ID-123")
    assert slug == "entry_id_123"


# ---------------------------------------------------------------------------
# SpServicesCoordinator._async_update_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_usage_returns_data():
    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    usage = _make_usage_data()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.fetch_usage = AsyncMock(return_value=usage)

    with (
        patch(
            "custom_components.singapore.sp_services_coordinator.SpServicesClient",
            return_value=mock_client,
        ),
        patch.object(coordinator, "_push_statistics"),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.electricity_today_kwh == 5.0


@pytest.mark.asyncio
async def test_missing_token_raises_update_failed():
    hass = MagicMock()
    entry = _make_entry(token=None)
    coordinator = SpServicesCoordinator(hass, entry)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success


@pytest.mark.asyncio
async def test_session_expired_raises_auth_failed():
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from sp_services import SessionExpiredError

    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.fetch_usage = AsyncMock(side_effect=SessionExpiredError("expired"))

    with patch(
        "custom_components.singapore.sp_services_coordinator.SpServicesClient",
        return_value=mock_client,
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_api_error_raises_update_failed():
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from sp_services import ApiError

    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.fetch_usage = AsyncMock(side_effect=ApiError("bad response"))

    with patch(
        "custom_components.singapore.sp_services_coordinator.SpServicesClient",
        return_value=mock_client,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# _push_statistics
# ---------------------------------------------------------------------------


def test_push_statistics_calls_recorder_for_hourly_electricity():
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )

    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    hourly = _make_points(("2026-04-11 01:00", 0.5), ("2026-04-11 02:00", 0.8))
    usage = _make_usage_data(elec_hourly=hourly)

    coordinator._push_statistics(usage)

    assert async_add_external_statistics.called
    # electricity push is first call
    call_args = async_add_external_statistics.call_args_list[0]
    meta = call_args[0][1]
    assert "sp_electricity" in meta["statistic_id"]
    assert meta["unit_of_measurement"] == "kWh"


def test_push_statistics_falls_back_to_daily():
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )

    async_add_external_statistics.reset_mock()
    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    daily = _make_points(("2026-04-11", 8.2), ("2026-04-12", 9.1))
    usage = _make_usage_data(elec_hourly=None, elec_daily=daily)

    coordinator._push_statistics(usage)

    assert async_add_external_statistics.called


def test_push_statistics_pushes_water_monthly():
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )

    async_add_external_statistics.reset_mock()
    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    monthly = _make_points(("2026-02", 12.5), ("2026-03", 14.1))
    usage = _make_usage_data(water_monthly=monthly)

    coordinator._push_statistics(usage)

    calls = async_add_external_statistics.call_args_list
    water_call = next(
        (c for c in calls if "sp_water" in c[0][1].get("statistic_id", "")), None
    )
    assert water_call is not None
    assert water_call[0][1]["unit_of_measurement"] == "m³"


def test_push_statistics_skips_empty_history():
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )

    async_add_external_statistics.reset_mock()
    hass = MagicMock()
    entry = _make_entry()
    coordinator = SpServicesCoordinator(hass, entry)

    usage = _make_usage_data(elec_hourly=None, elec_daily=None, water_monthly=None)
    coordinator._push_statistics(usage)

    assert not async_add_external_statistics.called
