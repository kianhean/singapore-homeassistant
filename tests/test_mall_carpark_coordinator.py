"""LTA DataMall mall carparks: parsing, choosing, paging, sensors, options."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.config_flow import (
    SingaporeOptionsFlow,
    options_from_input,
)
from custom_components.singapore.const import (
    CONF_LTA_API_KEY,
    CONF_MALL_CARPARKS,
    SOURCE_MALL_CARPARK,
    mall_carpark_terms,
)
from custom_components.singapore.mall_carpark_coordinator import (
    PAGE_SIZE,
    MallCarparkCoordinator,
    MallCarparkData,
    choose_carparks,
    parse_carparks,
)
from custom_components.singapore.sensor import (
    SingaporeMallCarparkSensor,
    async_setup_mall_carpark_sensors,
)

# Rows in DataMall's documented CarParkAvailabilityv2 shape (illustrative
# values: no AccountKey was available to record a live answer).
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "lta_carpark_availability.json").read_text()
)
ROWS = FIXTURE["value"]
# Marina Bay, closest to Suntec City and the URA Beach Road carpark.
HOME = (1.2905, 103.8520)
_SESSION = (
    "custom_components.singapore.mall_carpark_coordinator.async_get_clientsession"
)


def _entry(options: dict | None = None) -> ConfigEntry:
    entry = ConfigEntry("e1")
    entry.options = {CONF_LTA_API_KEY: "key", **(options or {})}
    entry.subentries = {}
    return entry


def _hass():
    hass = MagicMock()
    hass.config.latitude, hass.config.longitude = HOME
    return hass


def _session(*payloads, status=200):
    responses = []
    for payload in payloads:
        response = MagicMock(status=status)
        response.json = AsyncMock(return_value=payload)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=response)
        cm.__aexit__ = AsyncMock(return_value=False)
        responses.append(cm)
    session = MagicMock()
    session.get.side_effect = responses
    return session


def test_terms_split_on_commas_only():
    assert mall_carpark_terms(_entry()) == []
    entry = _entry({CONF_MALL_CARPARKS: " ION  Orchard, vivocity;VivoCity ,, 22 "})
    assert mall_carpark_terms(entry) == ["ION Orchard", "vivocity", "22"]
    assert (
        options_from_input({"sources": [], CONF_MALL_CARPARKS: "Jem;  Suntec City "})[
            CONF_MALL_CARPARKS
        ]
        == "Jem, Suntec City"
    )
    assert CONF_MALL_CARPARKS not in options_from_input({"sources": []})


def test_parse_merges_lot_types_and_skips_hdb():
    carparks = parse_carparks(ROWS)
    assert "hdb_bj55" not in carparks
    suntec = carparks["lta_1"]
    assert suntec.development == "Suntec City"
    assert suntec.area == "Marina"
    assert suntec.lots == {"C": 442, "Y": 12}
    assert suntec.car_lots == 442
    assert (suntec.latitude, suntec.longitude) == (1.29375, 103.85718)
    assert carparks["ura_a0007"].agency == "URA"
    assert carparks["ura_a0007"].area is None


def test_parse_tolerates_bad_rows():
    carparks = parse_carparks(
        [
            "nope",
            {"CarParkID": "", "Agency": "LTA"},
            {"CarParkID": "9", "Agency": "LTA", "Location": "x", "AvailableLots": "?"},
        ]
    )
    assert carparks["lta_9"].lots == {}
    assert carparks["lta_9"].latitude is None
    assert carparks["lta_9"].car_lots is None
    assert carparks["lta_9"].distance_km(*HOME) is None


def test_choose_by_name_or_id_in_order():
    carparks = parse_carparks(ROWS)
    chosen = choose_carparks(carparks, ["vivocity", "1", "beach road"], *HOME)
    assert list(chosen) == ["lta_30", "lta_31", "lta_1", "ura_a0007"]
    assert choose_carparks(carparks, ["not a mall"], *HOME) == {}


def test_choose_nearest_lta_only():
    carparks = parse_carparks(ROWS)
    nearest = choose_carparks(carparks, [], *HOME, count=2)
    # The URA carpark is close too, but nearest mode tracks malls (LTA) only.
    assert list(nearest) == ["lta_1", "lta_22"]
    assert choose_carparks(carparks, [], None, None) == {}


async def test_coordinator_pages_until_a_short_page():
    full_page = {"value": [dict(ROWS[0], CarParkID=str(n)) for n in range(PAGE_SIZE)]}
    session = _session(full_page, FIXTURE)
    coordinator = MallCarparkCoordinator(
        _hass(), _entry({CONF_MALL_CARPARKS: "Suntec"})
    )
    with patch(_SESSION, return_value=session):
        data = await coordinator._async_update_data()
    skips = [c.kwargs["params"]["$skip"] for c in session.get.call_args_list]
    assert skips == ["0", str(PAGE_SIZE)]
    assert session.get.call_args.kwargs["headers"]["AccountKey"] == "key"
    # Every "Suntec City" row matches: the page of copies plus the fixture's.
    assert "lta_1" in data.carparks
    assert len(data.carparks) == PAGE_SIZE


async def test_coordinator_nearest_mode():
    coordinator = MallCarparkCoordinator(_hass(), _entry())
    with patch(_SESSION, return_value=_session(FIXTURE)):
        data = await coordinator._async_update_data()
    assert list(data.carparks)[0] == "lta_1"
    assert len(data.carparks) == 5


async def test_coordinator_errors_and_fallback():
    coordinator = MallCarparkCoordinator(_hass(), _entry())
    with patch(_SESSION, return_value=_session(FIXTURE, status=503)):
        with pytest.raises(UpdateFailed, match="HTTP 503"):
            await coordinator._async_update_data()
    with patch(_SESSION, return_value=_session({"value": None})):
        with pytest.raises(UpdateFailed, match="missing value list"):
            await coordinator._async_update_data()
    with patch(_SESSION, return_value=_session({"value": [ROWS[-1]]})):
        with pytest.raises(UpdateFailed, match="no LTA or URA carparks"):
            await coordinator._async_update_data()

    coordinator.data = previous = MallCarparkData()
    with patch(_SESSION, return_value=_session(FIXTURE, status=429)):
        assert await coordinator._async_update_data() is previous
    failing = MagicMock()
    failing.get.side_effect = aiohttp.ClientError("down")
    with patch(_SESSION, return_value=failing):
        assert await coordinator._async_update_data() is previous


async def test_coordinator_without_key_fails():
    entry = ConfigEntry("e1")
    entry.options = {}
    with pytest.raises(UpdateFailed, match="API key not set"):
        await MallCarparkCoordinator(_hass(), entry)._async_update_data()


def test_sensor_state_and_attributes():
    coordinator = MallCarparkCoordinator(_hass(), _entry())
    coordinator.data = MallCarparkData(parse_carparks(ROWS))
    sensor = SingaporeMallCarparkSensor(coordinator, "e1", "lta_1", "Suntec City")
    sensor.hass = _hass()
    assert sensor.unique_id == "e1_mall_carpark_lta_1"
    assert sensor._attr_translation_key == "mall_carpark"
    assert sensor._attr_translation_placeholders == {"development": "Suntec City"}
    assert sensor.native_value == 442
    attrs = sensor.extra_state_attributes
    assert attrs["carpark_id"] == "1"
    assert attrs["agency"] == "LTA"
    assert attrs["car_lots_available"] == 442
    assert attrs["motorcycle_lots_available"] == 12
    assert attrs["latitude"] == 1.29375
    assert attrs["distance_km"] == pytest.approx(0.67, abs=0.05)

    gone = SingaporeMallCarparkSensor(coordinator, "e1", "lta_404", "Closed Mall")
    assert gone.native_value is None
    assert gone.extra_state_attributes == {}


def test_sensors_added_as_carparks_appear():
    coordinator = MallCarparkCoordinator(_hass(), _entry())
    added: list = []
    unsub = async_setup_mall_carpark_sensors(
        coordinator, "e1", lambda entities: added.extend(entities)
    )
    assert added == []
    carparks = parse_carparks(ROWS)
    coordinator.data = MallCarparkData({"lta_1": carparks["lta_1"]})
    coordinator.async_update_listeners()
    coordinator.data = MallCarparkData(
        {"lta_1": carparks["lta_1"], "lta_22": carparks["lta_22"]}
    )
    coordinator.async_update_listeners()
    coordinator.async_update_listeners()
    assert [s.unique_id for s in added] == [
        "e1_mall_carpark_lta_1",
        "e1_mall_carpark_lta_22",
    ]
    unsub()


async def test_options_flow_needs_lta_key_for_mall_carparks():
    flow = SingaporeOptionsFlow()
    flow.config_entry = ConfigEntry()
    flow.config_entry.options = {}
    flow.config_entry.subentries = {}
    result = await flow.async_step_init(
        {"sources": [SOURCE_MALL_CARPARK], "polling": "normal"}
    )
    assert result["errors"] == {"lta_api_key": "lta_api_key_required"}
