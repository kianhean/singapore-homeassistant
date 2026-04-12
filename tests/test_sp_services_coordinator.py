"""Tests for the SP Services coordinator and HTTP client."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.singapore.sp_services_coordinator import (
    SpServicesClient,
    SpServicesCoordinator,
    SpServicesData,
    _code_from_url,
    _extract_csrf,
    _extract_primary_account,
    _parse_daily_csv,
    _parse_daily_usage,
    _parse_monthly_csv,
    _parse_monthly_usage,
    _parse_titled_csv_sections,
)


def _make_response(
    status_code: int = 200,
    body: dict | None = None,
    *,
    text: str = "",
    url: str = "https://services.spservices.sg/",
    history: list | None = None,
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.json.return_value = body or {}
    resp.text = text
    resp.url = url
    resp.history = history or []
    resp.headers = headers or {}
    return resp


def test_extract_csrf_from_hidden_input() -> None:
    html = '<input type="hidden" name="_csrf" value="csrf-123">'
    assert _extract_csrf(html) == "csrf-123"


def test_extract_csrf_from_inline_config() -> None:
    html = '{"_csrf":"csrf-456"}'
    assert _extract_csrf(html) == "csrf-456"


def test_code_from_url_matches_state() -> None:
    url = "https://services.spservices.sg/callback?code=abc123&state=state-1"
    assert _code_from_url(url, "state-1") == "abc123"
    assert _code_from_url(url, "other") is None


def test_extract_primary_account() -> None:
    payload = {
        "status": 100,
        "user:getAccounts": {
            "data": {
                "accounts": [
                    {
                        "accountNo": "8949049293",
                        "premiseNo": "2001201124",
                        "msslPremiseNo": "WATER-1",
                    }
                ]
            }
        },
    }
    account = _extract_primary_account(payload)
    assert account == {
        "accountNo": "8949049293",
        "premiseNo": "2001201124",
        "ebsPremiseNo": None,
        "msslPremiseNo": "WATER-1",
        "premises_id": None,
    }


def test_parse_monthly_usage_realistic_shape() -> None:
    payload = {
        "status": 100,
        "charts:monthly": {
            "data": [
                {
                    "month": "2026-04-01",
                    "electricityTotal": "212.4",
                    "waterTotal": "9.1",
                }
            ]
        },
    }
    electricity, water = _parse_monthly_usage(payload, datetime(2026, 4, 11))
    assert electricity == pytest.approx(212.4)
    assert water == pytest.approx(9.1)


def test_parse_daily_usage_realistic_shape() -> None:
    payload = {
        "status": 100,
        "charts:hourly": {
            "data": [
                {
                    "date": "2026-04-11",
                    "electricityConsumption": "7.2",
                    "waterConsumption": "0.41",
                }
            ]
        },
    }
    electricity, water = _parse_daily_usage(payload, datetime(2026, 4, 11))
    assert electricity == pytest.approx(7.2)
    assert water is None


def test_parse_daily_usage_status_150_returns_none() -> None:
    electricity, water = _parse_daily_usage({"status": 150}, datetime(2026, 4, 11))
    assert electricity is None
    assert water is None


def test_parse_monthly_csv() -> None:
    csv_text = (
        '"Electricity"\n'
        '"Period","Status","Current"\n'
        '"2026-04-01","Actual","205.1"\n'
        "\n"
        '"Gas"\n'
        '"No data available"\n'
        "\n"
        '"Water"\n'
        '"Period","Status","Current"\n'
        '"2026-04-01","Estimated","8.7"\n'
    )
    electricity, water, electricity_last_month, water_last_month = _parse_monthly_csv(
        csv_text, datetime(2026, 4, 11)
    )
    assert electricity == pytest.approx(205.1)
    assert water == pytest.approx(8.7)
    assert electricity_last_month is None
    assert water_last_month is None


def test_parse_monthly_csv_with_current_and_last_month() -> None:
    csv_text = (
        '"Electricity"\n'
        '"Period","Status","Current"\n'
        '"2026-03-01","Actual","205.1"\n'
        '"2026-04-01","Actual","99.9"\n'
        "\n"
        '"Water"\n'
        '"Period","Status","Current"\n'
        '"2026-03-01","Estimated","8.7"\n'
        '"2026-04-01","Actual","4.2"\n'
    )
    electricity, water, electricity_last_month, water_last_month = _parse_monthly_csv(
        csv_text, datetime(2026, 4, 11)
    )
    assert electricity == pytest.approx(99.9)
    assert water == pytest.approx(4.2)
    assert electricity_last_month == pytest.approx(205.1)
    assert water_last_month == pytest.approx(8.7)


def test_parse_daily_csv() -> None:
    csv_text = (
        '"Period","Current"\n'
        '"2026-04-11 00:00:00","1.2"\n'
        '"2026-04-11 00:30:00","-0.2"\n'
        '"2026-04-11 01:00:00","2.0"\n'
        '"2026-04-10 23:30:00","5.0"\n'
    )
    electricity, water = _parse_daily_csv(csv_text, datetime(2026, 4, 11))
    assert electricity == pytest.approx(3.0)
    assert water is None


def test_parse_titled_csv_sections() -> None:
    csv_text = (
        '"Electricity"\n'
        '"Period","Status","Current"\n'
        '"2026-04-01","Actual","205.1"\n'
        "\n"
        '"Water"\n'
        '"Period","Status","Current"\n'
        '"2026-04-01","Estimated","8.7"\n'
    )
    sections = _parse_titled_csv_sections(csv_text)
    assert sections["electricity"][0]["Current"] == "205.1"
    assert sections["water"][0]["Status"] == "Estimated"


async def test_client_login_success() -> None:
    client = SpServicesClient()
    authorize_html = '<input type="hidden" name="_csrf" value="csrf-123">'
    client._session.get = AsyncMock(
        return_value=_make_response(200, text=authorize_html)
    )
    client._session.post = AsyncMock(
        side_effect=[
            _make_response(200, {"required": False}),
            _make_response(200, {}),
            _make_response(201, {}),
            _make_response(
                200,
                {
                    "id": "txn_123",
                    "state": "pending",
                    "enrollment": {"phone_number": "XXXXXXX2081"},
                },
            ),
            _make_response(200, {}),
        ]
    )

    result = await client.login("user@example.com", "secret")

    assert result["phone_number"] == "XXXXXXX2081"
    assert result["transaction_id"] == "txn_123"
    assert client._auth_context is not None
    assert client._auth_context.csrf == "csrf-123"


async def test_client_login_invalid_credentials() -> None:
    client = SpServicesClient()
    authorize_html = '<input type="hidden" name="_csrf" value="csrf-123">'
    client._session.get = AsyncMock(
        return_value=_make_response(200, text=authorize_html)
    )
    client._session.post = AsyncMock(
        side_effect=[
            _make_response(200, {"required": False}),
            _make_response(401),
        ]
    )

    with pytest.raises(ValueError, match="invalid_auth"):
        await client.login("user@example.com", "wrongpassword")


async def test_client_verify_otp_success() -> None:
    client = SpServicesClient()
    client._auth_context = MagicMock(
        state="state-1",
        code_verifier="verifier-1",
        csrf="csrf-1",
    )
    redirect_resp = _make_response(
        200,
        {},
        url="https://services.spservices.sg/callback?code=code-123&state=state-1",
    )
    token_resp = _make_response(200, {"access_token": "jwt.token.here"})
    client._session.post = AsyncMock(side_effect=[redirect_resp, token_resp])

    token = await client.verify_otp("123456", {"state": "state-1"})
    assert token == "jwt.token.here"


async def test_client_verify_otp_wrong_code() -> None:
    client = SpServicesClient()
    client._auth_context = MagicMock(
        state="state-1", code_verifier="verifier-1", csrf="csrf-1"
    )
    client._session.post = AsyncMock(return_value=_make_response(401))

    with pytest.raises(ValueError, match="invalid_otp"):
        await client.verify_otp("999999", {"state": "state-1"})


async def test_client_fetch_usage_success() -> None:
    client = SpServicesClient()
    now = datetime.now()
    previous_month = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    client._session.post = AsyncMock(
        side_effect=[
            _make_response(
                200,
                {
                    "status": 100,
                    "user:getAccounts": {
                        "data": {
                            "accounts": [
                                {
                                    "accountNo": "8949049293",
                                    "premiseNo": "2001201124",
                                    "msslPremiseNo": "WATER-1",
                                }
                            ]
                        }
                    },
                },
            ),
            _make_response(
                200,
                {
                    "status": 100,
                    "charts:monthly": {
                        "data": [
                            {
                                "month": now.strftime("%Y-%m-01"),
                                "electricityTotal": "212.4",
                                "waterTotal": "9.1",
                            }
                        ]
                    },
                },
            ),
            _make_response(
                200,
                {
                    "status": 100,
                    "charts:hourly": {
                        "data": [
                            {
                                "date": now.date().isoformat(),
                                "electricityConsumption": "7.2",
                                "waterConsumption": "0.41",
                            }
                        ]
                    },
                },
            ),
            _make_response(
                200,
                text=(
                    '"Electricity"\n'
                    '"Period","Status","Current"\n'
                    f'"{previous_month.date().isoformat()}","Actual","199.9"\n'
                    f'"{now.replace(day=1).date().isoformat()}","Actual","212.4"\n'
                    "\n"
                    '"Water"\n'
                    '"Period","Status","Current"\n'
                    f'"{previous_month.date().isoformat()}","Actual","8.8"\n'
                    f'"{now.replace(day=1).date().isoformat()}","Actual","9.1"\n'
                ),
            ),
        ]
    )

    data = await client.fetch_usage("valid-token")
    assert isinstance(data, SpServicesData)
    assert data.account_no == "8949049293"
    assert data.electricity_today_kwh == pytest.approx(7.2)
    assert data.electricity_month_kwh == pytest.approx(212.4)
    assert data.electricity_last_month_kwh == pytest.approx(199.9)
    assert data.water_today_m3 is None
    assert data.water_month_m3 == pytest.approx(9.1)
    assert data.water_last_month_m3 == pytest.approx(8.8)


async def test_client_fetch_usage_401() -> None:
    client = SpServicesClient()
    client._session.post = AsyncMock(return_value=_make_response(401))

    with pytest.raises(ConfigEntryAuthFailed):
        await client.fetch_usage("expired-token")


def _make_entry(token: str | None = "tok") -> MagicMock:
    entry = MagicMock()
    entry.data = {"sp_token": token} if token else {}
    return entry


async def test_coordinator_no_token_raises_auth_failed() -> None:
    hass = MagicMock()
    entry = _make_entry(token=None)
    coord = SpServicesCoordinator(hass, entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_coordinator_delegates_to_client() -> None:
    hass = MagicMock()
    entry = _make_entry(token="my-token")
    coord = SpServicesCoordinator(hass, entry)

    expected = SpServicesData(
        electricity_today_kwh=10.0,
        electricity_month_kwh=200.0,
        water_today_m3=None,
        water_month_m3=8.0,
        account_no="ACC-001",
        last_updated=datetime.now(),
        electricity_last_month_kwh=180.0,
        water_last_month_m3=7.4,
    )
    coord.client.fetch_usage = AsyncMock(return_value=expected)

    result = await coord._async_update_data()
    coord.client.fetch_usage.assert_called_once_with("my-token")
    assert result is expected
