"""Tests for Singapore weather entity behavior."""

from datetime import datetime
from unittest.mock import MagicMock

from custom_components.singapore.weather import SingaporeAreaWeatherEntity
from custom_components.singapore.weather_coordinator import (
    WeatherAreaData,
    WeatherData,
    WeatherReadings,
)


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_weather_condition_mapping_and_attrs():
    data = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Thundery Showers",
                valid_start=datetime.fromisoformat("2026-04-05T08:00:00+08:00"),
                valid_end=datetime.fromisoformat("2026-04-05T10:00:00+08:00"),
            )
        },
        updated_at=None,
        readings=WeatherReadings(
            temperature=31.2,
            humidity=74.0,
            wind_speed=12.4,
            wind_bearing=180.0,
            precipitation=0.6,
        ),
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")

    assert ent.unique_id == "entry1_weather_bedok"
    assert ent.condition == "lightning-rainy"

    attrs = ent.extra_state_attributes
    assert attrs["forecast_area"] == "Bedok"
    assert attrs["source"] == "data.gov.sg / NEA"
    assert attrs["temperature"] == 31.2
    assert attrs["humidity"] == 74.0


async def test_weather_hourly_forecast_approximation():
    data = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Partly Cloudy",
                valid_start=datetime.fromisoformat("2026-04-05T08:00:00+08:00"),
                valid_end=datetime.fromisoformat("2026-04-05T10:00:00+08:00"),
            )
        },
        updated_at=None,
        readings=WeatherReadings(
            temperature=30.0,
            humidity=80.0,
            wind_speed=10.0,
            wind_bearing=225.0,
            precipitation=1.2,
        ),
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")

    hourly = await ent.async_forecast_hourly()

    assert len(hourly) == 2
    assert hourly[0]["condition"] == "partlycloudy"
    assert hourly[0]["datetime"] == "2026-04-05T00:00:00+00:00"
    assert hourly[1]["datetime"] == "2026-04-05T01:00:00+00:00"
    assert hourly[0]["temperature"] == 30.0
    assert hourly[0]["humidity"] == 80.0
    assert hourly[0]["wind_speed"] == 10.0
    assert hourly[0]["wind_bearing"] == 225.0
    assert hourly[0]["precipitation"] == 1.2
