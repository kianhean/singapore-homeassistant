"""Sensor platform for Singapore SP Group tariffs and COE results."""

from __future__ import annotations

import re

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SingaporeConfigEntry
from .coe_coordinator import (
    _CATEGORY_DESCRIPTIONS,
    COE_CATEGORIES,
    UNIT_COE,
    CoeCoordinator,
)
from .coordinator import UNIT_ELECTRICITY, UNIT_GAS, UNIT_WATER, SPGroupCoordinator
from .sp_usage_client import UsagePoint
from .train_coordinator import TRAIN_LINES, TrainStatusCoordinator
from .usage_coordinator import SPUsageCoordinator
from .weather_coordinator import SingaporeWeatherCoordinator

PARALLEL_UPDATES = 0

UNIT_TEMP = UnitOfTemperature.CELSIUS
UNIT_HUMIDITY = PERCENTAGE
UNIT_WIND_SPEED = UnitOfSpeed.KILOMETERS_PER_HOUR
UNIT_WIND_BEARING = DEGREE
UNIT_RAINFALL = UnitOfPrecipitationDepth.MILLIMETERS
UNIT_ELECTRICITY_USAGE = UnitOfEnergy.KILO_WATT_HOUR
UNIT_WATER_USAGE = UnitOfVolume.CUBIC_METERS

# Keep attribute payloads bounded — the recorder stores them on every change.
_MAX_HISTORY_ATTR_POINTS = 12


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SP Group tariff and COE sensors."""
    data = entry.runtime_data
    tariff_coordinator = data.tariff
    coe_coordinator = data.coe
    weather_coordinator = data.weather
    train_coordinator = data.train

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

    if data.usage is not None:
        entities.extend(
            [
                SingaporeElectricityUsageTodaySensor(data.usage, entry.entry_id),
                SingaporeElectricityUsageMonthSensor(data.usage, entry.entry_id),
                SingaporeElectricityUsageLastMonthSensor(data.usage, entry.entry_id),
                SingaporeWaterUsageMonthSensor(data.usage, entry.entry_id),
                SingaporeWaterUsageLastMonthSensor(data.usage, entry.entry_id),
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

    _attr_has_entity_name = True
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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_energy")},
            name="Energy",
            manufacturer="Singapore",
            model="SP Group Tariffs",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=(
                "https://www.spgroup.com.sg/our-services/utilities/tariff-information"
            ),
        )


class SingaporeElectricityTariffSensor(_BaseTariffSensor):
    """Total residential electricity tariff (¢/kWh)."""

    _attr_translation_key = "electricity_tariff"
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

    _attr_translation_key = "solar_export_price"
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

    _attr_translation_key = "gas_tariff"
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

    _attr_translation_key = "water_tariff"
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

    _attr_has_entity_name = True
    _attr_translation_key = "coe_category"
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
        self._attr_translation_placeholders = {"category": category}

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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_coe")},
            name="COE",
            manufacturer="Singapore",
            model="LTA COE Results",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=(
                "https://data.gov.sg/datasets/d_69b3380ad7e51aff3a7dcc84eba52b8a/view"
            ),
        )


class _BaseWeatherReadingSensor(
    CoordinatorEntity[SingaporeWeatherCoordinator], SensorEntity
):
    """Base class for realtime weather reading sensors from data.gov.sg collection 1459."""

    _attr_has_entity_name = True
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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_weather")},
            name="Weather",
            manufacturer="Singapore",
            model="NEA Weather",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.nea.gov.sg",
        )


class SingaporeTemperatureSensor(_BaseWeatherReadingSensor):
    _attr_translation_key = "temperature"
    _attr_icon = "mdi:thermometer"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UNIT_TEMP

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "temperature")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.temperature


class SingaporeHumiditySensor(_BaseWeatherReadingSensor):
    _attr_translation_key = "humidity"
    _attr_icon = "mdi:water-percent"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = UNIT_HUMIDITY

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "humidity")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.humidity


class SingaporeWindSpeedSensor(_BaseWeatherReadingSensor):
    _attr_translation_key = "wind_speed"
    _attr_icon = "mdi:weather-windy"
    _attr_device_class = SensorDeviceClass.WIND_SPEED
    _attr_native_unit_of_measurement = UNIT_WIND_SPEED

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "wind_speed")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.wind_speed


class SingaporeWindBearingSensor(_BaseWeatherReadingSensor):
    _attr_translation_key = "wind_bearing"
    _attr_icon = "mdi:compass-outline"
    _attr_device_class = None  # no standard HA device class for wind bearing sensors
    _attr_native_unit_of_measurement = UNIT_WIND_BEARING

    def __init__(self, coordinator: SingaporeWeatherCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "wind_bearing")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.wind_bearing


class SingaporeRainfallSensor(_BaseWeatherReadingSensor):
    _attr_translation_key = "rainfall"
    _attr_icon = "mdi:weather-rainy"
    _attr_device_class = SensorDeviceClass.PRECIPITATION
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

    _attr_has_entity_name = True
    _attr_translation_key = "train_status"
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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_train")},
            name="MRT/LRT",
            manufacturer="Singapore",
            model="MyTransport Train Status",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.mytransport.sg/trainstatus#",
        )


class _BaseUsageSensor(CoordinatorEntity[SPUsageCoordinator], SensorEntity):
    """Base class for SP Services household usage sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SPUsageCoordinator, entry_id: str, suffix: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{suffix}"

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {"source": "SP Services"}
        if self.coordinator.data is not None:
            attrs["account_no"] = self.coordinator.data.account_no
            attrs["last_updated"] = self.coordinator.data.last_updated.isoformat()
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_sp_usage")},
            name="SP Services Usage",
            manufacturer="Singapore",
            model="SP Services Household Usage",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://services.spservices.sg",
        )


def _history_attr(points: list[UsagePoint] | None) -> list[dict]:
    """Render the most recent history points for a state attribute."""
    if not points:
        return []
    return [
        {"period": point.period, "value": point.value, "status": point.status}
        for point in points[-_MAX_HISTORY_ATTR_POINTS:]
    ]


class SingaporeElectricityUsageTodaySensor(_BaseUsageSensor):
    """Electricity consumed today (kWh)."""

    _attr_translation_key = "electricity_usage_today"
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UNIT_ELECTRICITY_USAGE

    def __init__(self, coordinator: SPUsageCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "sp_electricity_usage_today")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.electricity_today_kwh


class SingaporeElectricityUsageMonthSensor(_BaseUsageSensor):
    """Electricity consumed this month (kWh).

    SP publishes the in-progress month late, so this stays ``None`` (unknown,
    not zero) until it appears in the monthly export.
    """

    _attr_translation_key = "electricity_usage_month"
    _attr_icon = "mdi:calendar-month"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UNIT_ELECTRICITY_USAGE

    def __init__(self, coordinator: SPUsageCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "sp_electricity_usage_month")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.electricity_month_kwh

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        if self.coordinator.data is not None:
            attrs["monthly_history"] = _history_attr(
                self.coordinator.data.electricity_monthly_history
            )
        return attrs


class SingaporeElectricityUsageLastMonthSensor(_BaseUsageSensor):
    """Electricity consumed in the last published month (kWh)."""

    _attr_translation_key = "electricity_usage_last_month"
    _attr_icon = "mdi:calendar-arrow-left"
    _attr_device_class = SensorDeviceClass.ENERGY
    # A backwards-looking snapshot, not a meter: no statistics/state class.
    _attr_state_class = None
    _attr_native_unit_of_measurement = UNIT_ELECTRICITY_USAGE

    def __init__(self, coordinator: SPUsageCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "sp_electricity_usage_last_month")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.electricity_last_month_kwh


class SingaporeWaterUsageMonthSensor(_BaseUsageSensor):
    """Water consumed this month (m³).

    SP does not publish same-day water usage in any observed export, so only
    monthly figures exist.
    """

    _attr_translation_key = "water_usage_month"
    _attr_icon = "mdi:water"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UNIT_WATER_USAGE

    def __init__(self, coordinator: SPUsageCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "sp_water_usage_month")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.water_month_m3

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        if self.coordinator.data is not None:
            attrs["monthly_history"] = _history_attr(
                self.coordinator.data.water_monthly_history
            )
        return attrs


class SingaporeWaterUsageLastMonthSensor(_BaseUsageSensor):
    """Water consumed in the last published month (m³)."""

    _attr_translation_key = "water_usage_last_month"
    _attr_icon = "mdi:water-outline"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = None
    _attr_native_unit_of_measurement = UNIT_WATER_USAGE

    def __init__(self, coordinator: SPUsageCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "sp_water_usage_last_month")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.water_last_month_m3


class SingaporeTrainLineStatusSensor(
    CoordinatorEntity[TrainStatusCoordinator], SensorEntity
):
    """Status sensor for a single MRT/LRT line."""

    _attr_has_entity_name = True
    _attr_translation_key = "train_line_status"
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
        self._attr_translation_placeholders = {"line": line_name}

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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_train")},
            name="MRT/LRT",
            manufacturer="Singapore",
            model="MyTransport Train Status",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.mytransport.sg/trainstatus#",
        )
