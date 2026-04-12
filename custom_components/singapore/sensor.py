"""Sensor platform for Singapore SP Group tariffs and COE results."""

from __future__ import annotations

import re

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
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
from .sp_services_coordinator import SpServicesCoordinator
from .train_coordinator import TRAIN_LINES, TrainStatusCoordinator
from .weather_coordinator import SingaporeWeatherCoordinator

UNIT_TEMP = "°C"
UNIT_HUMIDITY = "%"
UNIT_WIND_SPEED = "km/h"
UNIT_WIND_BEARING = "°"
UNIT_RAINFALL = "mm"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SP Group tariff, COE, weather, train, and SP Services sensors."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    tariff_coordinator: SPGroupCoordinator = entry_data["tariff"]
    coe_coordinator: CoeCoordinator = entry_data["coe"]
    weather_coordinator: SingaporeWeatherCoordinator = entry_data["weather"]
    train_coordinator: TrainStatusCoordinator = entry_data["train"]
    sp_services_coordinator: SpServicesCoordinator | None = entry_data.get(
        "sp_services"
    )

    entities: list[SensorEntity] = [
        SingaporeElectricityTariffSensor(tariff_coordinator, entry.entry_id),
        SingaporeSolarExportPriceSensor(tariff_coordinator, entry.entry_id),
        SingaporeGasTariffSensor(tariff_coordinator, entry.entry_id),
        SingaporeWaterTariffSensor(tariff_coordinator, entry.entry_id),
    ]
    for cat in COE_CATEGORIES:
        entities.append(SingaporeCoeResultSensor(coe_coordinator, entry.entry_id, cat))

    entities.extend(
        [
            SingaporeTemperatureSensor(weather_coordinator, entry.entry_id),
            SingaporeHumiditySensor(weather_coordinator, entry.entry_id),
            SingaporeWindSpeedSensor(weather_coordinator, entry.entry_id),
            SingaporeWindBearingSensor(weather_coordinator, entry.entry_id),
            SingaporeRainfallSensor(weather_coordinator, entry.entry_id),
            SingaporeTrainStatusSensor(train_coordinator, entry.entry_id),
        ]
    )
    for line in TRAIN_LINES:
        entities.append(
            SingaporeTrainLineStatusSensor(train_coordinator, entry.entry_id, line)
        )

    # SP Services household usage sensors — only present when credentials are
    # configured (i.e. the coordinator is not None).
    if sp_services_coordinator is not None:
        entities.extend(
            [
                SpServicesElectricityTodaySensor(
                    sp_services_coordinator, entry.entry_id
                ),
                SpServicesElectricityMonthSensor(
                    sp_services_coordinator, entry.entry_id
                ),
                SpServicesWaterMonthSensor(sp_services_coordinator, entry.entry_id),
                SpServicesElectricityLastMonthSensor(
                    sp_services_coordinator, entry.entry_id
                ),
                SpServicesElectricityEstimatedBillSensor(
                    sp_services_coordinator, tariff_coordinator, entry.entry_id
                ),
                SpServicesWaterLastMonthSensor(
                    sp_services_coordinator, entry.entry_id
                ),
            ]
        )

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
        attrs: dict = {
            "quarter": self.coordinator.data.quarter,
            "year": self.coordinator.data.year,
            "source": "SP Group",
        }
        if self.coordinator.last_updated is not None:
            attrs["last_updated"] = self.coordinator.last_updated.isoformat()
        return attrs

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_energy")},
            "name": "Singapore Energy",
            "manufacturer": "Singapore",
            "model": "SP Group Tariffs",
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
        self._entry_id = entry_id
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
        attrs: dict = {
            "category": f"Category {self._category}",
            "description": _CATEGORY_DESCRIPTIONS[self._category],
            "month": self.coordinator.data.month,
            "bidding_no": self.coordinator.data.bidding_no,
            "source": "data.gov.sg / LTA",
        }
        if self.coordinator.last_updated is not None:
            attrs["last_updated"] = self.coordinator.last_updated.isoformat()
        return attrs

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_coe")},
            "name": "Singapore COE",
            "manufacturer": "Singapore",
            "model": "LTA COE Results",
        }


class _BaseWeatherReadingSensor(
    CoordinatorEntity[SingaporeWeatherCoordinator], SensorEntity
):
    """Base class for realtime weather reading sensors from data.gov.sg collection 1459."""

    _attr_has_entity_name = False
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: SingaporeWeatherCoordinator, entry_id: str, suffix: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{suffix}"

    @property
    def extra_state_attributes(self) -> dict:
        return {"source": "data.gov.sg / NEA (collection 1459)"}

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_weather")},
            "name": "Singapore Weather",
            "manufacturer": "Singapore",
            "model": "NEA Weather",
        }


class SingaporeTemperatureSensor(_BaseWeatherReadingSensor):
    _attr_name = "Singapore Temperature"
    _attr_icon = "mdi:thermometer"
    _attr_native_unit_of_measurement = UNIT_TEMP

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "temperature")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.temperature


class SingaporeHumiditySensor(_BaseWeatherReadingSensor):
    _attr_name = "Singapore Humidity"
    _attr_icon = "mdi:water-percent"
    _attr_native_unit_of_measurement = UNIT_HUMIDITY

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "humidity")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.humidity


class SingaporeWindSpeedSensor(_BaseWeatherReadingSensor):
    _attr_name = "Singapore Wind Speed"
    _attr_icon = "mdi:weather-windy"
    _attr_native_unit_of_measurement = UNIT_WIND_SPEED

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "wind_speed")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.wind_speed


class SingaporeWindBearingSensor(_BaseWeatherReadingSensor):
    _attr_name = "Singapore Wind Bearing"
    _attr_icon = "mdi:compass-outline"
    _attr_native_unit_of_measurement = UNIT_WIND_BEARING

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "wind_bearing")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.wind_bearing


class SingaporeRainfallSensor(_BaseWeatherReadingSensor):
    _attr_name = "Singapore Rainfall"
    _attr_icon = "mdi:weather-rainy"
    _attr_native_unit_of_measurement = UNIT_RAINFALL

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "rainfall")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.precipitation


class SingaporeTrainStatusSensor(
    CoordinatorEntity[TrainStatusCoordinator], SensorEntity
):
    """Overall MRT/LRT network status from mytransport.sg."""

    _attr_has_entity_name = False
    _attr_name = "Singapore Train Status"
    _attr_icon = "mdi:train"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(self, coordinator: TrainStatusCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_train_status"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.status

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        return {
            "details": self.coordinator.data.details,
            "line_statuses": self.coordinator.data.line_statuses,
            "source": "mytransport.sg",
            "url": "https://www.mytransport.sg/trainstatus#",
        }

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_train")},
            "name": "Singapore MRT/LRT",
            "manufacturer": "Singapore",
            "model": "MyTransport Train Status",
        }


class SingaporeTrainLineStatusSensor(
    CoordinatorEntity[TrainStatusCoordinator], SensorEntity
):
    """Status sensor for a single MRT/LRT line."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:train"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self, coordinator: TrainStatusCoordinator, entry_id: str, line_name: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._line_name = line_name
        slug = re.sub(r"[^a-z0-9]+", "_", line_name.lower()).strip("_")
        self._attr_unique_id = f"{entry_id}_train_{slug}_status"
        self._attr_name = f"Singapore {line_name} Status"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.line_statuses.get(self._line_name, "unknown")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "line": self._line_name,
            "source": "mytransport.sg",
            "url": "https://www.mytransport.sg/trainstatus#",
        }

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_train")},
            "name": "Singapore MRT/LRT",
            "manufacturer": "Singapore",
            "model": "MyTransport Train Status",
        }


# ---------------------------------------------------------------------------
# SP Services household usage sensors
# ---------------------------------------------------------------------------


class _BaseSpServicesSensor(CoordinatorEntity[SpServicesCoordinator], SensorEntity):
    """Shared base for SP Services household usage sensors."""

    _attr_has_entity_name = False
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: SpServicesCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        attrs: dict = {"source": "SP Services"}
        if self.coordinator.data.account_no:
            attrs["account_no"] = self.coordinator.data.account_no
        attrs["last_updated"] = self.coordinator.data.last_updated.isoformat()
        return attrs

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_sp_services")},
            "name": "SP Services Account",
            "manufacturer": "SP Group",
            "model": "Household Usage",
        }


class SpServicesElectricityTodaySensor(_BaseSpServicesSensor):
    """Today's household electricity consumption (kWh)."""

    _attr_name = "SP Services Electricity Today"
    _attr_icon = "mdi:lightning-bolt"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: SpServicesCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_sp_electricity_today"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.electricity_today_kwh


class SpServicesElectricityMonthSensor(_BaseSpServicesSensor):
    """This month's household electricity consumption (kWh)."""

    _attr_name = "SP Services Electricity This Month"
    _attr_icon = "mdi:lightning-bolt"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: SpServicesCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_sp_electricity_month"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.electricity_month_kwh


class SpServicesWaterMonthSensor(_BaseSpServicesSensor):
    """This month's household water consumption (m³)."""

    _attr_name = "SP Services Water This Month"
    _attr_icon = "mdi:water"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    def __init__(self, coordinator: SpServicesCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_sp_water_month"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.water_month_m3


class SpServicesElectricityLastMonthSensor(_BaseSpServicesSensor):
    """Previous month's household electricity consumption (kWh)."""

    _attr_name = "SP Services Electricity Last Month"
    _attr_icon = "mdi:lightning-bolt"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: SpServicesCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_sp_electricity_last_month"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.electricity_last_month_kwh


class SpServicesWaterLastMonthSensor(_BaseSpServicesSensor):
    """Previous month's household water consumption (m³)."""

    _attr_name = "SP Services Water Last Month"
    _attr_icon = "mdi:water"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    def __init__(self, coordinator: SpServicesCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_sp_water_last_month"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.water_last_month_m3


class SpServicesElectricityEstimatedBillSensor(_BaseSpServicesSensor):
    """Estimated current month's electricity bill based on the live tariff."""

    _attr_name = "SP Services Electricity Bill Estimate"
    _attr_icon = "mdi:cash"
    _attr_device_class = None
    _attr_native_unit_of_measurement = "SGD"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SpServicesCoordinator,
        tariff_coordinator: SPGroupCoordinator,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._tariff_coordinator = tariff_coordinator
        self._attr_unique_id = f"{entry_id}_sp_electricity_bill_estimate"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None or self._tariff_coordinator.data is None:
            return None
        usage_kwh = self.coordinator.data.electricity_month_kwh
        tariff_cents = self._tariff_coordinator.data.electricity_price
        if usage_kwh is None or tariff_cents is None:
            return None
        return round((usage_kwh * tariff_cents) / 100, 2)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        if self.coordinator.data is None or self._tariff_coordinator.data is None:
            return attrs
        attrs["estimated_from_month_kwh"] = self.coordinator.data.electricity_month_kwh
        attrs["tariff_cents_per_kwh"] = self._tariff_coordinator.data.electricity_price
        attrs["tariff_quarter"] = self._tariff_coordinator.data.quarter
        attrs["tariff_year"] = self._tariff_coordinator.data.year
        attrs["estimate_note"] = (
            "Uses the current SP Group residential tariff and SP Services month-to-date "
            "electricity consumption."
        )
        return attrs
