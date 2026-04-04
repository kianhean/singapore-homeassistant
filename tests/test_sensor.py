"""Tests for Singapore SP Group tariff sensor entities."""
from unittest.mock import MagicMock

from custom_components.singapore.coordinator import TariffData, UNIT_ELECTRICITY, UNIT_GAS, UNIT_WATER
from custom_components.singapore.sensor import (
    SingaporeElectricityTariffSensor,
    SingaporeGasTariffSensor,
    SingaporeSolarExportPriceSensor,
    SingaporeWaterTariffSensor,
)

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


def test_electricity_none_when_no_data():
    sensor = SingaporeElectricityTariffSensor(_coordinator(data=None), "entry1")
    assert sensor.native_value is None


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
