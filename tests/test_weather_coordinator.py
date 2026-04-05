"""Tests for weather payload parsing and coordinator output models."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.weather_coordinator import (
    _extract_readings_rows,
    _parse_weather,
    _to_float,
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

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = SingaporeWeatherCoordinator(hass)

    with patch(
        "custom_components.singapore.weather_coordinator.async_get_clientsession",
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

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

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
        "custom_components.singapore.weather_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.readings.temperature == 30.1
