"""Sensor platform for Singapore electricity tariff."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import UNIT, SPGroupCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the electricity tariff sensor."""
    coordinator: SPGroupCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SingaporeElectricityTariffSensor(coordinator, entry.entry_id)])


class SingaporeElectricityTariffSensor(CoordinatorEntity[SPGroupCoordinator], SensorEntity):
    """Sensor reporting the current Singapore residential electricity tariff."""

    _attr_has_entity_name = True
    _attr_name = "Electricity Tariff"
    _attr_icon = "mdi:lightning-bolt"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_electricity_tariff"

    @property
    def native_value(self) -> float | None:
        """Return the tariff in cents/kWh."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.price

    @property
    def extra_state_attributes(self) -> dict:
        """Return quarter and year as attributes."""
        if self.coordinator.data is None:
            return {}
        return {
            "quarter": self.coordinator.data.quarter,
            "year": self.coordinator.data.year,
            "source": "SP Group",
        }
