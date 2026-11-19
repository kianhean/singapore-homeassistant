"""Tests for the CapitaLand JustPark carpark coordinator and sensors."""

import json
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.capitaland_coordinator import (
    CAPITALAND_API_URL,
    CAPITALAND_PAGE_URL,
    CapitaLandCarparkCoordinator,
    CapitaLandData,
    _parse_sites,
    extract_token,
)
from custom_components.singapore.sensor import (
    SingaporeCapitaLandCarparkSensor,
    async_setup_capitaland_sensors,
)

_PATCH_SESSION = (
    "custom_components.singapore.capitaland_coordinator.async_get_clientsession"
)
# A real answer (2026-11-19), trimmed to a few sites.
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "capitaland_lots.json").read_text()
)
_PAGE = (
    '<form action="/Lot-Availability" method="post">'
    '<input name="__RequestVerificationToken" type="hidden" value="tok-1" /></form>'
)


def test_extract_token():
    assert extract_token(_PAGE) == "tok-1"
    swapped = '<input value="tok-2" type="hidden" name="__RequestVerificationToken">'
    assert extract_token(swapped) == "tok-2"
    assert extract_token("<html></html>") is None


def test_parse_real_payload():
    data = _parse_sites(FIXTURE)
    bedok = data.sites["BM"]
    assert (bedok.name, bedok.business_unit) == ("Bedok Mall", "Retail")
    assert (bedok.available, bedok.total, bedok.full) == (39, 264, False)
    # "NA" / null: no count.
    westgate_office = data.sites["WGO"]
    assert westgate_office.available is None
    assert westgate_office.total is None
    malls = [site.code for site in data.malls()]
    assert "BM" in malls and "PS" in malls
    # Offices are not malls.
    assert "CT" not in malls and "WGO" not in malls


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"HasError": True, "Message": "boom"},
        {"HasError": False, "Result": "not json"},
        {"HasError": False, "Result": "[]"},
        {"HasError": False, "Result": [{"SiteDesc": "No code"}]},
    ],
)
def test_parse_bad_payload_raises(payload):
    with pytest.raises(UpdateFailed):
        _parse_sites(payload)


def _response(status=200, text="", cookies=None):
    response = AsyncMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    jar = SimpleCookie()
    for name, value in (cookies or {}).items():
        jar[name] = value
    response.cookies = jar
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _page(token="tok-1"):
    return _response(
        text=_PAGE.replace("tok-1", token),
        cookies={"__RequestVerificationToken": f"cookie-{token}", "ARRAffinity": "a"},
    )


def _api(payload=FIXTURE, status=200):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return _response(status=status, text=text)


@pytest.mark.asyncio
async def test_coordinator_sends_token_and_cookie_and_reuses_them():
    session = MagicMock()
    session.get = MagicMock(return_value=_page())
    session.post = MagicMock(side_effect=[_api(), _api()])
    coordinator = CapitaLandCarparkCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.sites["BM"].available == 39
    # The page is loaded once; the token is kept between polls.
    assert session.get.call_count == 1
    assert session.get.call_args.args[0] == CAPITALAND_PAGE_URL
    assert session.post.call_count == 2
    call = session.post.call_args
    assert call.args[0] == CAPITALAND_API_URL
    headers = call.kwargs["headers"]
    assert headers["__RequestVerificationToken"] == "tok-1"
    assert headers["X-APIAction"] == "SelectSiteNoAuth/OnSelectSite"
    assert "__RequestVerificationToken=cookie-tok-1" in headers["Cookie"]
    assert "ARRAffinity=a" in headers["Cookie"]
    assert json.loads(call.kwargs["data"]["JData"]) == {
        "DisplayType": "LotAvail",
        "LotType": None,
    }


@pytest.mark.asyncio
async def test_stale_token_reloads_the_page_once():
    session = MagicMock()
    session.get = MagicMock(side_effect=[_page(), _page("tok-2")])
    session.post = MagicMock(side_effect=[_api(), _api("<html>Error</html>"), _api()])
    coordinator = CapitaLandCarparkCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert session.get.call_count == 2
    assert session.post.call_args.kwargs["headers"]["__RequestVerificationToken"] == (
        "tok-2"
    )


@pytest.mark.asyncio
async def test_html_from_a_fresh_token_fails():
    session = MagicMock()
    session.get = MagicMock(return_value=_page())
    session.post = MagicMock(return_value=_api("<html>Error</html>"))
    coordinator = CapitaLandCarparkCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_page_without_token_fails():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(text="<html></html>"))
    coordinator = CapitaLandCarparkCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_transient_errors_keep_last_known_data():
    session = MagicMock()
    session.get = MagicMock(return_value=_page())
    session.post = MagicMock(return_value=_api())
    coordinator = CapitaLandCarparkCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        cached = coordinator.data

        session.post = MagicMock(return_value=_api(status=503))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is cached

        session.post = MagicMock(side_effect=aiohttp.ClientError("down"))
        await coordinator.async_refresh()
        assert coordinator.data is cached

        # A changed payload is a real failure.
        session.post = MagicMock(return_value=_api({"HasError": True}))
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_first_fetch_errors_fail():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(status=500))
    coordinator = CapitaLandCarparkCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_sensor():
    coordinator = _coordinator(_parse_sites(FIXTURE))
    sensor = SingaporeCapitaLandCarparkSensor(coordinator, "entry1", "BM", "Bedok Mall")
    assert sensor.unique_id == "entry1_capitaland_bm"
    assert sensor._attr_translation_key == "capitaland_carpark"
    assert sensor._attr_translation_placeholders == {"mall": "Bedok Mall"}
    assert sensor.native_value == 39
    assert sensor.extra_state_attributes == {
        "site_code": "BM",
        "total_lots": 264,
        "full": False,
    }
    gone = SingaporeCapitaLandCarparkSensor(coordinator, "entry1", "XX", "Gone")
    assert gone.native_value is None
    assert gone.extra_state_attributes == {"site_code": "XX"}
    empty = SingaporeCapitaLandCarparkSensor(_coordinator(None), "entry1", "BM", "B")
    assert empty.native_value is None


def test_sensors_added_per_mall_as_they_appear():
    coordinator = _coordinator(None)
    listeners = []
    coordinator.async_add_listener = lambda cb: listeners.append(cb) or (lambda: None)
    added = []
    async_setup_capitaland_sensors(coordinator, "entry1", added.extend)
    assert added == []

    data = _parse_sites(FIXTURE)
    coordinator.data = data
    listeners[0]()
    codes = {sensor._code for sensor in added}
    assert codes == {site.code for site in data.malls()}
    assert "CT" not in codes and "WGO" not in codes

    # Nothing new: nothing added twice.
    listeners[0]()
    assert len(added) == len(codes)

    coordinator.data = CapitaLandData(dict(data.sites))
    listeners[0]()
    assert len(added) == len(codes)
