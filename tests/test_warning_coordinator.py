"""Tests for the NEA weather warnings and regional advisories coordinator and entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.binary_sensor import (
    SingaporeHeavyRainWarningBinarySensor,
)
from custom_components.singapore.sensor import (
    SingaporeLatestEarthquakeSensor,
    SingaporeWeatherAdvisoriesSensor,
)
from custom_components.singapore.warning_coordinator import (
    WeatherWarningsCoordinator,
    _parse_warnings,
)

_PATCH_SESSION = (
    "custom_components.singapore.warning_coordinator.async_get_clientsession"
)

_SAMPLE_PAYLOAD = [
    {
        "title": "Heavy Rain",
        "warning": "NIL",
        "link": "/weather/rain-areas",
        "issuedDateTime": "2026-11-18T16:37:00",
        "expiryDateTime": "2026-11-18T18:37:00",
        "viewMore": False,
        "lastModifiedDateTime": "2026-11-18T16:37:01Z",
        "sequence": 0,
        "openNewTab": "_self",
        "issueText": None,
    },
    {
        "title": "Earthquake",
        "warning": (
            "[Updated at 12.10 pm 18 Nov 2026]\n"
            "A 6.0 magnitude earthquake was detected in Papua New Guinea Region, "
            "approximately 4806 km E of Singapore."
        ),
        "link": "/weather/regional-earthquake-and-tsunami-alert",
        "issuedDateTime": "2026-11-18T12:10:00",
        "expiryDateTime": "2026-11-18T18:10:00",
        "viewMore": False,
        "lastModifiedDateTime": "2026-11-18T04:12:45Z",
        "sequence": 1,
        "openNewTab": "_self",
        "issueText": None,
    },
    {
        "title": "Volcanic",
        "warning": "Ash plume observed.<br><br>Not expected to affect Singapore.",
        "link": "/weather/regional-volcanic-eruptions",
        "issuedDateTime": "2026-05-12T16:14:00",
        "expiryDateTime": "2026-05-14T16:14:00",
    },
    {
        "title": "Haze",
        "warning": None,
        "link": None,
        "issuedDateTime": "0001-01-01T00:00:00",
        "expiryDateTime": "0001-01-01T00:00:00",
    },
]


def test_parse_warnings():
    data = _parse_warnings(_SAMPLE_PAYLOAD)
    assert set(data.advisories) == {"Heavy Rain", "Earthquake", "Volcanic", "Haze"}
    assert data.heavy_rain is not None
    assert data.heavy_rain.active is False
    assert data.heavy_rain.text is None
    assert data.heavy_rain.link == "https://www.nea.gov.sg/weather/rain-areas"

    assert len(data.active_advisories) == 2
    assert data.earthquake is not None
    assert data.earthquake.active is True
    assert data.earthquake_magnitude == 6.0
    assert "<br>" not in (data.advisories["Volcanic"].text or "")
    assert data.advisories["Haze"].issued is None


@pytest.mark.parametrize("bad", [{}, [], [{"warning": "x"}]])
def test_parse_bad_payload_raises(bad):
    with pytest.raises(UpdateFailed):
        _parse_warnings(bad)


def _response(status: int = 200, payload=None):
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


@pytest.mark.asyncio
async def test_coordinator_fetch_and_fallback():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(payload=_SAMPLE_PAYLOAD))
    coordinator = WeatherWarningsCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data.earthquake_magnitude == 6.0
        cached = coordinator.data

        session.get = MagicMock(return_value=_response(status=503))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        session.get = MagicMock(side_effect=aiohttp.ClientError("down"))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        session.get = MagicMock(return_value=_response(payload={}))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_error_without_cache_fails():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(status=502))
    coordinator = WeatherWarningsCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

    session.get = MagicMock(side_effect=TimeoutError("timeout"))
    coordinator2 = WeatherWarningsCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator2.async_refresh()
        assert coordinator2.last_update_success is False


def test_warning_entities():
    data = _parse_warnings(_SAMPLE_PAYLOAD)
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True

    adv = SingaporeWeatherAdvisoriesSensor(coord, "e1")
    assert adv.unique_id == "e1_weather_advisories"
    assert adv.native_value == 2
    assert adv.extra_state_attributes["active_categories"] == ["Earthquake", "Volcanic"]

    eq = SingaporeLatestEarthquakeSensor(coord, "e1")
    assert eq.unique_id == "e1_latest_earthquake"
    assert eq.native_value == 6.0
    assert "Papua New Guinea" in eq.extra_state_attributes["advisory"]

    rain = SingaporeHeavyRainWarningBinarySensor(coord, "e1")
    assert rain.unique_id == "e1_heavy_rain_warning"
    assert rain.is_on is False
    assert rain.extra_state_attributes["warning"] is None

    # Active heavy rain warning
    active_payload = [
        {
            "title": "Heavy Rain",
            "warning": "Moderate to heavy thundery showers expected over northern areas.",
            "issuedDateTime": "2026-11-21T10:00:00",
            "expiryDateTime": "2026-11-21T11:30:00",
        }
    ]
    coord.data = _parse_warnings(active_payload)
    assert rain.is_on is True
    assert "thundery showers" in rain.extra_state_attributes["warning"]

    coord.data = None
    assert adv.native_value is None
    assert adv.extra_state_attributes == {}
    assert eq.native_value is None
    assert eq.extra_state_attributes == {}
    assert rain.is_on is None
    assert rain.extra_state_attributes == {}
