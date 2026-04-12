"""Tests for SP Services usage sensors."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from custom_components.singapore.sensor import (
    SpElectricityMonthSensor,
    SpElectricityTodaySensor,
    SpWaterMonthSensor,
    SpWaterTodaySensor,
)
from custom_components.singapore.sp_services_coordinator import SpServicesCoordinator


def _make_coordinator(usage_data=None):
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {"sp_token": "tok"}
    coordinator = SpServicesCoordinator(hass, entry)
    coordinator.data = usage_data
    coordinator.last_updated = datetime(2026, 4, 12, 10, 0) if usage_data else None
    return coordinator


def _usage(**kwargs):
    from sp_services import UsageData

    defaults = dict(
        electricity_today_kwh=4.2,
        electricity_month_kwh=187.5,
        water_today_m3=0.25,
        water_month_m3=9.1,
        account_no="1234567890",
        last_updated=datetime(2026, 4, 12, 10, 0),
        electricity_last_month_kwh=312.1,
        water_last_month_m3=14.8,
    )
    defaults.update(kwargs)
    return UsageData(**defaults)


# ---------------------------------------------------------------------------
# SpElectricityTodaySensor
# ---------------------------------------------------------------------------


def test_electricity_today_value_and_unit():
    sensor = SpElectricityTodaySensor(_make_coordinator(_usage()), "entry1")
    assert sensor.native_value == 4.2
    assert sensor.native_unit_of_measurement == "kWh"


def test_electricity_today_unique_id():
    sensor = SpElectricityTodaySensor(_make_coordinator(_usage()), "entry1")
    assert sensor.unique_id == "entry1_sp_electricity_today"


def test_electricity_today_none_when_no_data():
    sensor = SpElectricityTodaySensor(_make_coordinator(None), "entry1")
    assert sensor.native_value is None


def test_electricity_today_attributes_include_account():
    sensor = SpElectricityTodaySensor(_make_coordinator(_usage()), "entry1")
    attrs = sensor.extra_state_attributes
    assert attrs["account_no"] == "1234567890"
    assert attrs["source"] == "SP Services"


# ---------------------------------------------------------------------------
# SpElectricityMonthSensor
# ---------------------------------------------------------------------------


def test_electricity_month_value():
    sensor = SpElectricityMonthSensor(_make_coordinator(_usage()), "entry1")
    assert sensor.native_value == 187.5


def test_electricity_month_exposes_last_month():
    sensor = SpElectricityMonthSensor(_make_coordinator(_usage()), "entry1")
    assert sensor.extra_state_attributes["last_month_kwh"] == 312.1


def test_electricity_month_no_last_month_when_none():
    sensor = SpElectricityMonthSensor(
        _make_coordinator(_usage(electricity_last_month_kwh=None)), "entry1"
    )
    assert "last_month_kwh" not in sensor.extra_state_attributes


def test_electricity_month_none_when_no_data():
    sensor = SpElectricityMonthSensor(_make_coordinator(None), "entry1")
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# SpWaterTodaySensor
# ---------------------------------------------------------------------------


def test_water_today_value_and_unit():
    sensor = SpWaterTodaySensor(_make_coordinator(_usage()), "entry1")
    assert sensor.native_value == 0.25
    assert sensor.native_unit_of_measurement == "m³"


def test_water_today_unique_id():
    sensor = SpWaterTodaySensor(_make_coordinator(_usage()), "entry1")
    assert sensor.unique_id == "entry1_sp_water_today"


def test_water_today_none_when_no_data():
    sensor = SpWaterTodaySensor(_make_coordinator(None), "entry1")
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# SpWaterMonthSensor
# ---------------------------------------------------------------------------


def test_water_month_value():
    sensor = SpWaterMonthSensor(_make_coordinator(_usage()), "entry1")
    assert sensor.native_value == 9.1


def test_water_month_exposes_last_month():
    sensor = SpWaterMonthSensor(_make_coordinator(_usage()), "entry1")
    assert sensor.extra_state_attributes["last_month_m3"] == 14.8


def test_water_month_no_last_month_when_none():
    sensor = SpWaterMonthSensor(
        _make_coordinator(_usage(water_last_month_m3=None)), "entry1"
    )
    assert "last_month_m3" not in sensor.extra_state_attributes


def test_water_month_none_when_no_data():
    sensor = SpWaterMonthSensor(_make_coordinator(None), "entry1")
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Device info
# ---------------------------------------------------------------------------


def test_device_info_identifier():
    sensor = SpElectricityTodaySensor(_make_coordinator(_usage()), "myentry")
    info = sensor.device_info
    assert ("singapore", "myentry_sp_services") in info["identifiers"]
    assert info["manufacturer"] == "SP Group"
