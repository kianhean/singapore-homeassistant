"""Sensor platform: tariffs, COE, weather, PSI, trains, floods, dengue, lightning, UV."""

from __future__ import annotations

import re

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER,
    DEGREE,
    PERCENTAGE,
    UV_INDEX,
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SingaporeConfigEntry
from .coe_coordinator import (
    _CATEGORY_DESCRIPTIONS,
    COE_CATEGORIES,
    UNIT_COE,
    CoeCoordinator,
)
from .const import DOMAIN
from .coordinator import UNIT_ELECTRICITY, UNIT_GAS, UNIT_WATER, SPGroupCoordinator
from .dengue_coordinator import DengueClusterCoordinator, nearest_cluster
from .flood_coordinator import FloodAlertCoordinator
from .lightning_coordinator import (
    LightningCoordinator,
    nearest_strike,
    strike_distance_km,
)
from .psi_coordinator import (
    DEFAULT_REGION_LOCATIONS,
    PSI_REGIONS,
    PsiCoordinator,
    psi_band,
)
from .train_coordinator import TRAIN_LINES, TrainStatusCoordinator
from .uv_coordinator import UV_EXPOSURE_OPTIONS, UvCoordinator, uv_exposure
from .weather_coordinator import SingaporeWeatherCoordinator

PARALLEL_UPDATES = 0

UNIT_TEMP = UnitOfTemperature.CELSIUS
UNIT_HUMIDITY = PERCENTAGE
UNIT_WIND_SPEED = UnitOfSpeed.KILOMETERS_PER_HOUR
UNIT_WIND_BEARING = DEGREE
UNIT_RAINFALL = UnitOfPrecipitationDepth.MILLIMETERS

TRAIN_STATUS_OPTIONS = ["normal", "planned", "disruption"]

PSI_DATASET_URL = "https://data.gov.sg/datasets/d_fe37906a0182569d891506e815e819b7/view"

# (reading key, translation key, device class, unit) for pollutant sensors.
PSI_POLLUTANT_SENSORS: tuple[tuple[str, str, str | None, str], ...] = (
    (
        "pm25_twenty_four_hourly",
        "pm25",
        SensorDeviceClass.PM25,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    ),
    (
        "pm10_twenty_four_hourly",
        "pm10",
        SensorDeviceClass.PM10,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    ),
    (
        "o3_eight_hour_max",
        "ozone",
        SensorDeviceClass.OZONE,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    ),
    (
        "no2_one_hour_max",
        "nitrogen_dioxide",
        SensorDeviceClass.NITROGEN_DIOXIDE,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    ),
    (
        "so2_twenty_four_hourly",
        "sulphur_dioxide",
        SensorDeviceClass.SULPHUR_DIOXIDE,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    ),
    # HA's CO device class only accepts ppm on older releases, so no device class.
    (
        "co_eight_hour_max",
        "carbon_monoxide",
        None,
        CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SP Group tariff and COE sensors."""
    data = entry.runtime_data
    tariff_coordinator = data.tariff
    coe_coordinator = data.coe
    weather_coordinator = data.weather
    train_coordinator = data.train
    psi_coordinator = data.psi

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

    entities.append(SingaporePsiSensor(psi_coordinator, entry.entry_id))
    for region in PSI_REGIONS:
        entities.append(
            SingaporeRegionalPsiSensor(psi_coordinator, entry.entry_id, region)
        )
    for reading_key, translation_key, device_class, unit in PSI_POLLUTANT_SENSORS:
        entities.append(
            SingaporePollutantSensor(
                psi_coordinator,
                entry.entry_id,
                reading_key,
                translation_key,
                device_class,
                unit,
            )
        )

    entities.extend(
        [
            SingaporeFloodAlertsSensor(data.flood, entry.entry_id),
            SingaporeDengueClustersSensor(data.dengue, entry.entry_id),
            SingaporeNearestDengueClusterSensor(data.dengue, entry.entry_id),
            SingaporeLightningStrikesSensor(data.lightning, entry.entry_id),
            SingaporeNearestLightningSensor(data.lightning, entry.entry_id),
            SingaporeUvIndexSensor(data.uv, entry.entry_id),
            SingaporeUvExposureSensor(data.uv, entry.entry_id),
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
    _attr_attribution = "Data provided by SP Group"
    _attr_device_class = None  # custom price-rate units; no HA device class applies
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

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
    _attr_attribution = "Data provided by LTA via data.gov.sg"
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_COE

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
    _attr_attribution = "Data provided by NEA via data.gov.sg"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

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
    _attr_device_class = SensorDeviceClass.WIND_DIRECTION
    # Angles wrap at 360°, so long-term statistics use a circular mean.
    _attr_state_class = SensorStateClass.MEASUREMENT_ANGLE
    _attr_suggested_display_precision = 0
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
    _attr_attribution = "Data provided by LTA via mytransport.sg"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TRAIN_STATUS_OPTIONS

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


class SingaporeTrainLineStatusSensor(
    CoordinatorEntity[TrainStatusCoordinator], SensorEntity
):
    """Status sensor for a single MRT/LRT line."""

    _attr_has_entity_name = True
    _attr_translation_key = "train_line_status"
    _attr_attribution = "Data provided by LTA via mytransport.sg"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TRAIN_STATUS_OPTIONS

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
        return self.coordinator.data.line_statuses.get(self._line_name)

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


class _BasePsiSensor(CoordinatorEntity[PsiCoordinator], SensorEntity):
    """Shared base for NEA PSI / air quality sensors."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by NEA via data.gov.sg"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: PsiCoordinator, entry_id: str, suffix: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{suffix}"

    def _common_attrs(self) -> dict:
        attrs: dict = {"source": "data.gov.sg / NEA"}
        if self.coordinator.data and self.coordinator.data.timestamp is not None:
            attrs["reading_time"] = self.coordinator.data.timestamp.isoformat()
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_air_quality")},
            name="Air Quality",
            manufacturer="Singapore",
            model="NEA PSI",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=PSI_DATASET_URL,
        )


class SingaporePsiSensor(_BasePsiSensor):
    """Singapore-wide 24-hour PSI (highest regional value)."""

    _attr_translation_key = "psi"
    _attr_device_class = SensorDeviceClass.AQI

    def __init__(self, coordinator: PsiCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "psi")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.national.get("psi_twenty_four_hourly")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = self._common_attrs()
        attrs["band"] = psi_band(self.native_value)
        if self.coordinator.data is not None:
            attrs["regions"] = self.coordinator.data.readings.get(
                "psi_twenty_four_hourly", {}
            )
        return attrs


class SingaporeRegionalPsiSensor(_BasePsiSensor):
    """24-hour PSI for one Singapore region (north/south/east/west/central).

    Carries latitude/longitude and every pollutant reading for its region as
    attributes, so a Map card renders one marker per region with all stats.
    """

    _attr_translation_key = "psi_region"
    _attr_device_class = SensorDeviceClass.AQI

    def __init__(self, coordinator: PsiCoordinator, entry_id: str, region: str) -> None:
        super().__init__(coordinator, entry_id, f"psi_{region}")
        self._region = region
        self._attr_translation_placeholders = {"region": region.title()}

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.get("psi_twenty_four_hourly", {}).get(
            self._region
        )

    @property
    def extra_state_attributes(self) -> dict:
        """All readings for this region, plus a location so it shows on the map."""
        attrs = self._common_attrs()
        attrs["region"] = self._region
        attrs["band"] = psi_band(self.native_value)
        data = self.coordinator.data
        location = (
            data.region_locations.get(self._region)
            if data is not None
            else DEFAULT_REGION_LOCATIONS.get(self._region)
        )
        if location is not None:
            attrs[ATTR_LATITUDE], attrs[ATTR_LONGITUDE] = location
        if data is not None:
            for key in sorted(data.readings):
                value = data.readings[key].get(self._region)
                if value is not None:
                    attrs[key] = value
        return attrs


class SingaporePollutantSensor(_BasePsiSensor):
    """Singapore-wide pollutant concentration (highest regional value)."""

    def __init__(
        self,
        coordinator: PsiCoordinator,
        entry_id: str,
        reading_key: str,
        translation_key: str,
        device_class: str | None,
        unit: str,
    ) -> None:
        super().__init__(coordinator, entry_id, translation_key)
        self._reading_key = reading_key
        self._attr_translation_key = translation_key
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        if reading_key == "co_eight_hour_max":
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.national.get(self._reading_key)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = self._common_attrs()
        attrs["reading"] = self._reading_key
        if self.coordinator.data is not None:
            attrs["regions"] = self.coordinator.data.readings.get(self._reading_key, {})
        return attrs


class SingaporeFloodAlertsSensor(
    CoordinatorEntity[FloodAlertCoordinator], SensorEntity
):
    """Number of active PUB flash-flood alerts, with alert details as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "flood_alerts"
    _attr_attribution = "Data provided by PUB via data.gov.sg"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(self, coordinator: FloodAlertCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_flood_alerts"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.alerts)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        return {
            "alerts": [a.as_dict() for a in data.alerts],
            "locations": [a.description or a.area for a in data.alerts],
            "last_observation": (
                data.last_observation.isoformat() if data.last_observation else None
            ),
            "source": "PUB via data.gov.sg",
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_flood")},
            name="PUB",
            manufacturer="Singapore",
            model="PUB Flood Alerts",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=(
                "https://data.gov.sg/datasets/d_f1404e08587ce555b9ea3f565e2eb9a3/view"
            ),
        )


def _nea_device_info(entry_id: str) -> DeviceInfo:
    """Device shared by the NEA dengue, lightning and UV sensors."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_nea")},
        name="NEA",
        manufacturer="Singapore",
        model="NEA via data.gov.sg",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://www.nea.gov.sg",
    )


class _BaseDengueSensor(CoordinatorEntity[DengueClusterCoordinator], SensorEntity):
    """Shared device info for NEA dengue cluster sensors."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by NEA via data.gov.sg"

    def __init__(self, coordinator: DengueClusterCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return _nea_device_info(self._entry_id)


class SingaporeDengueClustersSensor(_BaseDengueSensor):
    """Number of active dengue clusters island-wide."""

    _attr_translation_key = "dengue_clusters"
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DengueClusterCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_dengue_clusters"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.clusters)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        return {
            "total_cases": data.total_cases,
            "clusters": [c.as_dict() for c in data.clusters],
            "source": "NEA via data.gov.sg",
        }


class SingaporeNearestDengueClusterSensor(_BaseDengueSensor):
    """Distance from the Home Assistant home location to the nearest dengue cluster.

    0 km means home is inside a cluster boundary. Unknown when there are no
    active clusters.
    """

    _attr_translation_key = "nearest_dengue_cluster"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DengueClusterCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_nearest_dengue_cluster"

    def _nearest(self):
        if self.coordinator.data is None:
            return None
        return nearest_cluster(
            self.hass.config.latitude,
            self.hass.config.longitude,
            self.coordinator.data,
        )

    @property
    def native_value(self) -> float | None:
        nearest = self._nearest()
        if nearest is None:
            return None
        return round(nearest[1], 3)

    @property
    def extra_state_attributes(self) -> dict:
        nearest = self._nearest()
        if nearest is None:
            return {}
        cluster, _ = nearest
        return {
            "locality": cluster.locality,
            "case_size": cluster.case_size,
            "inside_cluster": nearest[1] == 0.0,
            "latitude": cluster.latitude,
            "longitude": cluster.longitude,
        }


# Strikes this close to home count as "nearby" (NEA: most strokes fall within
# 5-6 km of a thunderstorm cloud; a storm 10 km away can still strike).
LIGHTNING_NEARBY_KM = 10.0


class _BaseLightningSensor(CoordinatorEntity[LightningCoordinator], SensorEntity):
    """Shared device info for NEA lightning sensors."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by NEA (MSS) via data.gov.sg"

    def __init__(self, coordinator: LightningCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return _nea_device_info(self._entry_id)


class SingaporeLightningStrikesSensor(_BaseLightningSensor):
    """Number of lightning strikes detected in the last 30 minutes."""

    _attr_translation_key = "lightning_strikes"
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LightningCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_lightning_strikes"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.strikes)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        return {
            "cloud_to_ground": data.cloud_to_ground,
            "cloud_to_cloud": len(data.strikes) - data.cloud_to_ground,
            # Newest first; capped so a big storm doesn't bloat the state.
            "strikes": [s.as_dict() for s in data.strikes[:50]],
            "last_observation": (
                data.last_observation.isoformat() if data.last_observation else None
            ),
            "window_minutes": 30,
            "source": "NEA via data.gov.sg",
        }


class SingaporeNearestLightningSensor(_BaseLightningSensor):
    """Distance from the HA home location to the nearest strike in the last 30 min.

    Unknown when no lightning has been detected in that window.
    """

    _attr_translation_key = "nearest_lightning"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: LightningCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_nearest_lightning"

    def _home(self) -> tuple[float, float]:
        return self.hass.config.latitude, self.hass.config.longitude

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        nearest = nearest_strike(*self._home(), self.coordinator.data)
        return None if nearest is None else round(nearest[1], 2)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        lat, lon = self._home()
        nearest = nearest_strike(lat, lon, data)
        within = sum(
            1
            for s in data.strikes
            if strike_distance_km(lat, lon, s) <= LIGHTNING_NEARBY_KM
        )
        attrs: dict = {f"strikes_within_{int(LIGHTNING_NEARBY_KM)}_km": within}
        if nearest is not None:
            strike, _ = nearest
            attrs.update(
                {
                    "type": strike.type,
                    "time": strike.time.isoformat(),
                    "latitude": strike.latitude,
                    "longitude": strike.longitude,
                }
            )
        return attrs


class _BaseUvSensor(CoordinatorEntity[UvCoordinator], SensorEntity):
    """Shared device info for NEA UV index sensors."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by NEA via data.gov.sg"

    def __init__(self, coordinator: UvCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return _nea_device_info(self._entry_id)


class SingaporeUvIndexSensor(_BaseUvSensor):
    """Latest hourly UV index (NEA publishes 7 am to 7 pm)."""

    _attr_translation_key = "uv_index"
    _attr_native_unit_of_measurement = UV_INDEX
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UvCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_uv_index"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if data is None or data.latest is None:
            return None
        return data.latest.value

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if data is None or data.latest is None:
            return {}
        peak = data.peak
        return {
            "hour": data.latest.hour.isoformat(),
            "exposure": uv_exposure(data.latest.value),
            "peak": peak.value,
            "peak_hour": peak.hour.isoformat(),
            "readings": [r.as_dict() for r in data.readings],
            "source": "NEA via data.gov.sg",
        }


class SingaporeUvExposureSensor(_BaseUvSensor):
    """NEA exposure category for the latest UV index (low ... extreme)."""

    _attr_translation_key = "uv_exposure"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = UV_EXPOSURE_OPTIONS

    def __init__(self, coordinator: UvCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_uv_exposure"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.latest is None:
            return None
        return uv_exposure(data.latest.value)
