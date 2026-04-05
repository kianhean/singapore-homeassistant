"""Tests for weather payload parsing and coordinator output models."""

from datetime import timezone

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
