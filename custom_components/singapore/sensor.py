"""Sensor platform for Singapore SP Group tariffs."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import UNIT_ELECTRICITY, UNIT_GAS, UNIT_WATER, SPGroupCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SP Group tariff sensors."""
    coordinator: SPGroupCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SingaporeElectricityTariffSensor(coordinator, entry.entry_id),
            SingaporeSolarExportPriceSensor(coordinator, entry.entry_id),
            SingaporeGasTariffSensor(coordinator, entry.entry_id),
            SingaporeWaterTariffSensor(coordinator, entry.entry_id),
        ]
    )


class _BaseTariffSensor(CoordinatorEntity[SPGroupCoordinator], SensorEntity):
    """Shared base for SP Group tariff sensors.

    Tariff sensors report price-per-unit values (e.g. ¢/kWh, SGD/m³).
    These are not standard HA energy/gas/water units, so device_class is
    explicitly None to prevent HA's unit-validation warning at line 729 of
    homeassistant/components/sensor/__init__.py.
    """

    _attr_has_entity_name = False
    _attr_device_class = None  # custom price-rate units; no HA device class applies
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    def _common_attrs(self) -> dict:
        if self.coordinator.data is None:
            return {}
        return {
            "quarter": self.coordinator.data.quarter,
            "year": self.coordinator.data.year,
            "source": "SP Group",
        }


class SingaporeElectricityTariffSensor(_BaseTariffSensor):
    """Total residential electricity tariff (¢/kWh)."""

    _attr_name = "Singapore Electricity Tariff"
    _attr_icon = "mdi:lightning-bolt"
    _attr_native_unit_of_measurement = UNIT_ELECTRICITY

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_electricity_tariff"

    @property
    def native_value(self) -> float | None:
        return (
            self.coordinator.data.electricity_price if self.coordinator.data else None
        )

    @property
    def extra_state_attributes(self) -> dict:
        return self._common_attrs()


class SingaporeSolarExportPriceSensor(_BaseTariffSensor):
    """Solar export price = electricity tariff minus network costs (¢/kWh)."""

    _attr_name = "Singapore Solar Export Price"
    _attr_icon = "mdi:solar-power"
    _attr_native_unit_of_measurement = UNIT_ELECTRICITY

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_solar_export_price"

    @property
    def native_value(self) -> float | None:
        return (
            self.coordinator.data.solar_export_price if self.coordinator.data else None
        )

    @property
    def extra_state_attributes(self) -> dict:
        attrs = self._common_attrs()
        if self.coordinator.data:
            attrs["network_cost"] = self.coordinator.data.network_cost
            attrs["total_tariff"] = self.coordinator.data.electricity_price
        return attrs


class SingaporeGasTariffSensor(_BaseTariffSensor):
    """Piped natural gas tariff (¢/kWh)."""

    _attr_name = "Singapore Gas Tariff"
    _attr_icon = "mdi:gas-burner"
    _attr_native_unit_of_measurement = UNIT_GAS

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_gas_tariff"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.gas_price if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict:
        return self._common_attrs()


class SingaporeWaterTariffSensor(_BaseTariffSensor):
    """Water tariff (SGD/m³)."""

    _attr_name = "Singapore Water Tariff"
    _attr_icon = "mdi:water"
    _attr_native_unit_of_measurement = UNIT_WATER

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_water_tariff"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.water_price if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict:
        return self._common_attrs()
