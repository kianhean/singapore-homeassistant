"""Tests for Singapore electricity tariff sensor platform."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.singapore import DOMAIN
from custom_components.singapore.coordinator import UNIT

_HTML_TABLE = """
<html><body>
<h2>Electricity Tariff – 1 January 2025 to 31 March 2025</h2>
<table>
  <thead><tr><th>Component</th><th>Rate (¢/kWh)</th></tr></thead>
  <tbody>
    <tr><td>Energy</td><td>14.32</td></tr>
    <tr><td>Network</td><td>7.61</td></tr>
    <tr><td>Total (incl. GST)</td><td>29.29</td></tr>
  </tbody>
</table>
</body></html>
"""

_TARIFF_ENTITY = "sensor.singapore_electricity_tariff"
_SOLAR_ENTITY = "sensor.singapore_solar_export_price"


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Singapore Electricity"},
        unique_id="singapore_electricity",
    )
    entry.add_to_hass(hass)

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value=_HTML_TABLE)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    with patch(
        "custom_components.singapore_hello.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


# ---------------------------------------------------------------------------
# Electricity tariff sensor
# ---------------------------------------------------------------------------


async def test_tariff_sensor_state(hass: HomeAssistant) -> None:
    await _setup_entry(hass)
    state = hass.states.get(_TARIFF_ENTITY)
    assert state is not None
    assert float(state.state) == 29.29


async def test_tariff_sensor_unit(hass: HomeAssistant) -> None:
    await _setup_entry(hass)
    state = hass.states.get(_TARIFF_ENTITY)
    assert state.attributes.get("unit_of_measurement") == UNIT


async def test_tariff_sensor_attributes(hass: HomeAssistant) -> None:
    await _setup_entry(hass)
    state = hass.states.get(_TARIFF_ENTITY)
    assert state.attributes["quarter"] == "Q1"
    assert state.attributes["year"] == 2025
    assert state.attributes["source"] == "SP Group"


# ---------------------------------------------------------------------------
# Solar export price sensor
# ---------------------------------------------------------------------------


async def test_solar_sensor_state(hass: HomeAssistant) -> None:
    await _setup_entry(hass)
    state = hass.states.get(_SOLAR_ENTITY)
    assert state is not None
    assert float(state.state) == round(29.29 - 7.61, 2)


async def test_solar_sensor_unit(hass: HomeAssistant) -> None:
    await _setup_entry(hass)
    state = hass.states.get(_SOLAR_ENTITY)
    assert state.attributes.get("unit_of_measurement") == UNIT


async def test_solar_sensor_attributes(hass: HomeAssistant) -> None:
    await _setup_entry(hass)
    state = hass.states.get(_SOLAR_ENTITY)
    assert state.attributes["network_cost"] == 7.61
    assert state.attributes["total_tariff"] == 29.29
    assert state.attributes["quarter"] == "Q1"
    assert state.attributes["year"] == 2025


# ---------------------------------------------------------------------------
# Error / unload
# ---------------------------------------------------------------------------


async def test_sensors_unavailable_when_no_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Singapore Electricity"},
        unique_id="singapore_electricity",
    )
    entry.add_to_hass(hass)

    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=Exception("Network error"))

    with patch(
        "custom_components.singapore_hello.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for entity_id in (_TARIFF_ENTITY, _SOLAR_ENTITY):
        state = hass.states.get(entity_id)
        assert state is None or state.state in ("unavailable", "unknown")


async def test_unload_entry(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
