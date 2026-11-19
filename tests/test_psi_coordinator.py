"""Tests for the PSI parser and coordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.singapore.psi_coordinator import (
    DEFAULT_REGION_LOCATIONS,
    PsiCoordinator,
    PsiData,
    _parse_psi,
    pm25_band,
    psi_band,
)

# Sample from the data.gov.sg v2 PSI API documentation.
SAMPLE_V2 = {
    "code": 1,
    "errorMsg": None,
    "data": {
        "regionMetadata": [
            {"name": "West", "labelLocation": {"latitude": 1.35, "longitude": 103.7}}
        ],
        "items": [
            {
                "date": "2024-07-17T00:00:00.000Z",
                "updatedTimestamp": "2024-07-17T14:46:03.000Z",
                "timestamp": "2024-07-17T14:00:00.000Z",
                "readings": {
                    "co_sub_index": {
                        "east": 12,
                        "west": 15,
                        "north": 21,
                        "south": 20,
                        "central": 39,
                    },
                    "so2_twenty_four_hourly": {
                        "east": 5,
                        "west": 29,
                        "north": 19,
                        "south": 1,
                        "central": 39,
                    },
                    "psi_three_hourly": {
                        "east": 0,
                        "west": 12,
                        "north": 29,
                        "south": 3,
                        "central": 39,
                    },
                    "co_eight_hour_max": {
                        "east": 19,
                        "west": 2,
                        "north": 5,
                        "south": 9,
                        "central": 0,
                    },
                    "psi_twenty_four_hourly": {
                        "east": 39,
                        "west": 12,
                        "north": 3,
                        "south": 11,
                        "central": 10,
                    },
                    "pm25_twenty_four_hourly": {
                        "east": 0,
                        "west": 3,
                        "north": 3,
                        "south": 1,
                        "central": 12,
                    },
                },
            }
        ],
        "paginationToken": "b2Zmc2V0PTEwMA==",
    },
}


def test_parse_v2_regional_readings():
    data = _parse_psi(SAMPLE_V2)
    assert data.readings["psi_twenty_four_hourly"] == {
        "north": 3.0,
        "south": 11.0,
        "east": 39.0,
        "west": 12.0,
        "central": 10.0,
    }
    # Every reading key is kept, including sub-indices and 3-hourly PSI.
    assert data.readings["co_sub_index"]["central"] == 39.0
    assert data.readings["psi_three_hourly"]["north"] == 29.0


def test_parse_v2_national_is_highest_region():
    data = _parse_psi(SAMPLE_V2)
    assert data.national["psi_twenty_four_hourly"] == 39.0
    assert data.national["pm25_twenty_four_hourly"] == 12.0


def test_parse_timestamp():
    data = _parse_psi(SAMPLE_V2)
    assert data.timestamp is not None
    assert data.timestamp.isoformat() == "2024-07-17T14:00:00+00:00"


def test_parse_region_locations_from_metadata_with_defaults():
    data = _parse_psi(SAMPLE_V2)
    # "West" (capitalised) in metadata overrides the default.
    assert data.region_locations["west"] == (1.35, 103.7)
    # Regions missing from metadata fall back to NEA's known label locations.
    assert data.region_locations["east"] == DEFAULT_REGION_LOCATIONS["east"]
    assert set(data.region_locations) == {"north", "south", "east", "west", "central"}


def test_parse_v1_uses_national_value():
    payload = {
        "region_metadata": [
            {"name": "national", "label_location": {"latitude": 0, "longitude": 0}}
        ],
        "items": [
            {
                "timestamp": "2024-07-17T22:00:00+08:00",
                "readings": {
                    "psi_twenty_four_hourly": {
                        "national": 55,
                        "north": 50,
                        "south": 52,
                        "east": 40,
                        "west": 45,
                        "central": 48,
                    }
                },
            }
        ],
    }
    data = _parse_psi(payload)
    assert data.national["psi_twenty_four_hourly"] == 55.0
    assert "national" not in data.readings["psi_twenty_four_hourly"]
    assert "national" not in data.region_locations


@pytest.mark.parametrize("payload", [{}, {"data": {"items": []}}, None, []])
def test_parse_empty_payload(payload):
    data = _parse_psi(payload)
    assert data.readings == {}
    assert data.national == {}


def test_parse_skips_non_numeric_values():
    payload = {
        "data": {
            "items": [
                {"readings": {"psi_twenty_four_hourly": {"north": "n/a", "east": 30}}}
            ]
        }
    }
    data = _parse_psi(payload)
    assert data.readings["psi_twenty_four_hourly"] == {"east": 30.0}


@pytest.mark.parametrize(
    ("value", "band"),
    [
        (None, None),
        (0, "good"),
        (50, "good"),
        (51, "moderate"),
        (100, "moderate"),
        (101, "unhealthy"),
        (200, "unhealthy"),
        (201, "very_unhealthy"),
        (300, "very_unhealthy"),
        (301, "hazardous"),
    ],
)
def test_psi_band(value, band):
    assert psi_band(value) == band


def _session(status=200, payload=None, exc=None):
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    if exc is not None:
        session.get = MagicMock(side_effect=exc)
    else:
        session.get = MagicMock(return_value=response)
    return session


async def _refresh(coordinator, session):
    with patch(
        "custom_components.singapore.psi_coordinator.async_get_clientsession",
        return_value=session,
    ):
        await coordinator.async_refresh()


@pytest.mark.asyncio
async def test_coordinator_success():
    coordinator = PsiCoordinator(MagicMock())
    await _refresh(coordinator, _session(payload=SAMPLE_V2))
    assert coordinator.last_update_success is True
    assert coordinator.data.national["psi_twenty_four_hourly"] == 39.0


@pytest.mark.asyncio
async def test_coordinator_http_error_without_cache_fails():
    coordinator = PsiCoordinator(MagicMock())
    await _refresh(coordinator, _session(status=503))
    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_network_error_uses_last_known_data():
    coordinator = PsiCoordinator(MagicMock())
    coordinator.data = PsiData(national={"psi_twenty_four_hourly": 42.0})
    await _refresh(coordinator, _session(exc=aiohttp.ClientError("boom")))
    assert coordinator.last_update_success is True
    assert coordinator.data.national["psi_twenty_four_hourly"] == 42.0


@pytest.mark.asyncio
async def test_coordinator_unparseable_payload_fails_even_with_cache():
    """A payload with no PSI must surface as a failure, not stale success."""
    coordinator = PsiCoordinator(MagicMock())
    coordinator.data = PsiData(national={"psi_twenty_four_hourly": 42.0})
    await _refresh(coordinator, _session(payload={"data": {"items": []}}))
    assert coordinator.last_update_success is False


# -- hourly PM2.5 (NEA's 1-hour air quality figure) ---------------------------

# Trimmed from the live v2 pm25 payload on 2026-11-19.
SAMPLE_PM25 = {
    "code": 0,
    "data": {
        "regionMetadata": [],
        "items": [
            {
                "date": "2026-11-19",
                "timestamp": "2026-11-19T18:00:00+08:00",
                "readings": {
                    "pm25_one_hourly": {
                        "north": 7,
                        "south": 4,
                        "west": 4,
                        "east": 61,
                        "central": 2,
                    }
                },
            }
        ],
    },
}


def _routed_session(pm25):
    """PSI from SAMPLE_V2; the PM2.5 URL answers ``pm25`` (a response or error)."""
    from custom_components.singapore.psi_coordinator import PM25_URL

    def _response(status=200, payload=None):
        response = AsyncMock()
        response.headers = {}
        response.status = status
        response.json = AsyncMock(return_value=payload)
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        return response

    def _get(url, **_kwargs):
        if url != PM25_URL:
            return _response(payload=SAMPLE_V2)
        if isinstance(pm25, Exception):
            raise pm25
        if isinstance(pm25, int):
            return _response(status=pm25)
        return _response(payload=pm25)

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    return session


@pytest.mark.asyncio
async def test_coordinator_merges_hourly_pm25():
    coordinator = PsiCoordinator(MagicMock())
    await _refresh(coordinator, _routed_session(SAMPLE_PM25))
    data = coordinator.data
    assert data.readings["pm25_one_hourly"]["east"] == 61.0
    assert data.national["pm25_one_hourly"] == 61.0
    # The 24-hour readings are untouched.
    assert data.national["psi_twenty_four_hourly"] == 39.0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [503, aiohttp.ClientError("down"), {"data": {}}])
async def test_hourly_pm25_failure_keeps_previous_readings(failure):
    coordinator = PsiCoordinator(MagicMock())
    await _refresh(coordinator, _routed_session(SAMPLE_PM25))

    await _refresh(coordinator, _routed_session(failure))
    assert coordinator.last_update_success is True
    assert coordinator.data.national["pm25_one_hourly"] == 61.0


@pytest.mark.asyncio
async def test_hourly_pm25_failure_without_history_leaves_it_out():
    coordinator = PsiCoordinator(MagicMock())
    await _refresh(coordinator, _routed_session(503))
    assert coordinator.last_update_success is True
    assert "pm25_one_hourly" not in coordinator.data.national


@pytest.mark.parametrize(
    ("value", "band"),
    [
        (0, "normal"),
        (55, "normal"),
        (56, "elevated"),
        (150, "elevated"),
        (151, "high"),
        (250, "high"),
        (251, "very_high"),
        (None, None),
    ],
)
def test_pm25_band(value, band):
    assert pm25_band(value) == band


def test_hourly_pm25_sensor_reports_band():
    from custom_components.singapore.sensor import (
        PSI_POLLUTANT_SENSORS,
        SingaporePollutantSensor,
    )

    spec = next(s for s in PSI_POLLUTANT_SENSORS if s[1] == "pm25_one_hour")
    coordinator = MagicMock()
    coordinator.data = PsiData(
        readings={"pm25_one_hourly": {"east": 61.0, "west": 4.0}},
        national={"pm25_one_hourly": 61.0},
    )
    sensor = SingaporePollutantSensor(coordinator, "entry1", *spec)
    assert sensor.unique_id == "entry1_pm25_one_hour"
    assert sensor.native_value == 61.0
    attrs = sensor.extra_state_attributes
    assert attrs["band"] == "elevated"
    assert attrs["regions"] == {"east": 61.0, "west": 4.0}
