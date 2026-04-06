"""Tests for Singapore weather entity behavior."""

from datetime import datetime
from unittest.mock import MagicMock

from custom_components.singapore.weather import SingaporeAreaWeatherEntity
from custom_components.singapore.weather_coordinator import (
    FourDayForecastEntry,
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
    assert ent.device_info["identifiers"] == {("singapore", "entry1_weather")}

    attrs = ent.extra_state_attributes
    assert attrs["forecast_area"] == "Bedok"
    assert attrs["source"] == "data.gov.sg / NEA"
    assert attrs["temperature"] == 31.2
    assert attrs["humidity"] == 74.0


async def test_weather_hourly_forecast_is_unsupported():
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

    assert await ent.async_forecast_hourly() is None


# ---------------------------------------------------------------------------
# native_temperature tests
# ---------------------------------------------------------------------------


def test_weather_entity_native_temperature_from_readings():
    from datetime import timedelta, timezone

    sgt = timezone(timedelta(hours=8))
    data = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Cloudy",
                valid_start=datetime.fromisoformat("2026-04-05T08:00:00+08:00"),
                valid_end=datetime.fromisoformat("2026-04-05T10:00:00+08:00"),
            )
        },
        updated_at=None,
        readings=WeatherReadings(temperature=29.5),
        four_day_forecast=[
            FourDayForecastEntry(
                date=datetime(2026, 4, 5, 0, 0, 0, tzinfo=sgt),
                condition_text="Partly Cloudy",
                temp_high=33.0,
                temp_low=24.0,
            )
        ],
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")
    assert ent.native_temperature == 29.5  # readings takes priority


def test_weather_entity_native_temperature_fallback_to_forecast():
    from datetime import timedelta, timezone

    sgt = timezone(timedelta(hours=8))
    data = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Cloudy",
                valid_start=datetime.fromisoformat("2026-04-05T08:00:00+08:00"),
                valid_end=datetime.fromisoformat("2026-04-05T10:00:00+08:00"),
            )
        },
        updated_at=None,
        readings=WeatherReadings(temperature=None),
        four_day_forecast=[
            FourDayForecastEntry(
                date=datetime(2026, 4, 5, 0, 0, 0, tzinfo=sgt),
                condition_text="Partly Cloudy",
                temp_high=33.0,
                temp_low=24.0,
            )
        ],
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")
    assert ent.native_temperature == 33.0


# ---------------------------------------------------------------------------
# async_forecast_daily tests
# ---------------------------------------------------------------------------


async def test_weather_daily_forecast():
    from datetime import timedelta, timezone

    sgt = timezone(timedelta(hours=8))
    data = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Cloudy",
                valid_start=datetime.fromisoformat("2026-04-05T08:00:00+08:00"),
                valid_end=datetime.fromisoformat("2026-04-05T10:00:00+08:00"),
            )
        },
        updated_at=None,
        readings=WeatherReadings(),
        four_day_forecast=[
            FourDayForecastEntry(
                date=datetime(2026, 4, 5, 0, 0, 0, tzinfo=sgt),
                condition_text="Partly Cloudy",
                temp_high=33.0,
                temp_low=24.0,
                humidity_high=90.0,
                humidity_low=60.0,
                wind_speed_low=10.0,
                wind_speed_high=20.0,
                wind_direction="S",
            ),
            FourDayForecastEntry(
                date=datetime(2026, 4, 6, 0, 0, 0, tzinfo=sgt),
                condition_text="Thundery Showers",
                temp_high=31.0,
                temp_low=25.0,
                humidity_high=95.0,
                humidity_low=70.0,
                wind_speed_low=15.0,
                wind_speed_high=25.0,
                wind_direction="NE",
            ),
        ],
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")
    daily = await ent.async_forecast_daily()

    assert len(daily) == 2
    d0 = daily[0]
    assert d0["condition"] == "partlycloudy"
    assert d0["native_temperature"] == 33.0
    assert d0["native_templow"] == 24.0
    assert d0["humidity"] == 75.0  # midpoint of 60..90
    assert d0["native_wind_speed"] == 15.0  # midpoint of 10..20
    assert d0["wind_bearing"] == 180.0  # S
    assert d0["datetime"] == "2026-04-05T00:00:00+08:00"

    d1 = daily[1]
    assert d1["condition"] == "lightning-rainy"
    assert d1["wind_bearing"] == 45.0  # NE


async def test_weather_daily_forecast_none_when_no_data():
    data = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Cloudy",
                valid_start=datetime.fromisoformat("2026-04-05T08:00:00+08:00"),
                valid_end=datetime.fromisoformat("2026-04-05T10:00:00+08:00"),
            )
        },
        updated_at=None,
        readings=WeatherReadings(),
        four_day_forecast=None,
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")
    assert await ent.async_forecast_daily() is None


def test_weather_entity_supports_daily_feature_only():
    from custom_components.singapore.weather import WeatherEntityFeature

    data = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Cloudy",
                valid_start=datetime.fromisoformat("2026-04-05T08:00:00+08:00"),
                valid_end=datetime.fromisoformat("2026-04-05T10:00:00+08:00"),
            )
        },
        updated_at=None,
        readings=WeatherReadings(),
    )
    ent = SingaporeAreaWeatherEntity(_coordinator(data), "entry1", "Bedok")
    assert ent._attr_supported_features & WeatherEntityFeature.FORECAST_DAILY
    assert not (ent._attr_supported_features & WeatherEntityFeature.FORECAST_HOURLY)
