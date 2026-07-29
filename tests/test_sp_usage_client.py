"""Tests for the vendored SP Services usage client."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest

from custom_components.singapore.sp_usage_client import (
    SP_TIMEZONE,
    LoginSession,
    SPUsageApiError,
    SPUsageAuthError,
    SPUsageSessionExpired,
    TokenSet,
    UsagePoint,
    _code_from_url,
    _extract_accounts,
    _monthly_summary_from_sections,
    _parse_daily_csv,
    _parse_daily_history_csv,
    _parse_daily_usage,
    _parse_hourly_history,
    _parse_monthly_usage,
    _parse_titled_csv_sections,
    _section_history_points,
    _select_account,
    _token_set_from_body,
    async_exchange_callback_url,
    async_fetch_usage,
    async_refresh_token,
)

_NOW = datetime(2026, 4, 12, 16, 42, tzinfo=SP_TIMEZONE)

_MONTHLY_CSV = """Electricity

Period,Current,Status
2026-03-01,572.0,Actual
2026-04-01,120.5,Estimated

Water

Period,Current,Status
2026-03-01,34.1,Estimated
"""

_HOURLY_CSV = """Period,Current
2026-04-12 00:00:00,1.452
2026-04-12 00:30:00,1.356
2026-04-11 23:30:00,2.0
"""

_DAILY_CSV = """Period,Current
2026-04-11,15.265
2026-04-12,19.967
2026-04-13,0
"""

_ACCOUNTS_PAYLOAD = {
    "status": 100,
    "data": {
        "accounts": [
            {
                "accountNo": "8949049293",
                "premiseNo": "1234567890",
                "msslPremiseNo": "987654",
            }
        ]
    },
}


# ---------------------------------------------------------------------------
# Login helpers
# ---------------------------------------------------------------------------


def test_authorize_url_contains_pkce_and_state():
    login = LoginSession.create()
    query = parse_qs(urlparse(login.authorize_url).query)

    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [login.state]
    assert query["nonce"] == [login.nonce]
    assert "offline_access" in query["scope"][0]
    # The verifier itself must never leave the integration.
    assert login.code_verifier not in login.authorize_url


def test_login_sessions_are_unique():
    assert LoginSession.create().state != LoginSession.create().state


def test_code_from_url_requires_matching_state():
    url = "https://services.spservices.sg/callback?fromLogin=true&code=abc&state=xyz"
    assert _code_from_url(url, "xyz") == "abc"
    assert _code_from_url(url, "other") is None


def test_code_from_url_rejects_missing_state():
    url = "https://services.spservices.sg/callback?code=abc"
    assert _code_from_url(url, "xyz") is None


def test_token_set_from_body():
    token = _token_set_from_body(
        {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    )
    assert token.access_token == "at"
    assert token.refresh_token == "rt"
    assert token.expires_at is not None
    assert not token.is_expired()


def test_token_set_keeps_existing_refresh_token():
    token = _token_set_from_body({"access_token": "at"}, fallback_refresh_token="old")
    assert token.refresh_token == "old"
    # No expiry returned means we cannot know it expired.
    assert token.is_expired() is False


def test_token_set_without_access_token_raises():
    with pytest.raises(SPUsageAuthError):
        _token_set_from_body({"refresh_token": "rt"})


def test_token_set_is_expired_uses_leeway():
    token = TokenSet(
        access_token="at",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    assert token.is_expired() is True
    assert token.is_expired(leeway_seconds=0) is False


# ---------------------------------------------------------------------------
# Account selection
# ---------------------------------------------------------------------------


def test_extract_accounts_deduplicates():
    payload = {
        "data": {
            "accounts": [
                {"accountNo": "111", "premiseNo": "p1"},
                {"accountNo": "222", "premiseNo": "p2"},
            ],
            "primary": {"accountNo": "111", "premiseNo": "p1"},
        }
    }
    accounts = _extract_accounts(payload)
    assert [account["accountNo"] for account in accounts] == ["111", "222"]


def test_select_account():
    accounts = [{"accountNo": "111"}, {"accountNo": "222"}]
    assert _select_account(accounts, None)["accountNo"] == "111"
    assert _select_account(accounts, "222")["accountNo"] == "222"
    assert _select_account(accounts, "333") is None
    assert _select_account([], None) is None


# ---------------------------------------------------------------------------
# Payload / CSV parsing
# ---------------------------------------------------------------------------


def test_parse_monthly_usage_from_payload():
    payload = {
        "status": 100,
        "data": {
            "monthly": [
                {"month": "2026-03", "electricityUsage": 572.0, "waterUsage": 34.1},
                {"month": "2026-04", "electricityUsage": 250.5, "waterUsage": 12.3},
            ]
        },
    }
    electricity, water = _parse_monthly_usage(payload, _NOW)
    assert electricity == 250.5
    assert water == 12.3


def test_parse_daily_usage_from_payload():
    payload = {
        "status": 100,
        "data": {"hourly": [{"date": "2026-04-12", "electricityConsumption": 19.967}]},
    }
    assert _parse_daily_usage(payload, _NOW) == 19.967


def test_parse_daily_usage_no_data_status():
    assert _parse_daily_usage({"status": 150}, _NOW) is None


def test_parse_titled_csv_sections():
    sections = _parse_titled_csv_sections(_MONTHLY_CSV)
    assert set(sections) == {"electricity", "water"}
    assert len(sections["electricity"]) == 2
    assert sections["water"][0]["Current"] == "34.1"


def test_parse_titled_csv_sections_ignores_unknown_section():
    sections = _parse_titled_csv_sections("Solar\n\nPeriod,Current\n2026-04-01,1.0\n")
    assert sections == {}


def test_monthly_summary_from_sections():
    sections = _parse_titled_csv_sections(_MONTHLY_CSV)
    elec_month, water_month, elec_last, water_last = _monthly_summary_from_sections(
        sections, _NOW
    )
    assert elec_month == 120.5
    assert elec_last == 572.0
    # SP had not published April water yet.
    assert water_month is None
    assert water_last == 34.1


def test_section_history_points_keeps_status():
    sections = _parse_titled_csv_sections(_MONTHLY_CSV)
    points = _section_history_points(sections["electricity"])
    assert points == [
        UsagePoint(period="2026-03-01", value=572.0, status="Actual"),
        UsagePoint(period="2026-04-01", value=120.5, status="Estimated"),
    ]


def test_section_history_points_keeps_published_period_without_value():
    points = _section_history_points([{"Period": "2026-05-01", "Status": "Estimated"}])
    assert points == [UsagePoint(period="2026-05-01", value=None, status="Estimated")]


def test_parse_hourly_history():
    points = _parse_hourly_history(_HOURLY_CSV)
    assert len(points) == 3
    assert points[0] == UsagePoint(period="2026-04-12 00:00:00", value=1.452)


def test_parse_daily_csv_sums_todays_half_hours():
    assert _parse_daily_csv(_HOURLY_CSV, _NOW) == pytest.approx(2.808)


def test_parse_daily_history_drops_future_days():
    points = _parse_daily_history_csv(_DAILY_CSV, _NOW)
    assert [point.period for point in points] == ["2026-04-11", "2026-04-12"]


def test_parse_empty_csv():
    assert _parse_hourly_history("") == []
    assert _parse_daily_history_csv("", _NOW) == []
    assert _parse_daily_csv("", _NOW) is None
    assert _parse_titled_csv_sections("") == {}


# ---------------------------------------------------------------------------
# HTTP-level tests
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status=200, json_body=None, text_body=""):
        self.status = status
        self._json = json_body
        self._text = text_body

    async def json(self, content_type=None):
        return self._json

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    """Minimal stand-in for aiohttp.ClientSession used by the client."""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append((url, json or {}))
        result = self._handler(url, json or {})
        if isinstance(result, Exception):
            raise result
        return result


def _usage_handler(**overrides):
    def handler(url, payload):
        if url.endswith("/skalbox/api"):
            if "user:getAccounts" in payload:
                return _FakeResponse(
                    json_body=overrides.get("accounts", _ACCOUNTS_PAYLOAD)
                )
            if "charts:monthly" in payload:
                return _FakeResponse(
                    json_body=overrides.get("monthly", {"status": 100, "data": {}})
                )
            return _FakeResponse(
                json_body=overrides.get("hourly", {"status": 150, "data": {}})
            )
        if url.endswith("/csv/monthly"):
            return _FakeResponse(text_body=overrides.get("monthly_csv", _MONTHLY_CSV))
        if payload.get("consumptionBy") == "hourCSV":
            return _FakeResponse(text_body=overrides.get("hourly_csv", _HOURLY_CSV))
        return _FakeResponse(text_body=overrides.get("daily_csv", _DAILY_CSV))

    return handler


@pytest.mark.asyncio
async def test_fetch_usage_falls_back_to_csv_exports():
    session = _FakeSession(_usage_handler())

    usage = await async_fetch_usage(session, "token", now=_NOW)

    assert usage.account_no == "8949049293"
    assert usage.electricity_month_kwh == 120.5
    assert usage.electricity_last_month_kwh == 572.0
    assert usage.water_month_m3 is None
    assert usage.water_last_month_m3 == 34.1
    # No live hourly payload, so today comes from the half-hourly CSV.
    assert usage.electricity_today_kwh == pytest.approx(2.808)
    assert len(usage.electricity_hourly_history) == 3
    assert [point.period for point in usage.electricity_daily_history] == [
        "2026-04-11",
        "2026-04-12",
    ]
    assert usage.last_updated == _NOW


@pytest.mark.asyncio
async def test_fetch_usage_prefers_live_payload_values():
    session = _FakeSession(
        _usage_handler(
            monthly={
                "status": 100,
                "data": {
                    "monthly": [
                        {"month": "2026-04", "electricityUsage": 300.0},
                    ]
                },
            },
            hourly={
                "status": 100,
                "data": {
                    "hourly": [{"date": "2026-04-12", "electricityConsumption": 19.967}]
                },
            },
        )
    )

    usage = await async_fetch_usage(session, "token", now=_NOW)

    assert usage.electricity_month_kwh == 300.0
    assert usage.electricity_today_kwh == 19.967


@pytest.mark.asyncio
async def test_fetch_usage_survives_failing_csv_export():
    def handler(url, payload):
        if url.endswith("/skalbox/api"):
            if "user:getAccounts" in payload:
                return _FakeResponse(json_body=_ACCOUNTS_PAYLOAD)
            return _FakeResponse(json_body={"status": 100, "data": {}})
        return _FakeResponse(status=500)

    usage = await async_fetch_usage(_FakeSession(handler), "token", now=_NOW)

    assert usage.account_no == "8949049293"
    assert usage.electricity_today_kwh is None
    assert usage.electricity_monthly_history == []


@pytest.mark.asyncio
async def test_fetch_usage_selects_requested_account():
    accounts = {
        "status": 100,
        "data": {
            "accounts": [
                {"accountNo": "111", "premiseNo": "p1"},
                {"accountNo": "222", "premiseNo": "p2"},
            ]
        },
    }
    session = _FakeSession(_usage_handler(accounts=accounts))

    usage = await async_fetch_usage(session, "token", account_no="222", now=_NOW)

    assert usage.account_no == "222"


@pytest.mark.asyncio
async def test_fetch_usage_unknown_account_raises():
    session = _FakeSession(_usage_handler())

    with pytest.raises(SPUsageApiError, match="no utility account matching"):
        await async_fetch_usage(session, "token", account_no="999", now=_NOW)


@pytest.mark.asyncio
async def test_fetch_usage_no_accounts_raises():
    session = _FakeSession(_usage_handler(accounts={"status": 100, "data": {}}))

    with pytest.raises(SPUsageApiError, match="no linked utility accounts"):
        await async_fetch_usage(session, "token", now=_NOW)


@pytest.mark.asyncio
async def test_fetch_usage_expired_token_raises_session_expired():
    session = _FakeSession(lambda url, payload: _FakeResponse(status=401))

    with pytest.raises(SPUsageSessionExpired):
        await async_fetch_usage(session, "token", now=_NOW)


@pytest.mark.asyncio
async def test_expired_csv_export_still_propagates_session_expiry():
    def handler(url, payload):
        if url.endswith("/skalbox/api"):
            if "user:getAccounts" in payload:
                return _FakeResponse(json_body=_ACCOUNTS_PAYLOAD)
            return _FakeResponse(json_body={"status": 100, "data": {}})
        return _FakeResponse(status=401)

    with pytest.raises(SPUsageSessionExpired):
        await async_fetch_usage(_FakeSession(handler), "token", now=_NOW)


@pytest.mark.asyncio
async def test_unexpected_business_status_raises():
    session = _FakeSession(
        lambda url, payload: _FakeResponse(json_body={"status": 400, "data": {}})
    )

    with pytest.raises(SPUsageApiError, match="unexpected status"):
        await async_fetch_usage(session, "token", now=_NOW)


@pytest.mark.asyncio
async def test_network_error_propagates():
    session = _FakeSession(lambda url, payload: aiohttp.ClientError("connection reset"))

    with pytest.raises(aiohttp.ClientError):
        await async_fetch_usage(session, "token", now=_NOW)


@pytest.mark.asyncio
async def test_exchange_callback_url():
    login = LoginSession.create()
    session = _FakeSession(
        lambda url, payload: _FakeResponse(
            json_body={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        )
    )

    token = await async_exchange_callback_url(
        session, login, f"https://x/callback?code=abc&state={login.state}"
    )

    assert token.access_token == "at"
    assert token.refresh_token == "rt"
    assert session.requests[0][1]["code"] == "abc"
    assert session.requests[0][1]["code_verifier"] == login.code_verifier


@pytest.mark.asyncio
async def test_exchange_callback_url_state_mismatch():
    login = LoginSession.create()
    session = _FakeSession(lambda url, payload: _FakeResponse(json_body={}))

    with pytest.raises(SPUsageAuthError):
        await async_exchange_callback_url(
            session, login, "https://x/callback?code=abc&state=someone-elses-state"
        )
    assert session.requests == []


@pytest.mark.asyncio
async def test_refresh_token_keeps_unrotated_token():
    session = _FakeSession(
        lambda url, payload: _FakeResponse(
            json_body={"access_token": "new", "expires_in": 3600}
        )
    )

    token = await async_refresh_token(session, "stored-refresh")

    assert token.access_token == "new"
    assert token.refresh_token == "stored-refresh"


@pytest.mark.asyncio
async def test_refresh_token_rejected():
    session = _FakeSession(lambda url, payload: _FakeResponse(status=401))

    with pytest.raises(SPUsageSessionExpired):
        await async_refresh_token(session, "stored-refresh")


@pytest.mark.asyncio
async def test_refresh_token_requires_a_token():
    with pytest.raises(SPUsageAuthError):
        await async_refresh_token(_FakeSession(lambda url, payload: None), "")
