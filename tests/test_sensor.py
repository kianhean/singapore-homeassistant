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


def test_coe_name():
    sensor = SingaporeCoeResultSensor(_coe_coordinator(), "entry1", "A")
    assert sensor.name == "Singapore COE Category A"


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


def test_train_line_status_sensor_unknown_when_line_missing():
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
    assert sensor.native_value == "unknown"
