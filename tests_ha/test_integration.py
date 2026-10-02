"""End-to-end setup tests against a real Home Assistant instance."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.singapore.const import DOMAIN


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Singapore", data={"name": "Singapore"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities_and_unloads(
    hass: HomeAssistant, mock_sources
) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get("sensor.energy_electricity_tariff")
    assert state is not None
    assert float(state.state) == 29.29
    assert state.attributes["attribution"] == "Data provided by SP Group"

    coe = hass.states.get("sensor.coe_category_a")
    assert coe is not None and coe.state == "95501"

    line = hass.states.get("sensor.mrt_lrt_circle_line_status")
    assert line is not None
    assert line.state == "disruption"
    assert line.attributes["options"] == ["normal", "planned", "disruption"]

    bearing = hass.states.get("sensor.weather_wind_bearing")
    assert bearing is not None
    assert bearing.attributes["state_class"] == "measurement_angle"

    weather = hass.states.get("weather.weather_bedok")
    assert weather is not None
    assert weather.state == "lightning-rainy"

    assert hass.states.get("calendar.public_holidays") is not None

    # Every entity must use a stable, entry-scoped unique ID.
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert entities
    assert all(e.unique_id.startswith(entry.entry_id) for e in entities)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_source_unavailable(
    hass: HomeAssistant, mock_sources
) -> None:
    from custom_components.singapore.coordinator import TARIFF_URL

    mock_sources.clear_requests()
    mock_sources._mocks = [m for m in mock_sources._mocks if str(m.url) != TARIFF_URL]
    mock_sources.get(TARIFF_URL, status=503)

    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Singapore"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_config_flow_creates_entry_and_is_single_instance(
    hass: HomeAssistant, mock_sources
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Singapore"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_diagnostics(hass: HomeAssistant, hass_client, mock_sources) -> None:
    entry = await _setup(hass)

    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diag["coordinators"]["tariff"]["data"]["electricity_price"] == 29.29
    assert diag["coordinators"]["train"]["data"]["status"] == "disruption"
