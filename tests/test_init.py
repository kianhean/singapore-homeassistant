"""Tests for Singapore Hello World integration setup."""
import pytest
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.singapore_hello import DOMAIN


async def test_setup_entry(hass: HomeAssistant) -> None:
    """Test that a config entry sets up successfully."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Test"},
        unique_id="Test",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert DOMAIN in hass.data
    assert entry.entry_id in hass.data[DOMAIN]


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Test that a config entry unloads successfully."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Test"},
        unique_id="Test",
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
