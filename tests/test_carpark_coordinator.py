"""Tests for the HDB carpark availability coordinator and sensors."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.carpark_coordinator import (
    CARPARK_AVAILABILITY_URL,
    CARPARK_INFO_URL,
    CarparkCoordinator,
    _parse_availability,
    _parse_info,
    nearest_carparks,
    svy21_to_wgs84,
)
from custom_components.singapore.const import CONF_CARPARKS
from custom_components.singapore.sensor import (
    SingaporeCarparkSensor,
    async_setup_carpark_sensors,
)

_PATCH_SESSION = (
    "custom_components.singapore.carpark_coordinator.async_get_clientsession"
)

# Real rows from the HDB carpark information dataset (2026-11-19).
_INFO = {
    "success": True,
    "result": {
        "records": [
            {
                "car_park_no": "ACB",
                "address": "BLK 270/271 ALBERT CENTRE BASEMENT CAR PARK",
                "x_coord": "30314.7936",
                "y_coord": "31490.4942",
                "car_park_type": "BASEMENT CAR PARK",
                "type_of_parking_system": "ELECTRONIC PARKING",
                "short_term_parking": "WHOLE DAY",
                "free_parking": "NO",
                "night_parking": "YES",
                "car_park_decks": "1",
                "gantry_height": "1.8",
                "car_park_basement": "Y",
            },
            {
                "car_park_no": "ACM",
                "address": "BLK 98A ALJUNIED CRESCENT",
                "x_coord": "33758.4143",
                "y_coord": "33695.5198",
                "car_park_type": "MULTI-STOREY CAR PARK",
                "free_parking": "SUN & PH FR 7AM-10.30PM",
            },
            {"car_park_no": "NOXY", "address": "NOWHERE", "x_coord": "0"},
            # Listed in the information dataset, never in the live feed.
            {
                "car_park_no": "GHOST",
                "address": "NEXT TO ALBERT CENTRE",
                "x_coord": "30315",
                "y_coord": "31491",
            },
        ]
    },
}

_AVAILABILITY = {
    "items": [
        {
            "timestamp": "2026-11-19T18:15:36+08:00",
            "carpark_data": [
                {
                    "carpark_info": [
                        {"lot_type": "C", "lots_available": "103", "total_lots": "105"}
                    ],
                    "carpark_number": "ACB",
                    "update_datetime": "2026-11-19T18:14:20",
                },
                {
                    "carpark_info": [
                        {"lot_type": "C", "lots_available": "227", "total_lots": "583"},
                        {"lot_type": "Y", "lots_available": "12", "total_lots": "40"},
                        {"lot_type": "C", "lots_available": "3", "total_lots": "17"},
                        {"lot_type": "H", "lots_available": "x", "total_lots": "2"},
                    ],
                    "carpark_number": "acm",
                    "update_datetime": "2026-11-19T18:15:04",
                },
                {"carpark_info": [], "carpark_number": "NOXY"},
                "junk",
            ],
        }
    ]
}


def test_svy21_conversion_matches_known_locations():
    # Albert Centre and Aljunied Crescent, within ~30 m of their map locations.
    lat, lon = svy21_to_wgs84(31490.4942, 30314.7936)
    assert lat == pytest.approx(1.30106, abs=3e-4)
    assert lon == pytest.approx(103.85412, abs=3e-4)
    lat, lon = svy21_to_wgs84(33695.5198, 33758.4143)
    assert lat == pytest.approx(1.32100, abs=3e-4)
    assert lon == pytest.approx(103.88506, abs=3e-4)
    # The false origin maps back to the projection origin.
    lat, lon = svy21_to_wgs84(38744.572, 28001.642)
    assert lat == pytest.approx(1.366666, abs=1e-6)
    assert lon == pytest.approx(103.833333, abs=1e-6)


def test_parse_info():
    info = _parse_info(_INFO)
    assert set(info) == {"ACB", "ACM", "NOXY", "GHOST"}
    acb = info["ACB"]
    assert acb.address == "BLK 270/271 ALBERT CENTRE BASEMENT CAR PARK"
    assert acb.gantry_height == 1.8
    assert acb.night_parking == "YES"
    assert acb.latitude == pytest.approx(1.30106, abs=3e-4)
    assert info["NOXY"].latitude is None


def test_parse_availability():
    carparks, timestamp = _parse_availability(_AVAILABILITY)
    assert set(carparks) == {"ACB", "ACM", "NOXY"}
    acm = carparks["ACM"]
    # A lot type listed twice is added up; unparseable rows are skipped.
    assert acm.lots["C"].available == 230
    assert acm.lots["C"].total == 600
    assert acm.lots["Y"].available == 12
    assert "H" not in acm.lots
    assert acm.updated.isoformat() == "2026-11-19T18:15:04+08:00"
    assert timestamp.isoformat() == "2026-11-19T18:15:36+08:00"


@pytest.mark.parametrize(
    "payload", [{}, [], {"items": []}, {"items": [{"carpark_data": []}]}]
)
def test_parse_availability_bad_payload_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_availability(payload)


def test_parse_info_bad_payload_raises():
    with pytest.raises(UpdateFailed):
        _parse_info({"result": {"records": []}})


def test_nearest_carparks():
    info = _parse_info(_INFO)
    albert = (info["ACB"].latitude, info["ACB"].longitude)
    assert nearest_carparks(info, *albert, 2) == ["ACB", "GHOST"]
    # Only carparks the live feed reports are picked.
    assert nearest_carparks(info, *albert, 2, {"ACB", "ACM"}) == ["ACB", "ACM"]


def _response(status=200, payload=None):
    response = AsyncMock()
    response.headers = {}
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _session(info=None, availability=None):
    routes = {
        CARPARK_INFO_URL: info or _response(payload=_INFO),
        CARPARK_AVAILABILITY_URL: availability or _response(payload=_AVAILABILITY),
    }
    session = MagicMock()

    def _get(url, **_kwargs):
        route = routes[url]
        if isinstance(route, Exception):
            raise route
        return route

    session.get = MagicMock(side_effect=_get)
    return session


def _entry(options=None):
    entry = MagicMock()
    entry.options = options or {}
    return entry


def _hass(lat=1.30106, lon=103.85412):
    hass = MagicMock()
    hass.config.latitude = lat
    hass.config.longitude = lon
    return hass


async def _refresh(coordinator, session):
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()


@pytest.mark.asyncio
async def test_coordinator_tracks_nearest_carparks_by_default():
    coordinator = CarparkCoordinator(_hass(), _entry())
    session = _session()
    await _refresh(coordinator, session)

    assert coordinator.last_update_success is True
    data = coordinator.data
    # GHOST is closer but never reported, so ACM is the second; NOXY has no
    # location, so it can't be "near" anything.
    assert data.tracked == ["ACB", "ACM"]
    assert set(data.availability) == {"ACB", "ACM"}
    assert data.info["ACB"].address.startswith("BLK 270/271")
    assert data.carpark_count == 3

    # The information dataset is only re-read once a day.
    await _refresh(coordinator, session)
    urls = [c.args[0] for c in session.get.call_args_list]
    assert urls.count(CARPARK_INFO_URL) == 1
    assert urls.count(CARPARK_AVAILABILITY_URL) == 2


@pytest.mark.asyncio
async def test_coordinator_tracks_chosen_carparks_without_info():
    coordinator = CarparkCoordinator(_hass(), _entry({CONF_CARPARKS: "acm, MISSING"}))
    session = _session(info=aiohttp.ClientError("down"))
    await _refresh(coordinator, session)

    assert coordinator.last_update_success is True
    assert coordinator.data.tracked == ["ACM", "MISSING"]
    assert set(coordinator.data.availability) == {"ACM"}
    assert coordinator.data.info == {}

    # The failed information fetch is retried on the next poll.
    await _refresh(coordinator, _session())
    assert set(coordinator.data.info) == {"ACM"}


@pytest.mark.asyncio
async def test_coordinator_falls_back_and_fails_on_bad_payload():
    coordinator = CarparkCoordinator(_hass(), _entry())
    await _refresh(coordinator, _session())
    cached = coordinator.data

    await _refresh(coordinator, _session(availability=_response(status=503)))
    assert coordinator.last_update_success is True
    assert coordinator.data is cached

    await _refresh(coordinator, _session(availability=_response(payload={})))
    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_without_cache_fails():
    coordinator = CarparkCoordinator(_hass(), _entry())
    await _refresh(coordinator, _session(availability=aiohttp.ClientError("x")))
    assert coordinator.last_update_success is False


async def _coordinator_with_data():
    coordinator = CarparkCoordinator(_hass(), _entry())
    await _refresh(coordinator, _session())
    return coordinator


@pytest.mark.asyncio
async def test_carpark_sensor():
    coordinator = await _coordinator_with_data()
    sensor = SingaporeCarparkSensor(coordinator, "entry1", "ACM")
    sensor.hass = coordinator.hass

    assert sensor.unique_id == "entry1_carpark_acm"
    assert sensor._attr_translation_placeholders == {"number": "ACM"}
    assert sensor.native_value == 230
    attrs = sensor.extra_state_attributes
    assert attrs["car_lots_total"] == 600
    assert attrs["motorcycle_lots_available"] == 12
    assert attrs["address"] == "BLK 98A ALJUNIED CRESCENT"
    assert attrs["free_parking"] == "SUN & PH FR 7AM-10.30PM"
    assert attrs["latitude"] == pytest.approx(1.321, abs=1e-3)
    assert attrs["distance_km"] > 3

    # A carpark without car lots, or missing from the feed, has no value.
    coordinator.data.availability["NOXY"] = _parse_availability(_AVAILABILITY)[0][
        "NOXY"
    ]
    assert SingaporeCarparkSensor(coordinator, "entry1", "NOXY").native_value is None
    gone = SingaporeCarparkSensor(coordinator, "entry1", "GONE")
    assert gone.native_value is None
    assert gone.extra_state_attributes == {"carpark_number": "GONE"}


@pytest.mark.asyncio
async def test_carpark_sensors_are_added_as_carparks_become_known():
    coordinator = CarparkCoordinator(_hass(), _entry())
    added = []
    unsub = async_setup_carpark_sensors(coordinator, "entry1", added.extend)
    assert added == []  # no data yet

    await _refresh(coordinator, _session())
    coordinator.async_update_listeners()
    assert [s.unique_id for s in added] == ["entry1_carpark_acb", "entry1_carpark_acm"]
    # A later poll with the same carparks adds nothing.
    coordinator.async_update_listeners()
    assert len(added) == 2
    unsub()
