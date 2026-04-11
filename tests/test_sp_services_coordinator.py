"""Tests for the SP Services coordinator and HTTP client."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.sp_services_coordinator import (
    SpServicesClient,
    SpServicesCoordinator,
    SpServicesData,
    _parse_usage,
)

# ---------------------------------------------------------------------------
# _parse_usage unit tests — no network required
# ---------------------------------------------------------------------------


def _electricity(records=None, month_total=None, account_no=None) -> dict:
    payload: dict = {}
    if records is not None:
        payload["data"] = records
    if month_total is not None:
        payload["monthTotal"] = month_total
    if account_no is not None:
        payload["accountNo"] = account_no
    return payload


def _water(records=None, month_total=None) -> dict:
    payload: dict = {}
    if records is not None:
        payload["data"] = records
    if month_total is not None:
        payload["monthTotal"] = month_total
    return payload


def test_parse_usage_full() -> None:
    """All fields present → all values extracted."""
    elec = _electricity(
        records=[{"consumption": "10.5"}, {"consumption": "8.3"}],
        month_total=250.0,
        account_no="ACC-123",
    )
    water = _water(
        records=[{"consumption": "0.4"}],
        month_total=12.5,
    )
    result = _parse_usage(elec, water)
    assert result.electricity_today_kwh == pytest.approx(8.3)
    assert result.electricity_month_kwh == pytest.approx(250.0)
    assert result.water_today_m3 == pytest.approx(0.4)
    assert result.water_month_m3 == pytest.approx(12.5)
    assert result.account_no == "ACC-123"
    assert isinstance(result.last_updated, datetime)


def test_parse_usage_no_records() -> None:
    """Empty payload → today values are None, month values are None."""
    result = _parse_usage({}, {})
    assert result.electricity_today_kwh is None
    assert result.electricity_month_kwh is None
    assert result.water_today_m3 is None
    assert result.water_month_m3 is None
    assert result.account_no is None


def test_parse_usage_alternative_field_names() -> None:
    """Parser tries multiple field name variants."""
    elec = {
        "records": [{"value": "15.0"}],
        "totalUsage": 300.0,
        "accountNumber": "ACC-456",
    }
    water = {
        "usageData": [{"usage": "0.6"}, {"usage": "0.9"}],
        "monthlyUsage": 20.0,
    }
    result = _parse_usage(elec, water)
    assert result.electricity_today_kwh == pytest.approx(15.0)
    assert result.electricity_month_kwh == pytest.approx(300.0)
    assert result.water_today_m3 == pytest.approx(0.9)
    assert result.water_month_m3 == pytest.approx(20.0)
    assert result.account_no == "ACC-456"


def test_parse_usage_bad_float_ignored() -> None:
    """Non-numeric consumption value → today returns None."""
    elec = {"data": [{"consumption": "N/A"}], "monthTotal": 100.0}
    result = _parse_usage(elec, {})
    assert result.electricity_today_kwh is None
    assert result.electricity_month_kwh == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# SpServicesClient HTTP tests (mock niquests session)
# ---------------------------------------------------------------------------


def _make_response(status_code: int = 200, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.json.return_value = body or {}
    return resp


async def test_client_login_success() -> None:
    """Successful login returns the response body."""
    client = SpServicesClient()
    login_body = {"sessionId": "sess-abc", "message": "OTP sent"}
    client._session.post = AsyncMock(return_value=_make_response(200, login_body))

    result = await client.login("user@example.com", "secret")
    assert result["sessionId"] == "sess-abc"


async def test_client_login_invalid_credentials() -> None:
    """HTTP 401 from login endpoint → ValueError('invalid_auth')."""
    client = SpServicesClient()
    client._session.post = AsyncMock(return_value=_make_response(401))

    with pytest.raises(ValueError, match="invalid_auth"):
        await client.login("user@example.com", "wrongpassword")


async def test_client_login_server_error() -> None:
    """HTTP 500 → UpdateFailed."""
    client = SpServicesClient()
    client._session.post = AsyncMock(return_value=_make_response(500))

    with pytest.raises(UpdateFailed):
        await client.login("user@example.com", "secret")


async def test_client_verify_otp_success() -> None:
    """Successful OTP verification returns the token."""
    client = SpServicesClient()
    otp_body = {"token": "jwt.token.here"}
    client._session.post = AsyncMock(return_value=_make_response(200, otp_body))

    token = await client.verify_otp("123456", {"sessionId": "sess-abc"})
    assert token == "jwt.token.here"


async def test_client_verify_otp_alternative_token_fields() -> None:
    """Parser also accepts accessToken, authToken, access_token."""
    client = SpServicesClient()
    for field in ("accessToken", "authToken", "access_token"):
        client._session.post = AsyncMock(
            return_value=_make_response(200, {field: f"tok-{field}"})
        )
        token = await client.verify_otp("000000", {})
        assert token == f"tok-{field}"


async def test_client_verify_otp_wrong_code() -> None:
    """HTTP 401 from OTP endpoint → ValueError('invalid_otp')."""
    client = SpServicesClient()
    client._session.post = AsyncMock(return_value=_make_response(401))

    with pytest.raises(ValueError, match="invalid_otp"):
        await client.verify_otp("999999", {})


async def test_client_verify_otp_no_token_in_response() -> None:
    """200 response but no token field → UpdateFailed."""
    client = SpServicesClient()
    client._session.post = AsyncMock(return_value=_make_response(200, {"foo": "bar"}))

    with pytest.raises(UpdateFailed, match="no auth token"):
        await client.verify_otp("123456", {})


async def test_client_fetch_usage_success() -> None:
    """Successful fetch returns SpServicesData."""
    client = SpServicesClient()
    elec_resp = _make_response(
        200,
        {"data": [{"consumption": "12.5"}], "monthTotal": 300.0, "accountNo": "A1"},
    )
    water_resp = _make_response(
        200,
        {"data": [{"consumption": "0.5"}], "monthTotal": 15.0},
    )
    client._session.get = AsyncMock(side_effect=[elec_resp, water_resp])

    data = await client.fetch_usage("valid-token")
    assert isinstance(data, SpServicesData)
    assert data.electricity_today_kwh == pytest.approx(12.5)
    assert data.water_today_m3 == pytest.approx(0.5)
    assert data.account_no == "A1"


async def test_client_fetch_usage_electricity_401() -> None:
    """HTTP 401 from electricity endpoint → ConfigEntryAuthFailed."""
    client = SpServicesClient()
    client._session.get = AsyncMock(return_value=_make_response(401))

    with pytest.raises(ConfigEntryAuthFailed):
        await client.fetch_usage("expired-token")


async def test_client_fetch_usage_water_401() -> None:
    """HTTP 401 from water endpoint → ConfigEntryAuthFailed."""
    client = SpServicesClient()
    elec_ok = _make_response(200, {"data": [], "monthTotal": 0})
    water_unauth = _make_response(401)
    client._session.get = AsyncMock(side_effect=[elec_ok, water_unauth])

    with pytest.raises(ConfigEntryAuthFailed):
        await client.fetch_usage("expired-token")


# ---------------------------------------------------------------------------
# SpServicesCoordinator tests
# ---------------------------------------------------------------------------


def _make_entry(token: str | None = "tok") -> MagicMock:
    entry = MagicMock()
    entry.data = {"sp_token": token} if token else {}
    return entry


async def test_coordinator_no_token_raises_auth_failed() -> None:
    """Coordinator with no token immediately raises ConfigEntryAuthFailed."""
    hass = MagicMock()
    entry = _make_entry(token=None)
    coord = SpServicesCoordinator(hass, entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_coordinator_delegates_to_client() -> None:
    """Coordinator calls client.fetch_usage with the stored token."""
    hass = MagicMock()
    entry = _make_entry(token="my-token")
    coord = SpServicesCoordinator(hass, entry)

    expected = SpServicesData(
        electricity_today_kwh=10.0,
        electricity_month_kwh=200.0,
        water_today_m3=0.3,
        water_month_m3=8.0,
        account_no="ACC-001",
        last_updated=datetime.now(),
    )
    coord.client.fetch_usage = AsyncMock(return_value=expected)

    result = await coord._async_update_data()
    coord.client.fetch_usage.assert_called_once_with("my-token")
    assert result is expected
