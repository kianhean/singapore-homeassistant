"""Sensor platform for Singapore Hello World integration."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    name = entry.data.get(CONF_NAME, "Singapore Hello World")
    async_add_entities([SingaporeHelloSensor(entry.entry_id, name)])


class SingaporeHelloSensor(SensorEntity):
    """A hello world sensor entity."""

    _attr_has_entity_name = True
    _attr_name = "Hello World"

    def __init__(self, entry_id: str, integration_name: str) -> None:
        """Initialize the sensor."""
        self._entry_id = entry_id
        self._integration_name = integration_name
        self._attr_unique_id = f"{entry_id}_hello_world"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return "Hello from Singapore!"

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "integration": self._integration_name,
            "domain": DOMAIN,
        }

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:hand-wave"
