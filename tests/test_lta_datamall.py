"""Tests for LTA DataMall train crowd density, EV chargers, and options validation."""

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
    CONF_EV_POSTAL_CODES,
    CONF_LTA_API_KEY,
    CONF_TRAIN_CROWD_STATIONS,
    SOURCE_EV_CHARGERS,
    SOURCE_TRAIN_CROWD,
    ev_postal_codes,
    lta_datamall_headers,
    train_crowd_stations,
)
from custom_components.singapore.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.singapore.ev_charger_coordinator import (
    EvChargerCoordinator,
    batch_link,
    parse_ev_locations,
    summarize_ev_locations,
)
from custom_components.singapore.sensor import (
    SingaporeEvChargersAvailableSensor,
    SingaporeNearestEvChargerSensor,
    SingaporeTrainCrowdSensor,
)
from custom_components.singapore.train_crowd_coordinator import (
    TrainCrowdCoordinator,
    parse_pcd_realtime,
    station_to_train_line,
)

PCD_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "lta_pcd_realtime.json").read_text()
)
EV_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "lta_ev_charging_points.json").read_text()
)


def _entry(options: dict | None = None) -> ConfigEntry:
    entry = ConfigEntry("e1")
    entry.options = options or {}
    entry.subentries = {}
    return entry


def test_const_helpers():
    assert lta_datamall_headers(_entry()) == {}
    assert lta_datamall_headers(_entry({CONF_LTA_API_KEY: " secret "})) == {
        "AccountKey": "secret",
        "accept": "application/json",
    }
    assert train_crowd_stations(_entry()) == ["EW13", "EW14", "NS24"]
    assert train_crowd_stations(
        _entry({CONF_TRAIN_CROWD_STATIONS: "ew13, cc1; ew13"})
    ) == [
        "EW13",
        "CC1",
    ]
    assert ev_postal_codes(_entry({CONF_EV_POSTAL_CODES: "529536, abc, 238839"})) == [
        "529536",
        "238839",
    ]


def test_station_to_train_line():
    assert station_to_train_line("EW13") == "EWL"
    assert station_to_train_line("ns24") == "NSL"
    assert station_to_train_line("STC") == "SLRT"
    assert station_to_train_line("PE4") == "PLRT"
    assert station_to_train_line("999") is None


def test_parse_pcd_realtime():
    stations = parse_pcd_realtime(PCD_FIXTURE, "EWL")
    assert stations["EW13"].crowd_level == "moderate"
    assert stations["EW14"].crowd_level == "high"
    assert stations["NS24"].crowd_level == "low"
    assert stations["EW15"].crowd_level is None
    assert stations["EW13"].start_time == "2026-11-21T15:00:00+08:00"

    with pytest.raises(UpdateFailed):
        parse_pcd_realtime({"bad": True}, "EWL")


@pytest.mark.asyncio
async def test_train_crowd_coordinator_and_sensor():
    entry = _entry(
        {
            CONF_LTA_API_KEY: "test-key",
            CONF_TRAIN_CROWD_STATIONS: "EW13, EW14",
        }
    )
    coord = TrainCrowdCoordinator(MagicMock(), entry)

    resp = MagicMock(status=200)
    resp.json = AsyncMock(return_value=PCD_FIXTURE)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm

    with patch(
        "custom_components.singapore.train_crowd_coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coord._async_update_data()

    # Only 1 line (EWL) should be queried for EW13 + EW14.
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["params"] == {"TrainLine": "EWL"}
    assert session.get.call_args.kwargs["headers"]["AccountKey"] == "test-key"
    assert data.stations["EW13"].crowd_level == "moderate"

    coord.data = data
    sensor = SingaporeTrainCrowdSensor(coord, "e1", "EW13")
    assert sensor.unique_id == "e1_train_crowd_ew13"
    assert sensor.native_value == "moderate"
    assert sensor.extra_state_attributes["train_line"] == "EWL"


def test_parse_ev_locations_and_summary():
    # Near Tampines 1 (1.3541, 103.9452)
    locations = parse_ev_locations(
        EV_FIXTURE, home_lat=1.3540, home_lon=103.9450, max_distance_km=2.0
    )
    assert len(locations) == 1
    loc = locations[0]
    assert loc.name == "Tampines 1 Carpark"
    assert loc.available == 2
    assert loc.total == 3
    assert loc.operators == ["SP Mobility"]
    assert loc.plug_types == ["Type 2", "CCS2"]
    assert loc.max_power_kw == pytest.approx(50.0)
    assert loc.distance_km == pytest.approx(0.02, abs=0.02)

    summary = summarize_ev_locations(locations)
    assert summary.available_chargers == 2
    assert summary.total_chargers == 3
    assert summary.nearest_available is loc

    with pytest.raises(UpdateFailed):
        parse_ev_locations({"wrong": []})

    # DataMall's OData envelope is unwrapped
    wrapped = parse_ev_locations(
        {"odata.metadata": "x", "value": EV_FIXTURE},
        home_lat=1.3540,
        home_lon=103.9450,
        max_distance_km=2.0,
    )
    assert [w.name for w in wrapped] == ["Tampines 1 Carpark"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"odata.metadata": "x", "value": [{"Link": "https://a"}]}, "https://a"),
        ({"value": {"Link": "https://b"}}, "https://b"),
        ({"Link": "https://c"}, "https://c"),
        ({"value": []}, None),
        ({"value": [{"Link": ""}]}, None),
        ([], None),
        (None, None),
    ],
)
def test_ev_batch_link(payload, expected):
    assert batch_link(payload) == expected


@pytest.mark.asyncio
async def test_ev_charger_coordinator_postal_and_batch_modes():
    hass = MagicMock()
    hass.config.latitude = 1.3540
    hass.config.longitude = 103.9450

    # 1. Postal code mode
    entry_postal = _entry(
        {CONF_LTA_API_KEY: "test-key", CONF_EV_POSTAL_CODES: "529536"}
    )
    coord = EvChargerCoordinator(hass, entry_postal)
    resp = MagicMock(status=200)
    resp.json = AsyncMock(return_value=EV_FIXTURE)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm

    with patch(
        "custom_components.singapore.ev_charger_coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coord._async_update_data()

    assert session.get.call_args.kwargs["params"] == {"PostalCode": "529536"}
    assert data.available_chargers == 2
    assert data.total_chargers == 4

    coord.data = data
    avail_sensor = SingaporeEvChargersAvailableSensor(coord, "e1")
    nearest_sensor = SingaporeNearestEvChargerSensor(coord, "e1")
    assert avail_sensor.native_value == 2
    assert avail_sensor.extra_state_attributes["total_chargers"] == 4
    assert nearest_sensor.native_value == pytest.approx(0.02, abs=0.02)
    assert nearest_sensor.extra_state_attributes["name"] == "Tampines 1 Carpark"

    # 2. EVCBatch nearby mode
    entry_batch = _entry({CONF_LTA_API_KEY: "test-key"})
    coord_batch = EvChargerCoordinator(hass, entry_batch)
    meta_resp = MagicMock(status=200)
    # DataMall's real answer: the link inside the OData "value" list
    meta_resp.json = AsyncMock(
        return_value={
            "odata.metadata": "https://datamall2.mytransport.sg/ltaodataservice/$metadata#EVCBatch",
            "value": [{"Link": "https://s3.example/ev.json"}],
        }
    )
    batch_resp = MagicMock(status=200)
    batch_resp.json = AsyncMock(return_value=EV_FIXTURE)

    def _cm(r):
        m = MagicMock()
        m.__aenter__ = AsyncMock(return_value=r)
        m.__aexit__ = AsyncMock(return_value=False)
        return m

    session.get.side_effect = [_cm(meta_resp), _cm(batch_resp)]
    with patch(
        "custom_components.singapore.ev_charger_coordinator.async_get_clientsession",
        return_value=session,
    ):
        batch_data = await coord_batch._async_update_data()
    # Only Tampines 1 is within 2 km of home
    assert len(batch_data.locations) == 1
    assert batch_data.available_chargers == 2


@pytest.mark.asyncio
async def test_options_flow_validates_lta_api_key():
    flow = SingaporeOptionsFlow()
    flow.hass = MagicMock()
    flow.config_entry = _entry()

    # Enabling an LTA source without a key -> lta_api_key_required
    res = await flow.async_step_init({"sources": ["weather", SOURCE_TRAIN_CROWD]})
    assert res["type"] == "form"
    assert res["errors"][CONF_LTA_API_KEY] == "lta_api_key_required"

    # Invalid key (HTTP 401) -> invalid_lta_api_key
    resp = MagicMock(status=401)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm

    with patch(
        "custom_components.singapore.config_flow.async_get_clientsession",
        return_value=session,
    ):
        res = await flow.async_step_init(
            {
                "sources": ["weather", SOURCE_EV_CHARGERS],
                CONF_LTA_API_KEY: "bad-key",
            }
        )
    assert res["type"] == "form"
    assert res["errors"][CONF_LTA_API_KEY] == "invalid_lta_api_key"

    # Network error -> cannot_connect_lta
    session.get.side_effect = aiohttp.ClientError("dns failed")
    with patch(
        "custom_components.singapore.config_flow.async_get_clientsession",
        return_value=session,
    ):
        res = await flow.async_step_init(
            {
                "sources": ["weather", SOURCE_EV_CHARGERS],
                CONF_LTA_API_KEY: "any-key",
            }
        )
    assert res["type"] == "form"
    assert res["errors"][CONF_LTA_API_KEY] == "cannot_connect_lta"

    # Valid key (HTTP 200) -> creates entry
    resp.status = 200
    session.get.side_effect = None
    with patch(
        "custom_components.singapore.config_flow.async_get_clientsession",
        return_value=session,
    ):
        res = await flow.async_step_init(
            {
                "sources": ["weather", SOURCE_TRAIN_CROWD, SOURCE_EV_CHARGERS],
                CONF_LTA_API_KEY: "good-key",
                CONF_TRAIN_CROWD_STATIONS: "ew13, ns24",
                CONF_EV_POSTAL_CODES: "529536",
            }
        )
    assert res["type"] == "create_entry"
    assert res["data"][CONF_LTA_API_KEY] == "good-key"
    assert res["data"][CONF_TRAIN_CROWD_STATIONS] == "EW13, NS24"
    assert res["data"][CONF_EV_POSTAL_CODES] == "529536"


@pytest.mark.asyncio
async def test_diagnostics_redacts_lta_api_key():
    entry = _entry({CONF_LTA_API_KEY: "super-secret-lta-key"})
    entry.runtime_data = MagicMock()
    entry.runtime_data.coordinators.return_value = {}
    entry.runtime_data.sp_accounts = {}

    diag = await async_get_config_entry_diagnostics(MagicMock(), entry)
    assert diag["options"]["has_lta_api_key"] is True
    assert "super-secret-lta-key" not in json.dumps(diag)


def test_options_from_input_normalizes_lta_fields():
    stored = options_from_input(
        {
            "sources": ["weather", SOURCE_TRAIN_CROWD],
            CONF_LTA_API_KEY: "  mykey  ",
            CONF_TRAIN_CROWD_STATIONS: " ew13 ; ns24 ",
            CONF_EV_POSTAL_CODES: "529536, bad, 238839",
        }
    )
    assert stored[CONF_LTA_API_KEY] == "mykey"
    assert stored[CONF_TRAIN_CROWD_STATIONS] == "EW13, NS24"
    assert stored[CONF_EV_POSTAL_CODES] == "529536, 238839"
