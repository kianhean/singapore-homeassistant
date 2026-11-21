"""Sensor platform: tariffs, COE, weather, PSI, trains, floods, dengue, lightning,
UV, heat stress, HDB carparks, taxis and CapitaLand mall carparks."""

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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SingaporeConfigEntry
from .capitaland_coordinator import CapitaLandCarparkCoordinator
from .carpark_coordinator import (
    LOT_CAR,
    LOT_TYPE_NAMES,
    CarparkCoordinator,
)
from .coe_coordinator import (
    _CATEGORY_DESCRIPTIONS,
    COE_CATEGORIES,
    UNIT_COE,
    CoeCoordinator,
)
from .const import train_crowd_stations
from .coordinator import UNIT_ELECTRICITY, UNIT_GAS, UNIT_WATER, SPGroupCoordinator
from .dengue_coordinator import DengueClusterCoordinator, nearest_cluster
from .entity import (
    DEVICE_AIR_QUALITY,
    DEVICE_CAPITALAND,
    DEVICE_CARPARK,
    DEVICE_COE,
    DEVICE_ENERGY,
    DEVICE_EV_CHARGERS,
    DEVICE_FLOOD,
    DEVICE_NEA,
    DEVICE_TAXI,
    DEVICE_TRAFFIC_INCIDENTS,
    DEVICE_TRAIN,
    DEVICE_WEATHER,
    SingaporeEntity,
)
from .ev_charger_coordinator import EvChargerCoordinator
from .flood_coordinator import FloodAlertCoordinator
from .lightning_coordinator import (
    LightningCoordinator,
    nearest_strike,
    strike_distance_km,
)
from .psi_coordinator import (
    DEFAULT_REGION_LOCATIONS,
    PM25_ONE_HOUR_KEY,
    PSI_REGIONS,
    PsiCoordinator,
    pm25_band,
    psi_band,
)
from .sp_account_sensor import async_setup_sp_account_sensors
from .taxi_coordinator import NEARBY_KM, TaxiAvailabilityCoordinator
from .tide_coordinator import (
    TIDE_STATE_HIGH,
    TIDE_STATE_LOW,
    TIDE_STATE_OPTIONS,
    TideCoordinator,
)
from .traffic_incidents_coordinator import TrafficIncidentsCoordinator
from .train_coordinator import TRAIN_LINES, TrainStatusCoordinator
from .train_crowd_coordinator import CROWD_LEVEL_OPTIONS, TrainCrowdCoordinator
from .uv_coordinator import UV_EXPOSURE_OPTIONS, UvCoordinator, uv_exposure
from .warning_coordinator import WeatherWarningsCoordinator
from .wbgt_coordinator import HEAT_STRESS_OPTIONS, WbgtCoordinator
from .weather_coordinator import SingaporeWeatherCoordinator

PARALLEL_UPDATES = 0

UNIT_TEMP = UnitOfTemperature.CELSIUS
UNIT_HUMIDITY = PERCENTAGE
UNIT_WIND_SPEED = UnitOfSpeed.KILOMETERS_PER_HOUR
UNIT_WIND_BEARING = DEGREE
UNIT_RAINFALL = UnitOfPrecipitationDepth.MILLIMETERS

TRAIN_STATUS_OPTIONS = ["normal", "planned", "disruption"]

# (reading key, translation key, device class, unit) for pollutant sensors.
PSI_POLLUTANT_SENSORS: tuple[tuple[str, str, str | None, str], ...] = (
    (
        "pm25_twenty_four_hourly",
        "pm25",
        SensorDeviceClass.PM25,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    ),
    (
        PM25_ONE_HOUR_KEY,
        "pm25_one_hour",
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
    eid = entry.entry_id
    entities: list[SensorEntity] = []

    # A source turned off in the options has no coordinator and no entities.
    if (tariff := data.tariff) is not None:
        entities += [
            SingaporeElectricityTariffSensor(tariff, eid),
            SingaporeSolarExportPriceSensor(tariff, eid),
            SingaporeGasTariffSensor(tariff, eid),
            SingaporeWaterTariffSensor(tariff, eid),
        ]
    if (coe := data.coe) is not None:
        entities += [SingaporeCoeResultSensor(coe, eid, cat) for cat in COE_CATEGORIES]
    if (weather := data.weather) is not None:
        entities += [
            SingaporeTemperatureSensor(weather, eid),
            SingaporeHumiditySensor(weather, eid),
            SingaporeWindSpeedSensor(weather, eid),
            SingaporeWindBearingSensor(weather, eid),
            SingaporeRainfallSensor(weather, eid),
        ]
    if (train := data.train) is not None:
        entities.append(SingaporeTrainStatusSensor(train, eid))
        entities += [
            SingaporeTrainLineStatusSensor(train, eid, line) for line in TRAIN_LINES
        ]
    if (psi := data.psi) is not None:
        entities.append(SingaporePsiSensor(psi, eid))
        entities += [
            SingaporeRegionalPsiSensor(psi, eid, region) for region in PSI_REGIONS
        ]
        entities += [
            SingaporePollutantSensor(
                psi, eid, reading_key, translation_key, device_class, unit
            )
            for reading_key, translation_key, device_class, unit in (
                PSI_POLLUTANT_SENSORS
            )
        ]
    if data.flood is not None:
        entities.append(SingaporeFloodAlertsSensor(data.flood, eid))
    if (dengue := data.dengue) is not None:
        entities += [
            SingaporeDengueClustersSensor(dengue, eid),
            SingaporeNearestDengueClusterSensor(dengue, eid),
        ]
    if (lightning := data.lightning) is not None:
        entities += [
            SingaporeLightningStrikesSensor(lightning, eid),
            SingaporeNearestLightningSensor(lightning, eid),
        ]
    if (uv := data.uv) is not None:
        entities += [
            SingaporeUvIndexSensor(uv, eid),
            SingaporeUvExposureSensor(uv, eid),
        ]
    if (wbgt := data.wbgt) is not None:
        entities += [
            SingaporeWbgtSensor(wbgt, eid),
            SingaporeHeatStressSensor(wbgt, eid),
        ]
    if (warnings := data.warnings) is not None:
        entities += [
            SingaporeWeatherAdvisoriesSensor(warnings, eid),
            SingaporeLatestEarthquakeSensor(warnings, eid),
        ]
    if (tides := data.tides) is not None:
        entities += [
            SingaporeNextTideSensor(tides, eid),
            SingaporeNextTideLevelSensor(tides, eid),
        ]
    if (traffic_incidents := data.traffic_incidents) is not None:
        entities += [
            SingaporeTrafficIncidentsSensor(traffic_incidents, eid),
            SingaporeNearestTrafficIncidentSensor(traffic_incidents, eid),
        ]
    if (taxi := data.taxi) is not None:
        entities += [
            SingaporeTaxisAvailableSensor(taxi, eid),
            SingaporeTaxisNearbySensor(taxi, eid),
        ]
    if (train_crowd := data.train_crowd) is not None:
        entities += [
            SingaporeTrainCrowdSensor(train_crowd, eid, code)
            for code in train_crowd_stations(entry)
        ]
    if (ev_chargers := data.ev_chargers) is not None:
        entities += [
            SingaporeEvChargersAvailableSensor(ev_chargers, eid),
            SingaporeNearestEvChargerSensor(ev_chargers, eid),
        ]

    async_add_entities(entities)

    if (carpark := data.carpark) is not None:
        entry.async_on_unload(
            async_setup_carpark_sensors(carpark, eid, async_add_entities)
        )
    if (capitaland := data.capitaland) is not None:
        entry.async_on_unload(
            async_setup_capitaland_sensors(capitaland, eid, async_add_entities)
        )

    for unsub in async_setup_sp_account_sensors(
        entry.entry_id, data.sp_accounts, async_add_entities
    ):
        entry.async_on_unload(unsub)


class _BaseTariffSensor(SingaporeEntity[SPGroupCoordinator], SensorEntity):
    """Shared base for SP Group tariff sensors.

    Tariff sensors report price-per-unit values (e.g. ¢/kWh, SGD/m³).
    These are not standard HA energy/gas/water units, so device_class is
    explicitly None to prevent HA's unit-validation warning at line 729 of
    homeassistant/components/sensor/__init__.py.
    """

    _device = DEVICE_ENERGY
    _attr_attribution = "Data provided by SP Group"
    _attr_device_class = None  # custom price-rate units; no HA device class applies
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

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


class SingaporeElectricityTariffSensor(_BaseTariffSensor):
    """Total residential electricity tariff (¢/kWh)."""

    _attr_translation_key = "electricity_tariff"
    _attr_native_unit_of_measurement = UNIT_ELECTRICITY

    def __init__(self, coordinator: SPGroupCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "electricity_tariff")

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
        super().__init__(coordinator, entry_id, "solar_export_price")

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
        super().__init__(coordinator, entry_id, "gas_tariff")

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
        super().__init__(coordinator, entry_id, "water_tariff")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.water_price if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict:
        return self._common_attrs()


class SingaporeCoeResultSensor(SingaporeEntity[CoeCoordinator], SensorEntity):
    """COE bidding result (premium in SGD) for a single vehicle category."""

    _device = DEVICE_COE
    _attr_translation_key = "coe_category"
    _attr_attribution = "Data provided by LTA via data.gov.sg"
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_COE

    def __init__(
        self, coordinator: CoeCoordinator, entry_id: str, category: str
    ) -> None:
        super().__init__(coordinator, entry_id, f"coe_cat_{category.lower()}")
        self._category = category
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


class _BaseWeatherReadingSensor(
    SingaporeEntity[SingaporeWeatherCoordinator], SensorEntity
):
    """Base class for realtime weather reading sensors from data.gov.sg collection 1459."""

    _device = DEVICE_WEATHER
    _metric_key: str
    _attr_attribution = "Data provided by NEA via data.gov.sg"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {"source": "data.gov.sg / NEA (collection 1459)"}
        data = self.coordinator.data
        if (
            data is not None
            and (station := data.readings.stations.get(self._metric_key)) is not None
        ):
            attrs["station"] = station.name
            attrs["station_id"] = station.station_id
            attrs["distance_km"] = round(station.distance_km, 2)
            attrs["island_average"] = station.average
        return attrs


class SingaporeTemperatureSensor(_BaseWeatherReadingSensor):
    _metric_key = "temperature"
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
    _metric_key = "humidity"
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
    _metric_key = "wind_speed"
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
    _metric_key = "wind_bearing"
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
    _metric_key = "precipitation"
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


class SingaporeTrainStatusSensor(SingaporeEntity[TrainStatusCoordinator], SensorEntity):
    """Overall MRT/LRT network status from mytransport.sg."""

    _device = DEVICE_TRAIN
    _attr_translation_key = "train_status"
    _attr_attribution = "Data provided by LTA via mytransport.sg"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TRAIN_STATUS_OPTIONS

    def __init__(self, coordinator: TrainStatusCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "train_status")

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


class SingaporeTrainLineStatusSensor(
    SingaporeEntity[TrainStatusCoordinator], SensorEntity
):
    """Status sensor for a single MRT/LRT line."""

    _device = DEVICE_TRAIN
    _attr_translation_key = "train_line_status"
    _attr_attribution = "Data provided by LTA via mytransport.sg"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TRAIN_STATUS_OPTIONS

    def __init__(
        self, coordinator: TrainStatusCoordinator, entry_id: str, line_name: str
    ) -> None:
        slug = re.sub(r"[^a-z0-9]+", "_", line_name.lower()).strip("_")
        super().__init__(coordinator, entry_id, f"train_{slug}_status")
        self._line_name = line_name
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


class _BasePsiSensor(SingaporeEntity[PsiCoordinator], SensorEntity):
    """Shared base for NEA PSI / air quality sensors."""

    _device = DEVICE_AIR_QUALITY
    _attr_attribution = "Data provided by NEA via data.gov.sg"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def _common_attrs(self) -> dict:
        attrs: dict = {"source": "data.gov.sg / NEA"}
        if self.coordinator.data and self.coordinator.data.timestamp is not None:
            attrs["reading_time"] = self.coordinator.data.timestamp.isoformat()
        return attrs


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
        if self._reading_key == PM25_ONE_HOUR_KEY:
            # NEA's 1-hour PM2.5 band (normal/elevated/high/very_high).
            attrs["band"] = pm25_band(self.native_value)
        if self.coordinator.data is not None:
            attrs["regions"] = self.coordinator.data.readings.get(self._reading_key, {})
        return attrs


class SingaporeFloodAlertsSensor(SingaporeEntity[FloodAlertCoordinator], SensorEntity):
    """Number of active PUB flash-flood alerts, with alert details as attributes."""

    _device = DEVICE_FLOOD
    _attr_translation_key = "flood_alerts"
    _attr_attribution = "Data provided by PUB via data.gov.sg"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(self, coordinator: FloodAlertCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "flood_alerts")

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


class _BaseDengueSensor(SingaporeEntity[DengueClusterCoordinator], SensorEntity):
    """Shared base for NEA dengue cluster sensors."""

    _device = DEVICE_NEA
    _attr_attribution = "Data provided by NEA via data.gov.sg"


class SingaporeDengueClustersSensor(_BaseDengueSensor):
    """Number of active dengue clusters island-wide."""

    _attr_translation_key = "dengue_clusters"
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DengueClusterCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "dengue_clusters")

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
        super().__init__(coordinator, entry_id, "nearest_dengue_cluster")

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


class _BaseLightningSensor(SingaporeEntity[LightningCoordinator], SensorEntity):
    """Shared base for NEA lightning sensors."""

    _device = DEVICE_NEA
    _attr_attribution = "Data provided by NEA (MSS) via data.gov.sg"


class SingaporeLightningStrikesSensor(_BaseLightningSensor):
    """Number of lightning strikes detected in the last 30 minutes."""

    _attr_translation_key = "lightning_strikes"
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LightningCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "lightning_strikes")

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
        super().__init__(coordinator, entry_id, "nearest_lightning")

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


class _BaseUvSensor(SingaporeEntity[UvCoordinator], SensorEntity):
    """Shared base for NEA UV index sensors."""

    _device = DEVICE_NEA
    _attr_attribution = "Data provided by NEA via data.gov.sg"


class SingaporeUvIndexSensor(_BaseUvSensor):
    """Latest hourly UV index (NEA publishes 7 am to 7 pm)."""

    _attr_translation_key = "uv_index"
    _attr_native_unit_of_measurement = UV_INDEX
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UvCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "uv_index")

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
        super().__init__(coordinator, entry_id, "uv_exposure")

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.latest is None:
            return None
        return uv_exposure(data.latest.value)


class _BaseWbgtSensor(SingaporeEntity[WbgtCoordinator], SensorEntity):
    """Shared base for NEA heat stress sensors (nearest station to home)."""

    _device = DEVICE_NEA
    _attr_attribution = "Data provided by NEA via data.gov.sg"

    def _nearest(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.nearest(
            self.hass.config.latitude, self.hass.config.longitude
        )


class SingaporeWbgtSensor(_BaseWbgtSensor):
    """Wet Bulb Globe Temperature at the station nearest home."""

    _attr_translation_key = "wbgt"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_TEMP
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: WbgtCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "wbgt")

    @property
    def native_value(self) -> float | None:
        nearest = self._nearest()
        return None if nearest is None else nearest[0].wbgt

    @property
    def extra_state_attributes(self) -> dict:
        nearest = self._nearest()
        if nearest is None:
            return {}
        reading, distance = nearest
        data = self.coordinator.data
        highest = data.highest
        return {
            "heat_stress": reading.heat_stress,
            "station": reading.name,
            "town": reading.town,
            "distance_km": round(distance, 2),
            ATTR_LATITUDE: reading.latitude,
            ATTR_LONGITUDE: reading.longitude,
            "highest_wbgt": highest.wbgt if highest else None,
            "highest_station": highest.name if highest else None,
            "reading_time": data.timestamp.isoformat() if data.timestamp else None,
            "source": "NEA via data.gov.sg",
        }


class SingaporeHeatStressSensor(_BaseWbgtSensor):
    """NEA heat stress category (low/moderate/high) at the station nearest home."""

    _attr_translation_key = "heat_stress"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = HEAT_STRESS_OPTIONS

    def __init__(self, coordinator: WbgtCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "heat_stress")

    @property
    def native_value(self) -> str | None:
        nearest = self._nearest()
        return None if nearest is None else nearest[0].heat_stress


class _BaseWeatherWarningsSensor(
    SingaporeEntity[WeatherWarningsCoordinator], SensorEntity
):
    """Shared base for NEA weather warnings and regional advisory sensors."""

    _device = DEVICE_NEA
    _attr_attribution = "Data provided by NEA"


class SingaporeWeatherAdvisoriesSensor(_BaseWeatherWarningsSensor):
    """Count of active NEA weather warnings and regional advisories."""

    _attr_translation_key = "weather_advisories"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WeatherWarningsCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "weather_advisories")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.active_advisories)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        active = data.active_advisories
        return {
            "active_categories": [a.category for a in active],
            "advisories": {cat: a.as_dict() for cat, a in data.advisories.items()},
            "source": "NEA",
        }


class SingaporeLatestEarthquakeSensor(_BaseWeatherWarningsSensor):
    """Magnitude of the latest regional earthquake reported by NEA (MSS)."""

    _attr_translation_key = "latest_earthquake"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: WeatherWarningsCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "latest_earthquake")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.earthquake_magnitude

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        eq = self.coordinator.data.earthquake
        if eq is None:
            return {"source": "NEA"}
        return {
            "advisory": eq.text,
            "issued": eq.issued.isoformat() if eq.issued else None,
            "expires": eq.expires.isoformat() if eq.expires else None,
            "url": eq.link,
            "source": "NEA",
        }


class _BaseTideSensor(SingaporeEntity[TideCoordinator], SensorEntity):
    """Shared base for NEA tide timing sensors."""

    _device = DEVICE_NEA
    _attr_attribution = "Data provided by NEA"

    def _next(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.next_tide(dt_util.now())


class SingaporeNextTideSensor(_BaseTideSensor):
    """Next tide type (high or low) and upcoming schedule."""

    _attr_translation_key = "next_tide"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TIDE_STATE_OPTIONS

    def __init__(self, coordinator: TideCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "next_tide")

    @property
    def native_value(self) -> str | None:
        nxt = self._next()
        return None if nxt is None else nxt.state

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        nxt = self._next()
        if data is None or nxt is None:
            return {}
        now = dt_util.now()
        next_high = data.next_of_state(now, TIDE_STATE_HIGH)
        next_low = data.next_of_state(now, TIDE_STATE_LOW)
        upcoming = [t.as_dict() for t in data.tides if t.time >= now][:8]
        return {
            "time": nxt.time.isoformat(),
            "height_m": nxt.height,
            "next_high_tide": next_high.time.isoformat() if next_high else None,
            "next_high_height_m": next_high.height if next_high else None,
            "next_low_tide": next_low.time.isoformat() if next_low else None,
            "next_low_height_m": next_low.height if next_low else None,
            "upcoming": upcoming,
            "source": "NEA",
        }


class SingaporeNextTideLevelSensor(_BaseTideSensor):
    """Predicted height (m) of the next tide."""

    _attr_translation_key = "next_tide_level"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: TideCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "next_tide_level")

    @property
    def native_value(self) -> float | None:
        nxt = self._next()
        return None if nxt is None else nxt.height

    @property
    def extra_state_attributes(self) -> dict:
        nxt = self._next()
        if nxt is None:
            return {}
        return {
            "tide": nxt.state,
            "time": nxt.time.isoformat(),
            "source": "NEA",
        }


class _BaseTrafficIncidentsSensor(
    SingaporeEntity[TrafficIncidentsCoordinator], SensorEntity
):
    """Shared base for LTA traffic incident sensors."""

    _device = DEVICE_TRAFFIC_INCIDENTS
    _attr_attribution = "Data provided by LTA via mytransport.sg"


class SingaporeTrafficIncidentsSensor(_BaseTrafficIncidentsSensor):
    """Number of active LTA traffic incidents island-wide."""

    _attr_translation_key = "traffic_incidents"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TrafficIncidentsCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "traffic_incidents")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.incidents)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        data = self.coordinator.data
        return {
            "counts_by_type": data.counts_by_type,
            "incidents": [i.as_dict() for i in data.incidents[:50]],
            "source": "LTA via mytransport.sg",
        }


class SingaporeNearestTrafficIncidentSensor(_BaseTrafficIncidentsSensor):
    """Distance from the Home Assistant home location to the nearest traffic incident."""

    _attr_translation_key = "nearest_traffic_incident"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: TrafficIncidentsCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "nearest_traffic_incident")

    def _nearest(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.nearest(
            self.hass.config.latitude, self.hass.config.longitude
        )

    @property
    def native_value(self) -> float | None:
        nearest = self._nearest()
        return None if nearest is None else round(nearest[1], 3)

    @property
    def extra_state_attributes(self) -> dict:
        nearest = self._nearest()
        if nearest is None:
            return {}
        incident, _ = nearest
        return {
            "type": incident.type,
            "message": incident.message,
            ATTR_LATITUDE: incident.latitude,
            ATTR_LONGITUDE: incident.longitude,
            "source": "LTA via mytransport.sg",
        }


class _BaseTaxiSensor(SingaporeEntity[TaxiAvailabilityCoordinator], SensorEntity):
    """Shared base for LTA taxi availability sensors."""

    _device = DEVICE_TAXI
    _attr_attribution = "Data provided by LTA via data.gov.sg"
    _attr_state_class = SensorStateClass.MEASUREMENT


class SingaporeTaxisAvailableSensor(_BaseTaxiSensor):
    """Free taxis island-wide."""

    _attr_translation_key = "taxis_available"
    _attr_native_unit_of_measurement = "taxis"

    def __init__(self, coordinator: TaxiAvailabilityCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "taxis_available")

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return None if data is None else data.total

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "reading_time": data.timestamp.isoformat() if data.timestamp else None,
            "source": "LTA via data.gov.sg",
        }


class SingaporeTaxisNearbySensor(_BaseTaxiSensor):
    """Free taxis within 1 km of the Home Assistant home location."""

    _attr_translation_key = "taxis_nearby"
    _attr_native_unit_of_measurement = "taxis"

    def __init__(self, coordinator: TaxiAvailabilityCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "taxis_nearby")

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return None if data is None else data.nearby

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "radius_km": NEARBY_KM,
            "within_500_m": data.within_500_m,
            "within_2_km": data.within_2_km,
            "nearest_km": data.nearest_km,
            "reading_time": data.timestamp.isoformat() if data.timestamp else None,
        }


@callback
def async_setup_carpark_sensors(
    coordinator: CarparkCoordinator,
    entry_id: str,
    async_add_entities: AddConfigEntryEntitiesCallback,
):
    """Add a sensor per tracked carpark as they become known; return the unsub.

    The carparks nearest home are only known once the carpark information has
    loaded (the first refresh runs in the background), so sensors are added
    from the coordinator's data rather than at setup.
    """
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if data is None:
            return
        fresh = [number for number in data.tracked if number not in known]
        if not fresh:
            return
        known.update(fresh)
        async_add_entities(
            [SingaporeCarparkSensor(coordinator, entry_id, n) for n in fresh]
        )

    _add_new()
    return coordinator.async_add_listener(_add_new)


class SingaporeCarparkSensor(SingaporeEntity[CarparkCoordinator], SensorEntity):
    """Car lots available at one HDB carpark."""

    _device = DEVICE_CARPARK
    _attr_translation_key = "carpark"
    _attr_attribution = "Data provided by HDB via data.gov.sg"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "lots"

    def __init__(
        self, coordinator: CarparkCoordinator, entry_id: str, number: str
    ) -> None:
        super().__init__(coordinator, entry_id, f"carpark_{number.lower()}")
        self._number = number
        self._attr_translation_placeholders = {"number": number}

    def _availability(self):
        data = self.coordinator.data
        return None if data is None else data.availability.get(self._number)

    @property
    def native_value(self) -> int | None:
        availability = self._availability()
        if availability is None or (car := availability.lots.get(LOT_CAR)) is None:
            return None
        return car.available

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        attrs: dict = {"carpark_number": self._number}
        if data is None:
            return attrs
        if (availability := self._availability()) is not None:
            for lot_type, count in availability.lots.items():
                name = LOT_TYPE_NAMES.get(lot_type, lot_type.lower())
                attrs[f"{name}_lots_available"] = count.available
                attrs[f"{name}_lots_total"] = count.total
            if availability.updated is not None:
                attrs["updated"] = availability.updated.isoformat()
        if (info := data.info.get(self._number)) is not None:
            attrs["address"] = info.address
            if info.latitude is not None and info.longitude is not None:
                attrs[ATTR_LATITUDE] = info.latitude
                attrs[ATTR_LONGITUDE] = info.longitude
                distance = info.distance_km(
                    self.hass.config.latitude, self.hass.config.longitude
                )
                if distance is not None:
                    attrs["distance_km"] = round(distance, 2)
            for key in (
                "car_park_type",
                "parking_system",
                "short_term_parking",
                "free_parking",
                "night_parking",
                "gantry_height",
            ):
                if (value := getattr(info, key)) is not None:
                    attrs[key] = value
        return attrs


@callback
def async_setup_capitaland_sensors(
    coordinator: CapitaLandCarparkCoordinator,
    entry_id: str,
    async_add_entities: AddConfigEntryEntitiesCallback,
):
    """Add a sensor per CapitaLand mall as it reports lots; return the unsub.

    The first refresh runs in the background, and CapitaLand adds or drops
    malls without notice, so sensors come from the coordinator's data.
    """
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if data is None:
            return
        fresh = [site for site in data.malls() if site.code not in known]
        if not fresh:
            return
        known.update(site.code for site in fresh)
        async_add_entities(
            [
                SingaporeCapitaLandCarparkSensor(
                    coordinator, entry_id, site.code, site.name
                )
                for site in fresh
            ]
        )

    _add_new()
    return coordinator.async_add_listener(_add_new)


class SingaporeCapitaLandCarparkSensor(
    SingaporeEntity[CapitaLandCarparkCoordinator], SensorEntity
):
    """Car lots available at one CapitaLand mall."""

    _device = DEVICE_CAPITALAND
    _attr_translation_key = "capitaland_carpark"
    _attr_attribution = "Data provided by CapitaLand JustPark"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "lots"

    def __init__(
        self,
        coordinator: CapitaLandCarparkCoordinator,
        entry_id: str,
        code: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry_id, f"capitaland_{code.lower()}")
        self._code = code
        self._attr_translation_placeholders = {"mall": name}

    def _site(self):
        data = self.coordinator.data
        return None if data is None else data.sites.get(self._code)

    @property
    def native_value(self) -> int | None:
        site = self._site()
        return None if site is None else site.available

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {"site_code": self._code}
        if (site := self._site()) is not None:
            attrs["total_lots"] = site.total
            attrs["full"] = site.full
        return attrs


class SingaporeTrainCrowdSensor(SingaporeEntity[TrainCrowdCoordinator], SensorEntity):
    """Real-time platform crowd density for one MRT/LRT station."""

    _device = DEVICE_TRAIN
    _attr_translation_key = "train_crowd"
    _attr_attribution = "Data provided by LTA DataMall"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CROWD_LEVEL_OPTIONS

    def __init__(
        self, coordinator: TrainCrowdCoordinator, entry_id: str, station_code: str
    ) -> None:
        super().__init__(coordinator, entry_id, f"train_crowd_{station_code.lower()}")
        self._station = station_code
        self._attr_translation_placeholders = {"station": station_code}

    def _station_crowd(self):
        data = self.coordinator.data
        return None if data is None else data.stations.get(self._station)

    @property
    def native_value(self) -> str | None:
        crowd = self._station_crowd()
        return None if crowd is None else crowd.crowd_level

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {"station": self._station, "source": "LTA DataMall (PCDRealTime)"}
        if (crowd := self._station_crowd()) is not None:
            attrs["train_line"] = crowd.line
            if crowd.start_time is not None:
                attrs["start_time"] = crowd.start_time
            if crowd.end_time is not None:
                attrs["end_time"] = crowd.end_time
        if self.coordinator.last_updated is not None:
            attrs["last_updated"] = self.coordinator.last_updated.isoformat()
        return attrs


class SingaporeEvChargersAvailableSensor(
    SingaporeEntity[EvChargerCoordinator], SensorEntity
):
    """Count of available EV charging connectors at tracked or nearby locations."""

    _device = DEVICE_EV_CHARGERS
    _attr_translation_key = "ev_chargers_available"
    _attr_attribution = "Data provided by LTA DataMall"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "chargers"

    def __init__(self, coordinator: EvChargerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "ev_chargers_available")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.available_chargers

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {"source": "LTA DataMall"}
        data = self.coordinator.data
        if data is not None:
            attrs["total_chargers"] = data.total_chargers
            attrs["locations_count"] = len(data.locations)
            attrs["locations"] = [
                {
                    "name": loc.name,
                    "address": loc.address,
                    "available": loc.available,
                    "total": loc.total,
                    "operators": loc.operators,
                    "plug_types": loc.plug_types,
                    "max_power_kw": loc.max_power_kw,
                    "distance_km": loc.distance_km,
                }
                for loc in data.locations[:10]
            ]
        if self.coordinator.last_updated is not None:
            attrs["last_updated"] = self.coordinator.last_updated.isoformat()
        return attrs


class SingaporeNearestEvChargerSensor(
    SingaporeEntity[EvChargerCoordinator], SensorEntity
):
    """Distance to the nearest location with an available EV charger."""

    _device = DEVICE_EV_CHARGERS
    _attr_translation_key = "nearest_ev_charger"
    _attr_attribution = "Data provided by LTA DataMall"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: EvChargerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "nearest_ev_charger")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is None or data.nearest_available is None:
            return None
        return data.nearest_available.distance_km

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {"source": "LTA DataMall"}
        data = self.coordinator.data
        if data is not None and (nearest := data.nearest_available) is not None:
            attrs["name"] = nearest.name
            attrs["address"] = nearest.address
            attrs["available"] = nearest.available
            attrs["total"] = nearest.total
            attrs["operators"] = nearest.operators
            attrs["plug_types"] = nearest.plug_types
            if nearest.max_power_kw is not None:
                attrs["max_power_kw"] = nearest.max_power_kw
        if self.coordinator.last_updated is not None:
            attrs["last_updated"] = self.coordinator.last_updated.isoformat()
        return attrs
