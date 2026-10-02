"""Tests for Singapore SP Group tariff sensor entities."""

from unittest.mock import MagicMock

from custom_components.singapore.coe_coordinator import UNIT_COE, CoeData
from custom_components.singapore.coordinator import (
    UNIT_ELECTRICITY,
    UNIT_GAS,
    UNIT_WATER,
    TariffData,
)
from custom_components.singapore.sensor import (
    UNIT_HUMIDITY,
    UNIT_RAINFALL,
    UNIT_TEMP,
    UNIT_WIND_BEARING,
    UNIT_WIND_SPEED,
    SingaporeCoeResultSensor,
    SingaporeElectricityTariffSensor,
    SingaporeGasTariffSensor,
    SingaporeHumiditySensor,
    SingaporeRainfallSensor,
    SingaporeSolarExportPriceSensor,
    SingaporeTemperatureSensor,
    SingaporeTrainLineStatusSensor,
    SingaporeTrainStatusSensor,
    SingaporeWaterTariffSensor,
    SingaporeWindBearingSensor,
    SingaporeWindSpeedSensor,
)
from custom_components.singapore.train_coordinator import TrainStatusData
from custom_components.singapore.weather_coordinator import WeatherData, WeatherReadings

_DATA = TariffData(
    electricity_price=29.29,
    network_cost=7.61,
    gas_price=20.14,
    water_price=3.69,
    quarter="Q1",
    year=2025,
)

_COMMON_ATTRS = {"quarter": "Q1", "year": 2025, "source": "SP Group"}


def _coordinator(data=_DATA):
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_updated = None
    return coordinator


# ---------------------------------------------------------------------------
# Electricity tariff
# ---------------------------------------------------------------------------


def test_electricity_value():
    sensor = SingaporeElectricityTariffSensor(_coordinator(), "entry1")
    assert sensor.native_value == 29.29


def test_electricity_unit():
    sensor = SingaporeElectricityTariffSensor(_coordinator(), "entry1")
    assert sensor.native_unit_of_measurement == UNIT_ELECTRICITY


def test_electricity_attributes():
    sensor = SingaporeElectricityTariffSensor(_coordinator(), "entry1")
    assert sensor.extra_state_attributes == _COMMON_ATTRS


def test_electricity_unique_id():
    sensor = SingaporeElectricityTariffSensor(_coordinator(), "entry1")
    assert sensor.unique_id == "entry1_electricity_tariff"
    assert sensor.device_info["identifiers"] == {("singapore", "entry1_energy")}


def test_electricity_none_when_no_data():
    sensor = SingaporeElectricityTariffSensor(_coordinator(data=None), "entry1")
    assert sensor.native_value is None


def test_electricity_no_device_class():
    """¢/kWh is not a valid HA energy unit; device_class must be None."""
    sensor = SingaporeElectricityTariffSensor(_coordinator(), "entry1")
    assert sensor.device_class is None


# ---------------------------------------------------------------------------
# Solar export price
# ---------------------------------------------------------------------------


def test_solar_value():
    sensor = SingaporeSolarExportPriceSensor(_coordinator(), "entry1")
    assert sensor.native_value == round(29.29 - 7.61, 2)


def test_solar_unit():
    sensor = SingaporeSolarExportPriceSensor(_coordinator(), "entry1")
    assert sensor.native_unit_of_measurement == UNIT_ELECTRICITY


def test_solar_attributes():
    sensor = SingaporeSolarExportPriceSensor(_coordinator(), "entry1")
    attrs = sensor.extra_state_attributes
    assert attrs["network_cost"] == 7.61
    assert attrs["total_tariff"] == 29.29
    assert attrs["quarter"] == "Q1"
    assert attrs["year"] == 2025


def test_solar_unique_id():
    sensor = SingaporeSolarExportPriceSensor(_coordinator(), "entry1")
    assert sensor.unique_id == "entry1_solar_export_price"


def test_solar_none_when_no_data():
    sensor = SingaporeSolarExportPriceSensor(_coordinator(data=None), "entry1")
    assert sensor.native_value is None


def test_solar_no_device_class():
    """¢/kWh is not a valid HA energy unit; device_class must be None."""
    sensor = SingaporeSolarExportPriceSensor(_coordinator(), "entry1")
    assert sensor.device_class is None


# ---------------------------------------------------------------------------
# Gas tariff
# ---------------------------------------------------------------------------


def test_gas_value():
    sensor = SingaporeGasTariffSensor(_coordinator(), "entry1")
    assert sensor.native_value == 20.14


def test_gas_unit():
    sensor = SingaporeGasTariffSensor(_coordinator(), "entry1")
    assert sensor.native_unit_of_measurement == UNIT_GAS


def test_gas_attributes():
    sensor = SingaporeGasTariffSensor(_coordinator(), "entry1")
    assert sensor.extra_state_attributes == _COMMON_ATTRS


def test_gas_unique_id():
    sensor = SingaporeGasTariffSensor(_coordinator(), "entry1")
    assert sensor.unique_id == "entry1_gas_tariff"


def test_gas_none_when_no_data():
    sensor = SingaporeGasTariffSensor(_coordinator(data=None), "entry1")
    assert sensor.native_value is None


def test_gas_no_device_class():
    """¢/kWh is not a valid HA energy unit; device_class must be None."""
    sensor = SingaporeGasTariffSensor(_coordinator(), "entry1")
    assert sensor.device_class is None


# ---------------------------------------------------------------------------
# Water tariff
# ---------------------------------------------------------------------------


def test_water_value():
    sensor = SingaporeWaterTariffSensor(_coordinator(), "entry1")
    assert sensor.native_value == 3.69


def test_water_unit():
    sensor = SingaporeWaterTariffSensor(_coordinator(), "entry1")
    assert sensor.native_unit_of_measurement == UNIT_WATER


def test_water_attributes():
    sensor = SingaporeWaterTariffSensor(_coordinator(), "entry1")
    assert sensor.extra_state_attributes == _COMMON_ATTRS


def test_water_unique_id():
    sensor = SingaporeWaterTariffSensor(_coordinator(), "entry1")
    assert sensor.unique_id == "entry1_water_tariff"


def test_water_none_when_no_data():
    sensor = SingaporeWaterTariffSensor(_coordinator(data=None), "entry1")
    assert sensor.native_value is None


def test_water_no_device_class():
    """SGD/m³ is not a valid HA water unit; device_class must be None."""
    sensor = SingaporeWaterTariffSensor(_coordinator(), "entry1")
    assert sensor.device_class is None


# ---------------------------------------------------------------------------
# COE result sensors
# ---------------------------------------------------------------------------

_COE_DATA = CoeData(
    premiums={"A": 95501, "B": 112001, "C": 73001, "D": 9801, "E": 118001},
    month="2026-03",
    bidding_no=1,
)


def _coe_coordinator(data=_COE_DATA):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_coe_cat_a_value():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "A")
    assert sensor.native_value == 95501


def test_coe_cat_e_value():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "E")
    assert sensor.native_value == 118001


def test_coe_unit():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "A")
    assert sensor.native_unit_of_measurement == UNIT_COE


def test_coe_unique_id():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "A")
    assert sensor.unique_id == "entry1_coe_cat_a"
    assert sensor.device_info["identifiers"] == {("singapore", "entry1_coe")}


def test_coe_translation_key_and_placeholder():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "A")
    assert sensor._attr_translation_key == "coe_category"
    assert sensor._attr_translation_placeholders == {"category": "A"}
    assert sensor._attr_has_entity_name is True


def test_coe_attributes():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "B")
    attrs = sensor.extra_state_attributes
    assert attrs["category"] == "Category B"
    assert attrs["month"] == "2026-03"
    assert attrs["bidding_no"] == 1
    assert attrs["source"] == "data.gov.sg / LTA"


def test_coe_none_when_no_data():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(data=None), "entry1", "A")
    assert sensor.native_value is None


def test_coe_no_device_class():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "A")
    assert sensor.device_class is None


def _weather_coordinator(
    data=WeatherData(
        areas={},
        updated_at=None,
        readings=WeatherReadings(
            temperature=31.2,
            humidity=74.0,
            wind_speed=12.5,
            wind_bearing=180.0,
            precipitation=0.4,
        ),
    ),
):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_temperature_sensor_value_unit_and_id():
    sensor = SingaporeTemperatureSensor(_weather_coordinator(), "entry1")
    assert sensor.native_value == 31.2
    assert sensor.native_unit_of_measurement == UNIT_TEMP
    assert sensor.unique_id == "entry1_temperature"
    assert sensor.device_info["identifiers"] == {("singapore", "entry1_weather")}


def test_humidity_sensor_value_unit_and_id():
    sensor = SingaporeHumiditySensor(_weather_coordinator(), "entry1")
    assert sensor.native_value == 74.0
    assert sensor.native_unit_of_measurement == UNIT_HUMIDITY
    assert sensor.unique_id == "entry1_humidity"


def test_wind_speed_sensor_value_unit_and_id():
    sensor = SingaporeWindSpeedSensor(_weather_coordinator(), "entry1")
    assert sensor.native_value == 12.5
    assert sensor.native_unit_of_measurement == UNIT_WIND_SPEED
    assert sensor.unique_id == "entry1_wind_speed"


def test_wind_bearing_sensor_value_unit_and_id():
    sensor = SingaporeWindBearingSensor(_weather_coordinator(), "entry1")
    assert sensor.native_value == 180.0
    assert sensor.native_unit_of_measurement == UNIT_WIND_BEARING
    assert sensor.unique_id == "entry1_wind_bearing"


def test_rainfall_sensor_value_unit_and_id():
    sensor = SingaporeRainfallSensor(_weather_coordinator(), "entry1")
    assert sensor.native_value == 0.4
    assert sensor.native_unit_of_measurement == UNIT_RAINFALL
    assert sensor.unique_id == "entry1_rainfall"


def test_weather_sensors_none_when_no_data():
    weather_none = _weather_coordinator(data=None)
    assert SingaporeTemperatureSensor(weather_none, "entry1").native_value is None
    assert SingaporeHumiditySensor(weather_none, "entry1").native_value is None
    assert SingaporeWindSpeedSensor(weather_none, "entry1").native_value is None
    assert SingaporeWindBearingSensor(weather_none, "entry1").native_value is None
    assert SingaporeRainfallSensor(weather_none, "entry1").native_value is None


def _train_coordinator(
    data=TrainStatusData(
        status="planned",
        details="East-West Line planned disruption due to maintenance.",
        line_statuses={
            "North-South Line": "normal",
            "East-West Line": "planned",
            "North East Line": "normal",
            "Circle Line": "normal",
            "Downtown Line": "normal",
            "Thomson-East Coast Line": "normal",
            "Bukit Panjang LRT": "normal",
            "Sengkang LRT": "normal",
            "Punggol LRT": "normal",
        },
    ),
):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_train_status_sensor_value_and_id():
    sensor = SingaporeTrainStatusSensor(_train_coordinator(), "entry1")
    assert sensor.native_value == "planned"
    assert sensor.unique_id == "entry1_train_status"
    assert sensor.device_info["identifiers"] == {("singapore", "entry1_train")}


def test_train_status_sensor_attributes():
    sensor = SingaporeTrainStatusSensor(_train_coordinator(), "entry1")
    attrs = sensor.extra_state_attributes
    assert "planned disruption" in attrs["details"]
    assert attrs["line_statuses"]["East-West Line"] == "planned"
    assert attrs["source"] == "mytransport.sg"


def test_train_status_sensor_none_when_no_data():
    sensor = SingaporeTrainStatusSensor(_train_coordinator(data=None), "entry1")
    assert sensor.native_value is None


def test_train_line_status_sensor_value_and_id():
    sensor = SingaporeTrainLineStatusSensor(
        _train_coordinator(), "entry1", "East-West Line"
    )
    assert sensor.native_value == "planned"
    assert sensor.unique_id == "entry1_train_east_west_line_status"


def test_train_line_status_sensor_none_when_line_missing():
    sensor = SingaporeTrainLineStatusSensor(
        _train_coordinator(
            data=TrainStatusData(
                status="disruption",
                details="Limited details.",
                line_statuses={},
            )
        ),
        "entry1",
        "East-West Line",
    )
    # ENUM sensors may only report declared options; None renders as unknown.
    assert sensor.native_value is None


def test_train_sensors_are_enum_with_all_statuses_as_options():
    from custom_components.singapore.sensor import TRAIN_STATUS_OPTIONS

    overall = SingaporeTrainStatusSensor(_train_coordinator(), "entry1")
    line = SingaporeTrainLineStatusSensor(
        _train_coordinator(), "entry1", "East-West Line"
    )
    for sensor in (overall, line):
        assert sensor.device_class == "enum"
        assert sensor._attr_options == TRAIN_STATUS_OPTIONS
    assert set(TRAIN_STATUS_OPTIONS) == {"normal", "planned", "disruption"}


def test_wind_bearing_uses_wind_direction_angle_statistics():
    sensor = SingaporeWindBearingSensor(_weather_coordinator(), "entry1")
    assert sensor.device_class == "wind_direction"
    assert sensor._attr_state_class == "measurement_angle"


def _psi_coordinator(data=None):
    from custom_components.singapore.psi_coordinator import _parse_psi
    from tests.test_psi_coordinator import SAMPLE_V2

    coordinator = MagicMock()
    coordinator.data = _parse_psi(SAMPLE_V2) if data is None else data
    return coordinator


def test_psi_sensor_value_band_and_regions():
    from custom_components.singapore.sensor import SingaporePsiSensor

    sensor = SingaporePsiSensor(_psi_coordinator(), "entry1")
    assert sensor.unique_id == "entry1_psi"
    assert sensor.device_class == "aqi"
    assert sensor.native_value == 39.0
    attrs = sensor.extra_state_attributes
    assert attrs["band"] == "good"
    assert attrs["regions"]["east"] == 39.0
    assert attrs["reading_time"] == "2024-07-17T14:00:00+00:00"
    assert sensor.device_info["identifiers"] == {("singapore", "entry1_air_quality")}


def test_regional_psi_sensor_has_map_location_and_all_readings():
    from custom_components.singapore.sensor import SingaporeRegionalPsiSensor

    sensor = SingaporeRegionalPsiSensor(_psi_coordinator(), "entry1", "central")
    assert sensor.unique_id == "entry1_psi_central"
    assert sensor._attr_translation_placeholders == {"region": "Central"}
    assert sensor.native_value == 10.0
    attrs = sensor.extra_state_attributes
    assert (attrs["latitude"], attrs["longitude"]) == (1.35735, 103.82)
    assert attrs["region"] == "central"
    assert attrs["band"] == "good"
    assert attrs["psi_twenty_four_hourly"] == 10.0
    assert attrs["psi_three_hourly"] == 39.0
    assert attrs["pm25_twenty_four_hourly"] == 12.0
    assert attrs["so2_twenty_four_hourly"] == 39.0
    assert attrs["co_sub_index"] == 39.0


def test_regional_psi_sensor_keeps_location_without_data():
    from custom_components.singapore.psi_coordinator import PsiData
    from custom_components.singapore.sensor import SingaporeRegionalPsiSensor

    coordinator = MagicMock()
    coordinator.data = None
    sensor = SingaporeRegionalPsiSensor(coordinator, "entry1", "west")
    assert sensor.native_value is None
    attrs = sensor.extra_state_attributes
    assert (attrs["latitude"], attrs["longitude"]) == (1.35735, 103.7)
    assert attrs["band"] is None

    sensor = SingaporeRegionalPsiSensor(_psi_coordinator(PsiData()), "entry1", "west")
    assert sensor.native_value is None


def test_pollutant_sensors():
    from custom_components.singapore.sensor import (
        PSI_POLLUTANT_SENSORS,
        SingaporePollutantSensor,
    )

    coordinator = _psi_coordinator()
    sensors = {
        translation_key: SingaporePollutantSensor(
            coordinator, "entry1", key, translation_key, device_class, unit
        )
        for key, translation_key, device_class, unit in PSI_POLLUTANT_SENSORS
    }
    pm25 = sensors["pm25"]
    assert pm25.unique_id == "entry1_pm25"
    assert pm25.device_class == "pm25"
    assert pm25.native_unit_of_measurement == "µg/m³"
    assert pm25.native_value == 12.0
    assert pm25.extra_state_attributes["regions"]["central"] == 12.0

    co = sensors["carbon_monoxide"]
    assert co.device_class is None
    assert co.native_unit_of_measurement == "mg/m³"
    assert co.native_value == 19.0

    # Readings absent from the payload report None rather than raising.
    assert sensors["ozone"].native_value is None
