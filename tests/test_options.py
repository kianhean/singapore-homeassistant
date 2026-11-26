"""Options helpers, optional sources, the health monitor and event entities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import issue_registry as ir

from custom_components.singapore import async_setup_entry, health
from custom_components.singapore.coe_coordinator import CoeData
from custom_components.singapore.config_flow import (
    SingaporeOptionsFlow,
    options_from_input,
)
from custom_components.singapore.const import (
    CONF_API_KEY,
    CONF_CARPARKS,
    CONF_DISABLED_SOURCES,
    CONF_ENABLED_SOURCES,
    CONF_POLLING,
    OPT_IN_SOURCES,
    SOURCES,
    carpark_numbers,
    data_gov_headers,
    enabled_sources,
    scaled_interval,
)
from custom_components.singapore.event import (
    SingaporeCoeResultsEvent,
    SingaporeTrainServiceAlertEvent,
    coe_event_attributes,
    train_transitions,
)
from custom_components.singapore.health import (
    FAILING_THRESHOLD,
    SourceHealthMonitor,
    issue_owner,
)
from custom_components.singapore.holiday_coordinator import PublicHolidayCoordinator
from custom_components.singapore.sensor import (
    async_setup_entry as sensor_setup_entry,
)
from custom_components.singapore.train_coordinator import (
    TrainStatusCoordinator,
    TrainStatusData,
)
from custom_components.singapore.weather_coordinator import (
    SingaporeWeatherCoordinator,
)


def _entry(options: dict | None = None) -> ConfigEntry:
    entry = ConfigEntry()
    entry.options = options or {}
    return entry


# -- const helpers -----------------------------------------------------------


def test_every_source_but_the_opt_in_ones_enabled_by_default():
    default = set(SOURCES) - set(OPT_IN_SOURCES)
    assert {
        "carpark",
        "taxi",
        "capitaland",
        "train_crowd",
        "ev_chargers",
        "mall_carpark",
        "prayer",
        "pump_prices",
    } == set(OPT_IN_SOURCES)
    assert enabled_sources(_entry()) == default
    assert enabled_sources(None) == default


def test_disabled_sources_are_excluded():
    entry = _entry({CONF_DISABLED_SOURCES: ["psi", "radar", "gone_in_a_later_release"]})
    assert enabled_sources(entry) == set(SOURCES) - {"psi", "radar", *OPT_IN_SOURCES}


def test_opt_in_sources_are_on_once_ticked():
    entry = _entry({CONF_ENABLED_SOURCES: ["taxi"], CONF_DISABLED_SOURCES: ["uv"]})
    assert enabled_sources(entry) == set(SOURCES) - {
        "uv",
        "carpark",
        "capitaland",
        "train_crowd",
        "ev_chargers",
        "mall_carpark",
        "prayer",
        "pump_prices",
    }


def test_data_gov_headers():
    assert data_gov_headers(_entry()) == {}
    assert data_gov_headers(_entry({CONF_API_KEY: "  "})) == {}
    assert data_gov_headers(_entry({CONF_API_KEY: "abc"})) == {"x-api-key": "abc"}


def test_scaled_interval():
    reduced = _entry({CONF_POLLING: "reduced"})
    assert scaled_interval(reduced, timedelta(minutes=2)) == timedelta(minutes=6)
    # Slow sources keep their interval; None (COE, timer-driven) stays None.
    assert scaled_interval(reduced, timedelta(hours=24)) == timedelta(hours=24)
    assert scaled_interval(reduced, None) is None
    assert scaled_interval(_entry(), timedelta(minutes=2)) == timedelta(minutes=2)


def test_coordinators_use_entry_options():
    entry = _entry({CONF_POLLING: "reduced"})
    coordinator = TrainStatusCoordinator(MagicMock(), entry)
    assert coordinator.update_interval == timedelta(minutes=15)


# -- options flow ------------------------------------------------------------


def test_options_from_input_stores_disabled_sources():
    enabled = [s for s in SOURCES if s != "lightning"]
    assert options_from_input(
        {"sources": enabled, "api_key": " k ", "polling": "reduced"}
    ) == {
        CONF_DISABLED_SOURCES: ["lightning"],
        # Opt-in sources are stored as the ones ticked.
        CONF_ENABLED_SOURCES: [
            "carpark",
            "taxi",
            "capitaland",
            "train_crowd",
            "ev_chargers",
            "mall_carpark",
            "prayer",
            "pump_prices",
        ],
        CONF_POLLING: "reduced",
        CONF_API_KEY: "k",
    }
    # Unticked opt-in sources are simply not stored (off by default).
    stored = options_from_input(
        {"sources": [s for s in SOURCES if s not in OPT_IN_SOURCES]}
    )
    assert stored[CONF_DISABLED_SOURCES] == []
    assert CONF_ENABLED_SOURCES not in stored


def test_options_from_input_stores_carparks():
    stored = options_from_input(
        {"sources": ["carpark"], "carparks": " bj55;TM24  bj55 ,, "}
    )
    assert stored[CONF_CARPARKS] == "BJ55, TM24"
    assert carpark_numbers(stored) == ["BJ55", "TM24"]
    assert CONF_CARPARKS not in options_from_input({"sources": ["carpark"]})
    assert carpark_numbers({}) == []
    # An empty key is dropped (clears a stored one).
    assert CONF_API_KEY not in options_from_input(
        {"sources": list(SOURCES), "api_key": "", "polling": "normal"}
    )


@pytest.mark.asyncio
async def test_options_flow_requires_a_source():
    flow = SingaporeOptionsFlow()
    flow.config_entry = _entry()
    result = await flow.async_step_init({"sources": [], "polling": "normal"})
    assert result["type"] == "form"
    assert result["errors"] == {"sources": "no_sources"}

    result = await flow.async_step_init({"sources": ["uv"], "polling": "normal"})
    assert result["type"] == "create_entry"
    assert set(result["data"][CONF_DISABLED_SOURCES]) == (
        set(SOURCES) - {"uv", *OPT_IN_SOURCES}
    )


# -- setup with sources turned off -----------------------------------------


@pytest.mark.asyncio
async def test_disabled_sources_get_no_coordinator_or_entities():
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    disabled = [s for s in SOURCES if s not in {"uv", "flood"}]
    entry = _entry({CONF_DISABLED_SOURCES: disabled})
    entry.async_create_background_task = MagicMock(
        side_effect=lambda _hass, coro, _name: coro.close()
    )
    weather = AsyncMock()
    with patch.object(
        SingaporeWeatherCoordinator, "async_config_entry_first_refresh", weather
    ):
        assert await async_setup_entry(hass, entry) is True

    weather.assert_not_called()
    data = entry.runtime_data
    assert set(data.coordinators()) == {"uv", "flood"}
    assert data.weather is None and data.tariff is None
    names = [c.args[2] for c in entry.async_create_background_task.call_args_list]
    assert sorted(names) == [
        "singapore_flood_alert_initial_refresh",
        "singapore_uv_index_initial_refresh",
    ]

    added: list = []
    await sensor_setup_entry(hass, entry, added.extend)
    keys = {type(e).__name__ for e in added}
    assert keys == {
        "SingaporeFloodAlertsSensor",
        "SingaporeUvIndexSensor",
        "SingaporeUvExposureSensor",
    }


@pytest.mark.asyncio
async def test_options_change_schedules_reload():
    from custom_components.singapore import _async_entry_updated

    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = _entry({CONF_DISABLED_SOURCES: list(SOURCES)})
    await async_setup_entry(hass, entry)
    hass.config_entries.async_schedule_reload.assert_not_called()

    entry.options = {CONF_DISABLED_SOURCES: ["psi"]}
    with patch.object(PublicHolidayCoordinator, "async_refresh", AsyncMock()):
        await _async_entry_updated(hass, entry)
    hass.config_entries.async_schedule_reload.assert_called_once_with(entry.entry_id)


# -- health monitor ---------------------------------------------------------


def test_health_monitor_opens_issue_after_threshold_and_clears_it():
    ir.async_create_issue.reset_mock()
    ir.async_delete_issue.reset_mock()
    coordinator = SimpleNamespace(
        last_update_success=False, last_exception=ValueError("markup changed")
    )
    monitor = SourceHealthMonitor(MagicMock(), {"tariff": coordinator})
    start = datetime(2026, 11, 15, tzinfo=UTC)

    monitor.async_check(start)
    monitor.async_check(start + FAILING_THRESHOLD - timedelta(minutes=1))
    ir.async_create_issue.assert_not_called()

    monitor.async_check(start + FAILING_THRESHOLD)
    ir.async_create_issue.assert_called_once()
    (
        _hass,
        _domain,
        issue,
    ) = ir.async_create_issue.call_args.args
    kwargs = ir.async_create_issue.call_args.kwargs
    assert issue == "source_failing_tariff"
    assert kwargs["translation_placeholders"]["source"] == "SP Group tariffs"
    # Without an entry id the issue isn't tied to one (whatever HA supports).
    assert "config_entry_id" not in kwargs
    assert "markup changed" in kwargs["translation_placeholders"]["error"]

    coordinator.last_update_success = True
    monitor.async_check(start + FAILING_THRESHOLD + timedelta(hours=1))
    ir.async_delete_issue.assert_called_with(
        monitor.hass, "singapore", "source_failing_tariff"
    )
    assert monitor.failing_since == {}


# -- event entities ---------------------------------------------------------


def test_train_transitions():
    before = {"A": "normal", "B": "disruption", "C": "normal", "D": "planned"}
    after = {"A": "disruption", "B": "normal", "C": "planned", "D": "normal"}
    assert train_transitions(before, after) == [
        ("disruption", "A"),
        ("resolved", "B"),
        ("planned", "C"),
    ]
    # Unknown previous state (first poll) and unchanged lines fire nothing.
    assert train_transitions({}, after) == []
    assert train_transitions(after, after) == []


def _coe(bidding_no: int, premium_a: int = 95501) -> CoeData:
    return CoeData(
        premiums={"A": premium_a, "B": 1}, month="2026-03", bidding_no=bidding_no
    )


@pytest.mark.asyncio
async def test_coe_event_fires_once_per_new_exercise():
    coordinator = SimpleNamespace(data=_coe(1), last_update_success=True)
    entity = SingaporeCoeResultsEvent(coordinator, "e1")
    entity.async_write_ha_state = MagicMock()
    await entity.async_added_to_hass()
    assert getattr(entity, "fired", []) == []  # baseline only

    entity._handle_coordinator_update()  # same exercise again
    assert getattr(entity, "fired", []) == []

    coordinator.data = _coe(2, 100000)
    entity._handle_coordinator_update()
    assert entity.fired == [("new_results", coe_event_attributes(coordinator.data))]
    assert entity.fired[0][1]["category_a"] == 100000
    assert entity.fired[0][1]["exercise"] == "2026-03 #2"
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_coe_event_fires_for_results_missed_while_down():
    """The restored last exercise is older than the fetched one: fire."""
    coordinator = SimpleNamespace(data=None, last_update_success=True)
    entity = SingaporeCoeResultsEvent(coordinator, "e1")
    entity.async_write_ha_state = MagicMock()
    entity._last_event_type = "new_results"
    entity._last_event_attributes = {"exercise": "2026-03 #1"}
    await entity.async_added_to_hass()

    coordinator.data = _coe(2)
    entity._handle_coordinator_update()
    assert [event for event, _ in entity.fired] == ["new_results"]


@pytest.mark.asyncio
async def test_train_event_fires_per_line_change():
    statuses = {"Circle Line": "normal", "Downtown Line": "normal"}
    data = TrainStatusData(status="normal", details="", line_statuses=statuses)
    coordinator = SimpleNamespace(data=data, last_update_success=True)
    entity = SingaporeTrainServiceAlertEvent(coordinator, "e1")
    entity.async_write_ha_state = MagicMock()
    await entity.async_added_to_hass()

    coordinator.data = TrainStatusData(
        status="disruption",
        details="CCL delayed",
        line_statuses={"Circle Line": "disruption", "Downtown Line": "normal"},
    )
    entity._handle_coordinator_update()
    assert entity.fired == [
        (
            "disruption",
            {"line": "Circle Line", "status": "disruption", "details": "CCL delayed"},
        )
    ]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        # HA 2025.4 - 2026.10: neither argument exists.
        (frozenset({"hass", "domain", "issue_id"}), ({}, {})),
        # HA 2026.11: config entries only.
        (
            frozenset({"issue_id", "config_entry_id"}),
            ({"config_entry_id": "e1"}, {"config_entry_id": "e1"}),
        ),
        # HA 2026.12: subentries too.
        (
            frozenset({"issue_id", "config_entry_id", "config_subentry_id"}),
            (
                {"config_entry_id": "e1"},
                {"config_entry_id": "e1", "config_subentry_id": "s1"},
            ),
        ),
    ],
)
def test_issue_owner_follows_the_running_ha(monkeypatch, params, expected):
    monkeypatch.setattr(health, "_create_issue_params", lambda: params)
    assert issue_owner("e1") == expected[0]
    assert issue_owner("e1", "s1") == expected[1]


def test_health_monitor_ties_issue_to_entry(monkeypatch):
    monkeypatch.setattr(
        health, "_create_issue_params", lambda: frozenset({"config_entry_id"})
    )
    ir.async_create_issue.reset_mock()
    coordinator = SimpleNamespace(
        last_update_success=False, last_exception=ValueError("markup changed")
    )
    monitor = SourceHealthMonitor(MagicMock(), {"tariff": coordinator}, "entry1")
    start = datetime(2026, 11, 15, tzinfo=UTC)
    monitor.async_check(start)
    monitor.async_check(start + FAILING_THRESHOLD)
    assert ir.async_create_issue.call_args.kwargs["config_entry_id"] == "entry1"
