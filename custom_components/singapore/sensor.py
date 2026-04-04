"""Sensor platform for Singapore SP Group tariffs and COE results."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coe_coordinator import (
    _CATEGORY_DESCRIPTIONS,
    _CATEGORY_NAMES,
    COE_CATEGORIES,
    UNIT_COE,
    CoeCoordinator,
)
from .coordinator import UNIT_ELECTRICITY, UNIT_GAS, UNIT_WATER, SPGroupCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SP Group tariff and COE sensors."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    tariff_coordinator: SPGroupCoordinator = entry_data["tariff"]
    coe_coordinator: CoeCoordinator = entry_data["coe"]

    entities: list[SensorEntity] = [
        SingaporeElectricityTariffSensor(tariff_coordinator, entry.entry_id),
        SingaporeSolarExportPriceSensor(tariff_coordinator, entry.entry_id),
        SingaporeGasTariffSensor(tariff_coordinator, entry.entry_id),
        SingaporeWaterTariffSensor(tariff_coordinator, entry.entry_id),
    ]
    for cat in COE_CATEGORIES:
        entities.append(SingaporeCoeResultSensor(coe_coordinator, entry.entry_id, cat))

    async_add_entities(entities)


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


class SingaporeCoeResultSensor(CoordinatorEntity[CoeCoordinator], SensorEntity):
    """COE bidding result (premium in SGD) for a single vehicle category."""

    _attr_has_entity_name = False
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_COE
    _attr_icon = "mdi:car"

    def __init__(
        self, coordinator: CoeCoordinator, entry_id: str, category: str
    ) -> None:
        super().__init__(coordinator)
        self._category = category
        self._attr_unique_id = f"{entry_id}_coe_cat_{category.lower()}"
        self._attr_name = _CATEGORY_NAMES[category]

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.premiums.get(self._category)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        return {
            "category": f"Category {self._category}",
            "description": _CATEGORY_DESCRIPTIONS[self._category],
            "month": self.coordinator.data.month,
            "bidding_no": self.coordinator.data.bidding_no,
            "source": "data.gov.sg / LTA",
        }
