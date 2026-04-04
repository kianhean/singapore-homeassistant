"""Tests for the Singapore Hello World sensor platform."""
import pytest
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.singapore_hello import DOMAIN


async def _setup_entry(hass: HomeAssistant, name: str = "Test") -> MockConfigEntry:
    """Create and load a config entry, returning it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: name},
        unique_id=name,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensor_state(hass: HomeAssistant) -> None:
    """Test that the sensor reports the correct state."""
    await _setup_entry(hass, "Singapore")

    state = hass.states.get("sensor.singapore_hello_world")
    assert state is not None
    assert state.state == "Hello from Singapore!"


async def test_sensor_attributes(hass: HomeAssistant) -> None:
    """Test that the sensor exposes the expected attributes."""
    await _setup_entry(hass, "Singapore")

    state = hass.states.get("sensor.singapore_hello_world")
    assert state.attributes["domain"] == DOMAIN
    assert state.attributes["integration"] == "Singapore"


async def test_sensor_icon(hass: HomeAssistant) -> None:
    """Test the sensor icon."""
    await _setup_entry(hass, "Singapore")

    state = hass.states.get("sensor.singapore_hello_world")
    assert state.attributes.get("icon") == "mdi:hand-wave"


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Test that the entry unloads cleanly."""
    entry = await _setup_entry(hass, "Singapore")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
