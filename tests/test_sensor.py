"""Tests for Singapore electricity tariff sensor platform."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.singapore_hello import DOMAIN
from custom_components.singapore_hello.coordinator import TariffData, UNIT

_MOCK_TARIFF = TariffData(price=29.29, quarter="Q1", year=2025)

_HTML_TABLE = """
<html><body>
<h2>Electricity Tariff – 1 January 2025 to 31 March 2025</h2>
<table>
  <thead><tr><th>Component</th><th>Rate (¢/kWh)</th></tr></thead>
  <tbody>
    <tr><td>Energy</td><td>14.32</td></tr>
    <tr><td>Total (incl. GST)</td><td>29.29</td></tr>
  </tbody>
</table>
</body></html>
"""


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a config entry with mocked HTTP fetching."""
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


async def test_sensor_state(hass: HomeAssistant) -> None:
    """Sensor reports the correct tariff value."""
    await _setup_entry(hass)

    state = hass.states.get("sensor.singapore_electricity_tariff_electricity_tariff")
    assert state is not None
    assert float(state.state) == 29.29


async def test_sensor_unit(hass: HomeAssistant) -> None:
    """Sensor uses cents/kWh as the unit."""
    await _setup_entry(hass)

    state = hass.states.get("sensor.singapore_electricity_tariff_electricity_tariff")
    assert state.attributes.get("unit_of_measurement") == UNIT


async def test_sensor_attributes(hass: HomeAssistant) -> None:
    """Sensor exposes quarter, year, and source attributes."""
    await _setup_entry(hass)

    state = hass.states.get("sensor.singapore_electricity_tariff_electricity_tariff")
    assert state.attributes["quarter"] == "Q1"
    assert state.attributes["year"] == 2025
    assert state.attributes["source"] == "SP Group"


async def test_sensor_unavailable_when_no_data(hass: HomeAssistant) -> None:
    """Sensor is unavailable when coordinator has no data."""
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

    state = hass.states.get("sensor.singapore_electricity_tariff_electricity_tariff")
    # Entry setup fails when first refresh fails, so state won't be loaded
    assert state is None or state.state in ("unavailable", "unknown")


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Config entry unloads cleanly."""
    entry = await _setup_entry(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
