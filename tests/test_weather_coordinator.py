"""Tests for weather payload parsing and coordinator output models."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.weather_coordinator import (
    _extract_readings_rows,
    _parse_four_day,
    _parse_weather,
    _to_float,
    _wind_direction_to_degrees,
)


def test_parse_v1_shape_area_forecasts():
    payload = {
        "items": [
            {
                "timestamp": "2026-04-05T08:00:00+08:00",
                "valid_period": {
                    "start": "2026-04-05T08:00:00+08:00",
                    "end": "2026-04-05T10:00:00+08:00",
                },
                "forecasts": [
                    {"area": "Bedok", "forecast": "Partly Cloudy (Day)"},
                    {"area": "Woodlands", "forecast": "Thundery Showers"},
                ],
            }
        ]
    }

    data = _parse_weather(payload)

    assert set(data.areas) == {"Bedok", "Woodlands"}
    assert data.areas["Bedok"].condition_text == "Partly Cloudy (Day)"
    assert data.areas["Bedok"].valid_start.astimezone(timezone.utc).hour == 0


def test_parse_v2_regions_shape():
    payload = {
        "data": {
            "records": [
                {
                    "periods": [
                        {
                            "timePeriod": {
                                "start": "2026-04-05T02:00:00Z",
                                "end": "2026-04-05T04:00:00Z",
                            },
                            "regions": {
                                "north": "Cloudy",
                                "south": "Showers",
                            },
                        }
                    ]
                }
            ]
        }
    }

    data = _parse_weather(payload)

    assert set(data.areas) == {"North", "South"}
    assert data.areas["South"].condition_text == "Showers"


def test_extract_reading_rows_from_items_shape():
    payload = {"items": [{"readings": [{"value": 1.2}, {"value": 2.3}]}]}
    rows = _extract_readings_rows(payload)
    assert len(rows) == 2


def test_extract_reading_rows_from_data_shape():
    payload = {"data": {"readings": [{"value": 55}]}}
    rows = _extract_readings_rows(payload)
    assert rows[0]["value"] == 55


def test_to_float_handles_invalid_values():
    assert _to_float("2.5") == 2.5
    assert _to_float(None) is None
    assert _to_float("bad") is None


@pytest.mark.asyncio
async def test_weather_coordinator_http_error_without_cache_fails():
    from custom_components.singapore.weather_coordinator import (
        SingaporeWeatherCoordinator,
    )

    hass = MagicMock()

    mock_response = MagicMock()
    mock_response.status_code = 503

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    coordinator = SingaporeWeatherCoordinator(hass)

    with patch(
        "custom_components.singapore.weather_coordinator.niquests.AsyncSession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_weather_coordinator_http_error_uses_last_known_data():
    from custom_components.singapore.weather_coordinator import (
        SingaporeWeatherCoordinator,
        WeatherAreaData,
        WeatherData,
        WeatherReadings,
    )

    hass = MagicMock()

    mock_response = MagicMock()
    mock_response.status_code = 503

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    coordinator = SingaporeWeatherCoordinator(hass)
    cached = WeatherData(
        areas={
            "Bedok": WeatherAreaData(
                area="Bedok",
                condition_text="Cloudy",
                valid_start=datetime(2026, 4, 5, 0, 0, tzinfo=timezone.utc),
                valid_end=datetime(2026, 4, 5, 2, 0, tzinfo=timezone.utc),
            )
        },
        updated_at=None,
        readings=WeatherReadings(temperature=30.1),
    )
    coordinator.data = cached

    with patch(
        "custom_components.singapore.weather_coordinator.niquests.AsyncSession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.readings.temperature == 30.1


# ---------------------------------------------------------------------------
# _parse_four_day tests
# ---------------------------------------------------------------------------


def test_parse_four_day_shape_a():
    payload = {
        "items": [
            {
                "forecasts": [
                    {
                        "date": "2026-04-05",
                        "forecast": "Partly Cloudy",
                        "temperature": {"low": 24, "high": 33},
                        "relative_humidity": {"low": 60, "high": 90},
                        "wind": {"speed": {"low": 10, "high": 20}, "direction": "S"},
                    },
                    {
                        "date": "2026-04-06",
                        "forecast": "Thundery Showers",
                        "temperature": {"low": 25, "high": 32},
                        "relative_humidity": {"low": 65, "high": 95},
                        "wind": {"speed": {"low": 15, "high": 25}, "direction": "NE"},
                    },
                ]
            }
        ]
    }
    entries = _parse_four_day(payload)
    assert len(entries) == 2
    e0 = entries[0]
    assert e0.condition_text == "Partly Cloudy"
    assert e0.temp_high == 33
    assert e0.temp_low == 24
    assert e0.humidity_high == 90
    assert e0.humidity_low == 60
    assert e0.wind_speed_low == 10
    assert e0.wind_speed_high == 20
    assert e0.wind_direction == "S"
    from datetime import timedelta, timezone

    sgt = timezone(timedelta(hours=8))
    assert e0.date.tzinfo == sgt
    assert e0.date.hour == 0
    assert e0.date.day == 5


def test_parse_four_day_shape_b_camel_case():
    payload = {
        "data": {
            "records": [
                {
                    "date": "2026-04-05",
                    "forecast": "Cloudy",
                    "temperature": {"low": 23, "high": 30},
                    "relativeHumidity": {"low": 70, "high": 95},
                    "wind": {"speed": {"low": 5, "high": 15}, "direction": "W"},
                }
            ]
        }
    }
    entries = _parse_four_day(payload)
    assert len(entries) == 1
    assert entries[0].humidity_high == 95
    assert entries[0].wind_direction == "W"


def test_parse_four_day_shape_b_records_with_nested_forecasts():
    payload = {
        "data": {
            "records": [
                {
                    "date": "2026-04-06",
                    "forecasts": [
                        {
                            "day": "Tuesday",
                            "timestamp": "2026-04-07T00:00:00+08:00",
                            "forecast": {
                                "summary": "Afternoon thundery showers",
                                "text": "Thundery Showers",
                            },
                            "temperature": {"low": 24, "high": 34},
                            "relativeHumidity": {"low": 60, "high": 95},
                            "wind": {
                                "speed": {"low": 5, "high": 15},
                                "direction": "VARIABLE",
                            },
                        }
                    ],
                }
            ]
        }
    }
    entries = _parse_four_day(payload)
    assert len(entries) == 1
    assert entries[0].condition_text == "Thundery Showers"
    assert entries[0].date.day == 7
    assert entries[0].temp_low == 24
    assert entries[0].temp_high == 34
    assert entries[0].wind_direction == "VARIABLE"


def test_parse_four_day_skips_invalid_date():
    payload = {
        "data": {
            "records": [
                {"date": "not-a-date", "forecast": "Cloudy", "temperature": {}},
                {
                    "date": "2026-04-06",
                    "forecast": "Rainy",
                    "temperature": {"low": 24, "high": 31},
                },
            ]
        }
    }
    entries = _parse_four_day(payload)
    assert len(entries) == 1
    assert entries[0].condition_text == "Rainy"


def test_parse_four_day_empty_payload():
    assert _parse_four_day({}) == []
    assert _parse_four_day({"items": []}) == []
    assert _parse_four_day({"data": {"records": []}}) == []


def test_parse_four_day_missing_fields_are_none():
    payload = {
        "data": {
            "records": [
                {"date": "2026-04-05", "forecast": "Fair", "temperature": {}},
            ]
        }
    }
    entries = _parse_four_day(payload)
    assert len(entries) == 1
    assert entries[0].temp_high is None
    assert entries[0].temp_low is None
    assert entries[0].humidity_high is None
    assert entries[0].wind_direction is None


# ---------------------------------------------------------------------------
# _wind_direction_to_degrees tests
# ---------------------------------------------------------------------------


def test_wind_direction_to_degrees():
    assert _wind_direction_to_degrees("N") == 0.0
    assert _wind_direction_to_degrees("S") == 180.0
    assert _wind_direction_to_degrees("NE") == 45.0
    assert _wind_direction_to_degrees("NNE") == 22.5
    assert _wind_direction_to_degrees("SSW") == 202.5
    assert _wind_direction_to_degrees("CALM") == 0.0
    assert _wind_direction_to_degrees("s") == 180.0  # case-insensitive
    assert _wind_direction_to_degrees("") is None
    assert _wind_direction_to_degrees(None) is None
    assert _wind_direction_to_degrees("BOGUS") is None


# ---------------------------------------------------------------------------
# Coordinator integration tests for four-day fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weather_coordinator_fetches_four_day():
    from custom_components.singapore.weather_coordinator import (
        SingaporeWeatherCoordinator,
    )

    hass = MagicMock()

    two_hr_payload = {
        "items": [
            {
                "timestamp": "2026-04-05T08:00:00+08:00",
                "valid_period": {
                    "start": "2026-04-05T08:00:00+08:00",
                    "end": "2026-04-05T10:00:00+08:00",
                },
                "forecasts": [{"area": "Bedok", "forecast": "Cloudy"}],
            }
        ]
    }
    four_day_payload = {
        "items": [
            {
                "forecasts": [
                    {
                        "date": "2026-04-05",
                        "forecast": "Partly Cloudy",
                        "temperature": {"low": 24, "high": 33},
                        "relative_humidity": {"low": 60, "high": 90},
                        "wind": {"speed": {"low": 10, "high": 20}, "direction": "S"},
                    }
                ]
            }
        ]
    }

    two_hr_resp = MagicMock()
    two_hr_resp.status_code = 200
    two_hr_resp.json = MagicMock(return_value=two_hr_payload)

    four_day_resp = MagicMock()
    four_day_resp.status_code = 200
    four_day_resp.json = MagicMock(return_value=four_day_payload)

    async def _mock_get(url, timeout=20):
        if "four-day" in url:
            return four_day_resp
        if "two-hr" in url:
            return two_hr_resp
        r = MagicMock()
        r.status_code = 404
        return r

    mock_session = AsyncMock()
    mock_session.get = _mock_get
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    coordinator = SingaporeWeatherCoordinator(hass)

    with patch(
        "custom_components.singapore.weather_coordinator.niquests.AsyncSession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.four_day_forecast is not None
    assert len(coordinator.data.four_day_forecast) == 1
    assert coordinator.data.four_day_forecast[0].temp_high == 33


@pytest.mark.asyncio
async def test_weather_coordinator_four_day_http_error_is_soft():
    from custom_components.singapore.weather_coordinator import (
        SingaporeWeatherCoordinator,
    )

    hass = MagicMock()

    two_hr_payload = {
        "items": [
            {
                "timestamp": "2026-04-05T08:00:00+08:00",
                "valid_period": {
                    "start": "2026-04-05T08:00:00+08:00",
                    "end": "2026-04-05T10:00:00+08:00",
                },
                "forecasts": [{"area": "Bedok", "forecast": "Cloudy"}],
            }
        ]
    }

    two_hr_resp = MagicMock()
    two_hr_resp.status_code = 200
    two_hr_resp.json = MagicMock(return_value=two_hr_payload)

    four_day_resp = MagicMock()
    four_day_resp.status_code = 503

    async def _mock_get(url, timeout=20):
        if "four-day" in url:
            return four_day_resp
        if "two-hr" in url:
            return two_hr_resp
        r = MagicMock()
        r.status_code = 404
        return r

    mock_session = AsyncMock()
    mock_session.get = _mock_get
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    coordinator = SingaporeWeatherCoordinator(hass)
    with patch(
        "custom_components.singapore.weather_coordinator.niquests.AsyncSession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.four_day_forecast is None
