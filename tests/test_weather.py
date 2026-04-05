"""Tests for Singapore weather entity behavior."""

from datetime import datetime
from unittest.mock import MagicMock

from custom_components.singapore.weather import SingaporeAreaWeatherEntity
from custom_components.singapore.weather_coordinator import WeatherAreaData, WeatherData


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
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")

    assert ent.unique_id == "entry1_weather_bedok"
    assert ent.condition == "lightning-rainy"

    attrs = ent.extra_state_attributes
    assert attrs["forecast_area"] == "Bedok"
    assert attrs["source"] == "data.gov.sg / NEA"


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
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")

    hourly = await ent.async_forecast_hourly()

    assert len(hourly) == 2
    assert hourly[0]["condition"] == "partlycloudy"
    assert hourly[0]["datetime"] == "2026-04-05T00:00:00+00:00"
    assert hourly[1]["datetime"] == "2026-04-05T01:00:00+00:00"
