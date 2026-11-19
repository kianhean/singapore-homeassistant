"""Tests for weather payload parsing and coordinator output models."""

from datetime import datetime, timedelta, timezone
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
    mock_response.status = 503

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_response)

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

    mock_response = MagicMock()
    mock_response.status = 503

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_response)

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

    class _MockResponse:
        def __init__(self, status=200, json_data=None):
            self.status = status
            self._json_data = json_data

        async def json(self):
            return self._json_data

        def release(self):
            pass

    two_hr_resp = _MockResponse(200, two_hr_payload)
    four_day_resp = _MockResponse(200, four_day_payload)

    async def _mock_get(url, timeout=None, headers=None):
        if "four-day" in url:
            return four_day_resp
        if "two-hr" in url:
            return two_hr_resp
        return _MockResponse(404)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    coordinator = SingaporeWeatherCoordinator(hass)

    with patch(
        "custom_components.singapore.weather_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.four_day_forecast is not None
    assert len(coordinator.data.four_day_forecast) == 1
    assert coordinator.data.four_day_forecast[0].temp_high == 33


@pytest.mark.asyncio
async def test_weather_coordinator_releases_both_responses_on_two_hr_error():
    """Regression test: every response must be released even if two_hr fails.

    The original code only released four_day_resp on the two_hr-status
    error path, leaking the two_hr response's connection.
    """
    from custom_components.singapore.weather_coordinator import (
        SingaporeWeatherCoordinator,
    )

    hass = MagicMock()

    class _MockResponse:
        def __init__(self, status=200, json_data=None):
            self.status = status
            self._json_data = json_data
            self.release = MagicMock()

        async def json(self):
            return self._json_data

    two_hr_resp = _MockResponse(503)
    four_day_resp = _MockResponse(200, {})
    day_resp = _MockResponse(200, {})

    async def _mock_get(url, timeout=None, headers=None):
        if "four-day" in url:
            return four_day_resp
        if "twenty-four" in url:
            return day_resp
        return two_hr_resp

    mock_session = MagicMock()
    mock_session.get = _mock_get

    coordinator = SingaporeWeatherCoordinator(hass)

    with patch(
        "custom_components.singapore.weather_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    two_hr_resp.release.assert_called_once()
    four_day_resp.release.assert_called_once()
    day_resp.release.assert_called_once()


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

    class _MockResponse:
        def __init__(self, status=200, json_data=None):
            self.status = status
            self._json_data = json_data

        async def json(self):
            return self._json_data

        def release(self):
            pass

    two_hr_resp = _MockResponse(200, two_hr_payload)
    four_day_resp = _MockResponse(503)

    async def _mock_get(url, timeout=None, headers=None):
        if "four-day" in url:
            return four_day_resp
        if "two-hr" in url:
            return two_hr_resp
        return _MockResponse(404)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    coordinator = SingaporeWeatherCoordinator(hass)
    with patch(
        "custom_components.singapore.weather_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.four_day_forecast is None


# ---------------------------------------------------------------------------
# 24-hour regional forecast + area region tests
# ---------------------------------------------------------------------------


def test_parse_twenty_four_hour_v2_shape():
    from custom_components.singapore.weather_coordinator import (
        _parse_twenty_four_hour,
    )

    payload = {
        "code": 0,
        "data": {
            "records": [
                {
                    "date": "2026-11-13",
                    "periods": [
                        {
                            "regions": {
                                "east": {"code": "CL", "text": "Cloudy"},
                                "west": {"code": "TL", "text": "Thundery Showers"},
                            },
                            "timePeriod": {
                                "text": "6 pm 13 Nov to 6 am 14 Nov",
                                "start": "2026-11-13T18:00:00+08:00",
                                "end": "2026-11-14T06:00:00+08:00",
                            },
                        },
                        {"regions": {"east": {"text": "Cloudy"}}, "timePeriod": {}},
                    ],
                }
            ]
        },
    }

    periods = _parse_twenty_four_hour(payload)

    assert len(periods) == 1  # second period has no times
    assert periods[0].start == datetime.fromisoformat("2026-11-13T18:00:00+08:00")
    assert periods[0].regions == {"east": "Cloudy", "west": "Thundery Showers"}


def test_parse_twenty_four_hour_v1_shape():
    from custom_components.singapore.weather_coordinator import (
        _parse_twenty_four_hour,
    )

    payload = {
        "items": [
            {
                "periods": [
                    {
                        "time": {
                            "start": "2026-11-14T06:00:00+08:00",
                            "end": "2026-11-14T12:00:00+08:00",
                        },
                        "regions": {"North": "Partly Cloudy (Day)"},
                    }
                ]
            }
        ]
    }

    periods = _parse_twenty_four_hour(payload)

    assert periods[0].regions == {"north": "Partly Cloudy (Day)"}


def test_parse_twenty_four_hour_empty_payload():
    from custom_components.singapore.weather_coordinator import (
        _parse_twenty_four_hour,
    )

    assert _parse_twenty_four_hour({}) == []


@pytest.mark.parametrize(
    ("area", "lat", "lon", "region"),
    [
        ("Bedok", 1.321, 103.924, "east"),
        ("Changi", 1.357, 103.987, "east"),
        ("Woodlands", 1.432, 103.786, "north"),
        ("Jurong West", 1.34, 103.705, "west"),
        ("City", 1.292, 103.844, "south"),
        ("Toa Payoh", 1.343, 103.856, "central"),
    ],
)
def test_parse_weather_assigns_nearest_region(area, lat, lon, region):
    from custom_components.singapore.weather_coordinator import _parse_weather

    payload = {
        "code": 0,
        "data": {
            "area_metadata": [
                {"name": area, "label_location": {"latitude": lat, "longitude": lon}}
            ],
            "items": [
                {
                    "timestamp": "2026-11-13T17:00:00+08:00",
                    "valid_period": {
                        "start": "2026-11-13T17:00:00+08:00",
                        "end": "2026-11-13T19:00:00+08:00",
                    },
                    "forecasts": [{"area": area, "forecast": "Cloudy"}],
                }
            ],
        },
    }

    assert _parse_weather(payload).areas[area].region == region


def test_parse_weather_region_none_without_metadata():
    from custom_components.singapore.weather_coordinator import _parse_weather

    payload = {
        "items": [
            {
                "valid_period": {
                    "start": "2026-11-13T17:00:00+08:00",
                    "end": "2026-11-13T19:00:00+08:00",
                },
                "forecasts": [{"area": "Bedok", "forecast": "Cloudy"}],
            }
        ]
    }

    assert _parse_weather(payload).areas["Bedok"].region is None


@pytest.mark.asyncio
@pytest.mark.parametrize("day_status", [200, 503])
async def test_weather_coordinator_twenty_four_hour(day_status):
    from custom_components.singapore.weather_coordinator import (
        SingaporeWeatherCoordinator,
    )

    two_hr_payload = {
        "items": [
            {
                "valid_period": {
                    "start": "2026-11-13T17:00:00+08:00",
                    "end": "2026-11-13T19:00:00+08:00",
                },
                "forecasts": [{"area": "Bedok", "forecast": "Cloudy"}],
            }
        ]
    }
    day_payload = {
        "data": {
            "records": [
                {
                    "periods": [
                        {
                            "timePeriod": {
                                "start": "2026-11-13T18:00:00+08:00",
                                "end": "2026-11-14T06:00:00+08:00",
                            },
                            "regions": {"east": {"text": "Cloudy"}},
                        }
                    ]
                }
            ]
        }
    }

    class _MockResponse:
        def __init__(self, status=200, json_data=None):
            self.status = status
            self._json_data = json_data

        async def json(self):
            return self._json_data

        def release(self):
            pass

    async def _mock_get(url, timeout=None, headers=None):
        if "twenty-four" in url:
            return _MockResponse(day_status, day_payload)
        if "two-hr" in url:
            return _MockResponse(200, two_hr_payload)
        return _MockResponse(404)

    mock_session = MagicMock()
    mock_session.get = _mock_get
    coordinator = SingaporeWeatherCoordinator(MagicMock())

    with patch(
        "custom_components.singapore.weather_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    # A 24-hour forecast outage must not fail the whole weather update.
    assert coordinator.last_update_success is True
    if day_status == 200:
        assert coordinator.data.regional_forecast[0].regions == {"east": "Cloudy"}
    else:
        assert coordinator.data.regional_forecast is None


# -- helper edge cases -------------------------------------------------------


def _response(status: int = 200, payload: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=payload or {})
    response.text = AsyncMock(return_value="")
    response.headers = {}
    return response


async def test_fetch_with_retry_retries_429_then_returns_200():
    import asyncio  # noqa: F401  (keeps the fake clock fixture in charge)

    from custom_components.singapore.weather_coordinator import _fetch_with_retry

    session = MagicMock()
    busy, ok = _response(429), _response(200)
    session.get = AsyncMock(side_effect=[busy, ok])
    assert await _fetch_with_retry(session, "https://example.com/x") is ok
    busy.release.assert_called_once()
    assert session.get.await_count == 2


async def test_fetch_with_retry_gives_up_after_max_retries():
    from custom_components.singapore.datagov import MAX_RETRIES
    from custom_components.singapore.weather_coordinator import _fetch_with_retry

    session = MagicMock()
    responses = [_response(429) for _ in range(MAX_RETRIES + 1)]
    session.get = AsyncMock(side_effect=responses)
    result = await _fetch_with_retry(session, "https://example.com/x")
    assert result is responses[-1]
    assert result.status == 429


async def test_reading_average_skips_errors_and_averages_values():
    import asyncio

    from custom_components.singapore.weather_coordinator import (
        _fetch_reading_average,
    )

    sem = asyncio.Semaphore(1)
    session = MagicMock()
    payload = {"items": [{"readings": [{"value": 30}, {"value": "x"}, {"value": 31}]}]}
    session.get = AsyncMock(return_value=_response(200, payload))
    assert await _fetch_reading_average(session, sem, "u", "temperature") == 30.5

    session.get = AsyncMock(return_value=_response(500))
    assert await _fetch_reading_average(session, sem, "u", "temperature") is None

    session.get = AsyncMock(side_effect=OSError("boom"))
    assert await _fetch_reading_average(session, sem, "u", "temperature") is None

    bad_json = _response(200)
    bad_json.json = AsyncMock(side_effect=ValueError("not json"))
    session.get = AsyncMock(return_value=bad_json)
    assert await _fetch_reading_average(session, sem, "u", "temperature") is None
    bad_json.release.assert_called_once()

    session.get = AsyncMock(return_value=_response(200, {"items": []}))
    assert await _fetch_reading_average(session, sem, "u", "temperature") is None


async def test_aggregated_readings_treat_exceptions_as_none():
    from custom_components.singapore import weather_coordinator as wc

    async def _avg(_session, _sem, _url, key, *_args):
        if key == "humidity":
            raise RuntimeError("unexpected")
        return 1.0

    with patch.object(wc, "_fetch_reading_average", _avg):
        readings = await wc._fetch_aggregated_readings(MagicMock(), MagicMock())
    assert readings.humidity is None
    assert readings.temperature == 1.0


def test_parsers_tolerate_junk():
    from custom_components.singapore.weather_coordinator import (
        _area_regions,
        _parse_iso_datetime,
        _parse_twenty_four_hour,
        _to_int,
    )

    assert _to_int("7") == 7
    assert _to_int(None) is None
    assert _to_int("x") is None
    assert _parse_iso_datetime(None) is None
    assert _parse_iso_datetime("not a date") is None
    naive = _parse_iso_datetime("2026-04-05T08:00:00")
    assert naive.tzinfo is timezone.utc
    assert _parse_iso_datetime("2026-04-05T00:00:00Z").tzinfo is not None

    assert (
        _area_regions({"area_metadata": ["junk", {"name": "", "label_location": {}}]})
        == {}
    )
    assert _parse_twenty_four_hour({"data": {"records": ["junk"]}}) == []
    assert (
        _parse_twenty_four_hour(
            {"data": {"records": [{"periods": ["junk", {"timePeriod": {}}]}]}}
        )
        == []
    )


def test_parse_four_day_skips_junk_rows():
    payload = {
        "data": {
            "records": [
                "junk",
                {"date": "2026-04-06", "forecast": {"text": ""}},
                {
                    "date": "2026-04-07",
                    "forecast": "Thundery Showers",
                    "temperature": {"low": 25, "high": 33},
                },
            ]
        }
    }
    days = _parse_four_day(payload)
    assert [d.condition_text for d in days] == ["Thundery Showers"]


def test_parse_weather_without_valid_period_uses_timestamp_or_now():
    with_timestamp = _parse_weather(
        {
            "items": [
                {
                    "timestamp": "2026-04-05T08:00:00+08:00",
                    "forecasts": [{"area": "Bedok", "forecast": "Fair"}],
                }
            ]
        }
    )
    area = with_timestamp.areas["Bedok"]
    assert area.valid_end - area.valid_start == timedelta(hours=2)

    no_times = _parse_weather(
        {"items": [{"forecasts": [{"area": "Bedok", "forecast": "Fair"}]}]}
    )
    assert no_times.areas["Bedok"].valid_start is not None
