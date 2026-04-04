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
    """Set up electricity tariff sensors."""
    coordinator: SPGroupCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SingaporeElectricityTariffSensor(coordinator, entry.entry_id),
        SingaporeSolarExportPriceSensor(coordinator, entry.entry_id),
    ])


class _BaseTariffSensor(CoordinatorEntity[SPGroupCoordinator], SensorEntity):
    """Shared base for SP Group tariff sensors."""

    _attr_has_entity_name = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    def _quarter_year_attrs(self) -> dict:
        if self.coordinator.data is None:
            return {}
        return {
            "quarter": self.coordinator.data.quarter,
            "year": self.coordinator.data.year,
            "source": "SP Group",
        }


class SingaporeElectricityTariffSensor(_BaseTariffSensor):
    """Total residential electricity tariff from SP Group."""

    _attr_name = "Singapore Electricity Tariff"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_electricity_tariff"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.price

    @property
    def extra_state_attributes(self) -> dict:
        return self._quarter_year_attrs()


class SingaporeSolarExportPriceSensor(_BaseTariffSensor):
    """Solar export price = total tariff minus network costs."""

    _attr_name = "Singapore Solar Export Price"
    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_solar_export_price"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.solar_export_price

    @property
    def extra_state_attributes(self) -> dict:
        attrs = self._quarter_year_attrs()
        if self.coordinator.data is not None:
            attrs["network_cost"] = self.coordinator.data.network_cost
            attrs["total_tariff"] = self.coordinator.data.price
        return attrs
