"""Options flow, optional data sources, API key, repairs and event entities."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.singapore.const import (
    CONF_API_KEY,
    CONF_DISABLED_SOURCES,
    CONF_ENABLED_SOURCES,
    CONF_POLLING,
    DOMAIN,
    SOURCES,
)
from custom_components.singapore.health import CHECK_INTERVAL, FAILING_THRESHOLD
from custom_components.singapore.psi_coordinator import PSI_URL
from custom_components.singapore.train_coordinator import TRAIN_STATUS_URL

_CORE_AND_COE = ["weather", "train", "holiday", "coe"]


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Singapore",
        data={"name": "Singapore"},
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_options_form_serializes_over_http(
    hass: HomeAssistant, hass_client, mock_sources
) -> None:
    """The options form loads through the HTTP API (schema must serialize)."""
    await async_setup_component(hass, "config", {})
    entry = await _setup(hass)
    client = await hass_client()
    resp = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    assert resp.status == HTTPStatus.OK, await resp.text()
    body = await resp.json()
    assert body["step_id"] == "init"
    assert [field["name"] for field in body["data_schema"]] == [
        "sources",
        "school_events",
        "api_key",
        "lta_api_key",
        "carparks",
        "train_crowd_stations",
        "ev_postal_codes",
        "polling",
    ]


async def test_turning_sources_off_reloads_without_their_entities(
    hass: HomeAssistant, mock_sources
) -> None:
    entry = await _setup(hass)
    assert hass.states.get("sensor.air_quality_psi") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    enabled = [
        s
        for s in SOURCES
        if s not in {"psi", "traffic", "radar", "train_crowd", "ev_chargers"}
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"sources": enabled, "api_key": " key-123 ", "polling": "reduced"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_DISABLED_SOURCES: ["psi", "traffic", "radar"],
        CONF_ENABLED_SOURCES: ["carpark", "taxi", "capitaland"],
        CONF_POLLING: "reduced",
        CONF_API_KEY: "key-123",
    }
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data
    assert data.psi is None and data.traffic is None and data.radar is None
    # Reduced polling: 2 min → 6 min; the daily tariff scrape is unchanged.
    assert data.flood.update_interval == timedelta(minutes=6)
    assert data.tariff.update_interval == timedelta(hours=24)
    # Entities and devices of the disabled sources are removed, not left
    # behind as "no longer provided".
    entity_registry = er.async_get(hass)
    for entity_id in (
        "sensor.air_quality_psi",
        "sensor.air_quality_pm2_5",
        "camera.weather_rain_radar_70_km",
        "camera.traffic_cameras_woodlands_causeway",
    ):
        assert entity_registry.async_get(entity_id) is None, entity_id
        assert hass.states.get(entity_id) is None, entity_id
    devices = _device_keys(hass, entry)
    assert "air_quality" not in devices
    assert "traffic_cameras" not in devices
    # Weather keeps its device (radar shared it) and its own entities.
    assert "weather" in devices
    assert entity_registry.async_get("sensor.weather_temperature") is not None
    assert hass.states.get("sensor.coe_category_a").state == "95501"

    # Turned back on, the source's device and entities come back.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "sources": [s for s in SOURCES if s not in {"train_crowd", "ev_chargers"}],
            "polling": "normal",
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entity_registry.async_get("sensor.air_quality_psi") is not None
    assert hass.states.get("sensor.air_quality_psi") is not None
    assert "air_quality" in _device_keys(hass, entry)


def _device_keys(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """The entry's devices, keyed by identifier minus the entry id prefix."""
    prefix = f"{entry.entry_id}_"
    return {
        identifier.removeprefix(prefix): device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for _domain, identifier in device.identifiers
    }


async def test_unticked_school_calendars_are_removed(
    hass: HomeAssistant, mock_sources
) -> None:
    entry = await _setup(hass)
    assert hass.states.get("calendar.school_terms") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "sources": [s for s in SOURCES if s not in {"train_crowd", "ev_chargers"}],
            "school_events": ["holidays", "exams"],
            "polling": "normal",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["disabled_school_events"] == ["terms"]
    await hass.async_block_till_done(wait_background_tasks=True)

    entity_registry = er.async_get(hass)
    assert entity_registry.async_get("calendar.school_terms") is None
    assert hass.states.get("calendar.school_terms") is None
    assert hass.states.get("calendar.school_holidays") is not None
    assert "school" in _device_keys(hass, entry)

    # None ticked while the source is on is refused on the form.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"sources": list(SOURCES), "school_events": [], "polling": "normal"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"school_events": "no_school_events"}


async def test_a_device_no_longer_provided_can_be_deleted(
    hass: HomeAssistant, hass_ws_client, mock_sources
) -> None:
    """The UI's "Delete" works on stale devices only."""
    assert await async_setup_component(hass, "config", {})
    entry = await _setup(hass)
    device_registry = dr.async_get(hass)
    stale = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "old_device")}
    )
    live = _device_keys(hass, entry)["coe"]
    client = await hass_ws_client(hass)
    for device, allowed in ((live, False), (stale, True)):
        await client.send_json_auto_id(
            {
                "type": "config/device_registry/remove_config_entry",
                "config_entry_id": entry.entry_id,
                "device_id": device.id,
            }
        )
        response = await client.receive_json()
        assert response["success"] is allowed, response
    assert device_registry.async_get(stale.id) is None
    assert device_registry.async_get(live.id) is not None


async def test_no_sources_is_rejected(hass: HomeAssistant, mock_sources) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sources": [], "polling": "normal"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"sources": "no_sources"}


async def test_api_key_is_sent_to_data_gov_only(
    hass: HomeAssistant, mock_sources
) -> None:
    await _setup(hass, {CONF_API_KEY: "secret-key"})
    sent = {
        str(url): headers for _method, url, _data, headers in mock_sources.mock_calls
    }
    assert sent[PSI_URL]["x-api-key"] == "secret-key"
    # mytransport.sg is not data.gov.sg: the key must not leak there.
    assert "x-api-key" not in (sent[TRAIN_STATUS_URL] or {})


async def test_core_sources_off_still_sets_up(
    hass: HomeAssistant, mock_sources
) -> None:
    """With weather, trains and holidays off, nothing blocks setup."""
    entry = await _setup(hass, {CONF_DISABLED_SOURCES: _CORE_AND_COE})
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.weather_temperature") is None
    assert hass.states.get("calendar.public_holidays") is None
    assert hass.states.get("event.mrt_lrt_service_alert") is None
    assert hass.states.get("event.coe_new_results") is None
    assert hass.states.get("sensor.air_quality_psi") is not None


async def test_failing_source_opens_and_clears_repair(
    hass: HomeAssistant, aioclient_mock, request, freezer
) -> None:
    # Registered before the canned sources, so it wins: a PSI payload with no
    # readings, which the parser rejects (what a changed API looks like).
    aioclient_mock.get(PSI_URL, json={"code": 0, "data": {"items": []}})
    request.getfixturevalue("mock_sources")

    entry = await _setup(hass)
    assert not entry.runtime_data.psi.last_update_success
    registry = ir.async_get(hass)

    freezer.tick(CHECK_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, "source_failing_psi") is None

    freezer.tick(FAILING_THRESHOLD + CHECK_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    issue = registry.async_get_issue(DOMAIN, "source_failing_psi")
    assert issue is not None
    assert issue.translation_placeholders["source"] == "Air quality (PSI)"

    # Turning the source off clears its issue.
    hass.config_entries.async_update_entry(
        entry, options={CONF_DISABLED_SOURCES: ["psi"]}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_issue(DOMAIN, "source_failing_psi") is None


async def test_event_entities(hass: HomeAssistant, mock_sources) -> None:
    entry = await _setup(hass)
    coe = hass.states.get("event.coe_new_results")
    assert coe is not None
    # The first exercise seen only sets the baseline.
    assert coe.state == "unknown"
    assert coe.attributes["event_types"] == ["new_results"]

    alert = hass.states.get("event.mrt_lrt_service_alert")
    assert alert is not None
    assert alert.attributes["event_types"] == ["disruption", "planned", "resolved"]

    # The next exercise fires.
    coordinator = entry.runtime_data.coe
    coordinator.async_set_updated_data(
        type(coordinator.data)(
            premiums={**coordinator.data.premiums, "A": 100000},
            month="2026-03",
            bidding_no=2,
        )
    )
    await hass.async_block_till_done()
    coe = hass.states.get("event.coe_new_results")
    assert coe.state != "unknown"
    assert coe.attributes["event_type"] == "new_results"
    assert coe.attributes["exercise"] == "2026-03 #2"
    assert coe.attributes["category_a"] == 100000

    # CCL recovers in the next poll → "resolved".
    train = entry.runtime_data.train
    statuses = {**train.data.line_statuses, "Circle Line": "normal"}
    train.async_set_updated_data(
        type(train.data)(status="normal", details="", line_statuses=statuses)
    )
    await hass.async_block_till_done()
    alert = hass.states.get("event.mrt_lrt_service_alert")
    assert alert.attributes["event_type"] == "resolved"
    assert alert.attributes["line"] == "Circle Line"


async def test_removing_the_entry_clears_repairs(
    hass: HomeAssistant, mock_sources
) -> None:
    entry = await _setup(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        "source_failing_psi",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="source_failing",
        translation_placeholders={"source": "PSI", "error": "x", "issue_tracker": "y"},
    )
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "source_failing_psi") is None
