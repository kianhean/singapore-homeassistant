"""Async client for SP Services household electricity and water usage.

This is an aiohttp port of https://github.com/kianhean/sp_api (``sp-services``),
vendored because that library is not published on PyPI and uses blocking
``requests`` calls, which Home Assistant cannot run on the event loop.

Only the browser-assisted Auth0 login is supported: SP enforces a captcha on the
direct ``usernamepassword/login`` endpoint, so a headless username/password flow
is not reliably usable. The config flow hands the user an authorize URL, they
complete login/captcha/OTP in a browser, and paste the callback URL back.

The endpoints are private SP web APIs with no compatibility guarantee. Parsing
gaps degrade to ``None`` fields rather than failing the whole fetch.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import logging
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import aiohttp

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://services.spservices.sg"
_AUTH0_BASE_URL = "https://identity.spdigital.auth0.com"
_SKALBOX_BASE_URL = "https://c-api-gateway.tkg.spdigital.io/skalbox"

_AUTH0_AUTHORIZE_URL = f"{_AUTH0_BASE_URL}/authorize"
_AUTH0_TOKEN_URL = f"{_AUTH0_BASE_URL}/oauth/token"

_SKALBOX_API_URL = f"{_SKALBOX_BASE_URL}/api"
_SKALBOX_DAILY_CSV_URL = f"{_SKALBOX_BASE_URL}/private/charts/csv/dailyHourly"
_SKALBOX_MONTHLY_CSV_URL = f"{_SKALBOX_BASE_URL}/private/charts/csv/monthly"

_AUTH0_CLIENT_ID = "0I6XpXThehIU3SgaSbzraCgekkHg2rJH"
_AUTH0_REDIRECT_URI = f"{_BASE_URL}/callback?fromLogin=true"
_AUTH0_SCOPE = "openid profile email offline_access"
_AUTH0_AUDIENCE = "https://profile.up.spdigital.sg/"

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-SG,en;q=0.9",
    "Origin": _BASE_URL,
    "Referer": f"{_BASE_URL}/",
    "Content-Type": "application/json",
}

_HTML_HEADERS: dict[str, str] = {
    **{key: value for key, value in _HEADERS.items() if key != "Content-Type"},
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Auth0 keeps its tenant session in these cookies; `auth0` is the session
# itself, the `did` pair identifies the device for remembered MFA.
_SESSION_COOKIE_NAMES = ("auth0", "auth0_compat", "did", "did_compat")

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

SP_TIMEZONE = ZoneInfo("Asia/Singapore")

_KNOWN_CSV_SECTIONS = frozenset({"electricity", "water", "gas"})

_PERIOD_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m",
    "%Y/%m",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%Y",
    "%m-%Y",
    "%b %Y",
    "%B %Y",
    "%b-%Y",
    "%b-%y",
    "%Y%m%d",
    "%Y%m",
)

_MONTH_MARKER_KEYS = ("month", "label", "date", "period")
_DAY_MARKER_KEYS = ("date", "day", "label", "period")


class SPUsageError(Exception):
    """Base exception for the SP Services usage client."""


class SPUsageAuthError(SPUsageError):
    """Raised when a login or token exchange fails."""


class SPUsageSessionExpired(SPUsageAuthError):
    """Raised when the access/refresh token is no longer accepted by SP."""


class SPUsageApiError(SPUsageError):
    """Raised when SP returns an unexpected response."""


@dataclass(slots=True)
class UsagePoint:
    """Single historical usage data point.

    ``value`` is ``None`` when SP published the period (usually with a
    ``status`` such as ``Estimated``) but no consumption figure for it.
    """

    period: str
    value: float | None
    status: str | None = None


@dataclass(slots=True)
class UsageData:
    """Household utility consumption fetched from SP Services."""

    account_no: str | None
    last_updated: datetime
    electricity_today_kwh: float | None = None
    electricity_month_kwh: float | None = None
    electricity_last_month_kwh: float | None = None
    water_month_m3: float | None = None
    water_last_month_m3: float | None = None
    electricity_monthly_history: list[UsagePoint] = field(default_factory=list)
    water_monthly_history: list[UsagePoint] = field(default_factory=list)
    electricity_daily_history: list[UsagePoint] = field(default_factory=list)
    electricity_hourly_history: list[UsagePoint] = field(default_factory=list)


@dataclass(slots=True)
class TokenSet:
    """OAuth tokens returned by the Auth0 token endpoint.

    ``refresh_token`` is only present when SP/Auth0 honours the
    ``offline_access`` scope; without one the only way back after expiry is
    another interactive browser login (i.e. a Home Assistant reauth flow).
    """

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str | None = None

    def is_expired(self, *, leeway_seconds: int = 60) -> bool:
        """Whether the access token is expired or about to expire."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(
            seconds=leeway_seconds
        )


@dataclass(slots=True)
class LoginSession:
    """Ephemeral PKCE state for one browser-assisted login attempt."""

    state: str
    nonce: str
    code_verifier: str

    @classmethod
    def create(cls) -> LoginSession:
        return cls(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(48),
        )

    def _authorize_params(self) -> dict[str, str]:
        digest = hashlib.sha256(self.code_verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return {
            "client_id": _AUTH0_CLIENT_ID,
            "redirect_uri": _AUTH0_REDIRECT_URI,
            "response_type": "code",
            "scope": _AUTH0_SCOPE,
            "audience": _AUTH0_AUDIENCE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": self.nonce,
            "state": self.state,
        }

    @property
    def authorize_url(self) -> str:
        """Auth0 URL the user opens in a browser to log in."""
        return f"{_AUTH0_AUTHORIZE_URL}?{urlencode(self._authorize_params())}"

    @property
    def silent_authorize_url(self) -> str:
        """Authorize URL for a non-interactive renewal (``prompt=none``).

        Auth0 answers this with an authorization code when the tenant session
        cookie sent alongside it is still valid, and with ``login_required``
        when it is not. This is how SP's own web portal stays signed in.
        """
        return (
            f"{_AUTH0_AUTHORIZE_URL}?"
            f"{urlencode({**self._authorize_params(), 'prompt': 'none'})}"
        )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def async_exchange_callback_url(
    session: aiohttp.ClientSession, login: LoginSession, callback_url: str
) -> TokenSet:
    """Exchange a pasted Auth0 callback URL for a token set."""
    code = _code_from_url(callback_url.strip(), login.state)
    if not code:
        raise SPUsageAuthError(
            "callback URL did not contain a code matching the expected state"
        )
    return await _async_exchange_code(session, code, login.code_verifier)


async def async_renew_with_session_cookie(
    session: aiohttp.ClientSession, cookie: str
) -> tuple[TokenSet, str]:
    """Mint a fresh token set from a stored Auth0 session cookie.

    SP does not hand every account a refresh token, but its portal keeps itself
    signed in through the Auth0 tenant session instead. Replaying that session
    cookie against ``prompt=none`` gives the same unattended renewal, and
    returns the (possibly rotated) cookie to store for next time.
    """
    if not cookie:
        raise SPUsageAuthError("no SP session cookie stored")

    login = LoginSession.create()
    async with session.get(
        login.silent_authorize_url,
        headers={**_HTML_HEADERS, "Cookie": cookie},
        allow_redirects=False,
        timeout=_REQUEST_TIMEOUT,
    ) as response:
        location = response.headers.get("Location", "")
        set_cookies = _response_set_cookies(response)
        status = response.status

    if status not in (301, 302, 303, 307, 308) or not location:
        raise SPUsageSessionExpired(
            f"SP Services did not return a renewal redirect (HTTP {status}); "
            "the stored session cookie is no longer usable"
        )

    code = _code_from_url(location, login.state)
    if not code:
        raise SPUsageSessionExpired(
            "SP Services rejected the stored session cookie "
            f"({_error_from_url(location) or 'no authorization code returned'})"
        )

    token = await _async_exchange_code(session, code, login.code_verifier)
    return token, _merged_session_cookie(cookie, set_cookies)


async def _async_exchange_code(
    session: aiohttp.ClientSession, code: str, code_verifier: str
) -> TokenSet:
    body = await _post_json(
        session,
        _AUTH0_TOKEN_URL,
        {
            "client_id": _AUTH0_CLIENT_ID,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _AUTH0_REDIRECT_URI,
        },
    )
    return _token_set_from_body(body)


async def async_refresh_token(
    session: aiohttp.ClientSession, refresh_token: str
) -> TokenSet:
    """Exchange a refresh token for a fresh access token."""
    if not refresh_token:
        raise SPUsageAuthError("no refresh token available; re-authentication required")

    body = await _post_json(
        session,
        _AUTH0_TOKEN_URL,
        {
            "client_id": _AUTH0_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    # Auth0 omits refresh_token on refresh unless rotation is enabled, so keep
    # the existing one rather than dropping it.
    return _token_set_from_body(body, fallback_refresh_token=refresh_token)


def _token_set_from_body(
    body: dict[str, Any], fallback_refresh_token: str | None = None
) -> TokenSet:
    access_token = body.get("access_token")
    if not access_token:
        raise SPUsageAuthError("SP Services token exchange returned no access token")

    expires_in = _coerce_float(body.get("expires_in"))
    refresh_token = body.get("refresh_token") or fallback_refresh_token
    scope = body.get("scope")
    if not refresh_token:
        # SP's Auth0 tenant drops `offline_access` for some accounts; log what
        # it did grant so the difference is diagnosable from the HA log.
        _LOGGER.debug(
            "SP Services issued no refresh token (granted scope: %s)",
            scope or "unknown",
        )
    return TokenSet(
        access_token=str(access_token),
        refresh_token=str(refresh_token) if refresh_token else None,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            if expires_in is not None
            else None
        ),
        scope=str(scope) if scope else None,
    )


# ---------------------------------------------------------------------------
# Usage data
# ---------------------------------------------------------------------------


async def async_list_accounts(
    session: aiohttp.ClientSession, access_token: str
) -> list[dict[str, Any]]:
    """List the utility accounts linked to the logged-in SP user."""
    payload = await _skalbox_call(
        session, {"user:getAccounts": {"token": access_token}}, access_token
    )
    return _extract_accounts(payload)


async def async_fetch_usage(
    session: aiohttp.ClientSession,
    access_token: str,
    account_no: str | None = None,
    now: datetime | None = None,
) -> UsageData:
    """Fetch today's and this month's electricity and water usage.

    ``account_no`` selects one of several linked accounts; the first account SP
    returns is used when it is omitted.
    """
    accounts = await async_list_accounts(session, access_token)
    account = _select_account(accounts, account_no)
    if account is None:
        if account_no:
            raise SPUsageApiError(
                f"SP Services returned no utility account matching {account_no}"
            )
        raise SPUsageApiError("SP Services returned no linked utility accounts")

    account_no = str(account.get("accountNo") or "")
    ebs_premise_no = str(
        account.get("premiseNo")
        or account.get("ebsPremiseNo")
        or account.get("premises_id")
        or ""
    )
    mssl_premise_no = str(account.get("msslPremiseNo") or "")
    if not account_no or not ebs_premise_no:
        raise SPUsageApiError("SP Services account payload is missing premise details")

    now = now or datetime.now(SP_TIMEZONE)

    monthly_payload = await _skalbox_call(
        session,
        {
            "charts:monthly": {
                "ebsPremiseNo": ebs_premise_no,
                "msslPremiseNo": mssl_premise_no,
                "consumptionValue": str(now.year),
            }
        },
        access_token,
    )
    hourly_payload = await _skalbox_call(
        session,
        {"charts:hourly": {"accountNos": [account_no], "date": now.date().isoformat()}},
        access_token,
        allow_business_no_data=True,
    )

    electricity_month, water_month = _parse_monthly_usage(monthly_payload, now)
    electricity_today = _parse_daily_usage(hourly_payload, now)

    monthly_csv = await _post_text_optional(
        session,
        _SKALBOX_MONTHLY_CSV_URL,
        {
            "ebsPremiseNo": ebs_premise_no,
            "msslPremiseNo": mssl_premise_no,
            "accountNo": account_no,
        },
        access_token,
    )
    monthly_sections = _parse_titled_csv_sections(monthly_csv)
    (
        csv_elec_month,
        csv_water_month,
        electricity_last_month_kwh,
        water_last_month_m3,
    ) = _monthly_summary_from_sections(monthly_sections, now)
    if electricity_month is None:
        electricity_month = csv_elec_month
    if water_month is None:
        water_month = csv_water_month

    hourly_csv = await _post_text_optional(
        session,
        _SKALBOX_DAILY_CSV_URL,
        {"accountNos": [account_no], "consumptionBy": "hourCSV"},
        access_token,
    )
    electricity_hourly_history = _parse_hourly_history(hourly_csv)
    if electricity_today is None:
        electricity_today = _parse_daily_csv(hourly_csv, now)

    daily_csv = await _post_text_optional(
        session,
        _SKALBOX_DAILY_CSV_URL,
        {"accountNos": [account_no], "consumptionBy": "dayCSV"},
        access_token,
    )
    electricity_daily_history = _parse_daily_history_csv(daily_csv, now)
    if electricity_today is None:
        electricity_today = _latest_value_for_day(electricity_daily_history, now)

    return UsageData(
        account_no=account_no,
        last_updated=now,
        electricity_today_kwh=electricity_today,
        electricity_month_kwh=electricity_month,
        electricity_last_month_kwh=electricity_last_month_kwh,
        # SP does not publish same-day water usage in any observed export.
        water_month_m3=water_month,
        water_last_month_m3=water_last_month_m3,
        electricity_monthly_history=_section_history_points(
            monthly_sections.get("electricity", [])
        ),
        water_monthly_history=_section_history_points(
            monthly_sections.get("water", [])
        ),
        electricity_daily_history=electricity_daily_history,
        electricity_hourly_history=electricity_hourly_history,
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _api_headers(token: str | None) -> dict[str, str]:
    headers = dict(_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _post_json(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    token: str | None = None,
) -> dict[str, Any]:
    async with session.post(
        url,
        json=payload,
        headers=_api_headers(token),
        timeout=_REQUEST_TIMEOUT,
    ) as response:
        if response.status == 401:
            raise SPUsageSessionExpired("SP Services rejected the token (HTTP 401)")
        if response.status >= 400:
            raise SPUsageApiError(f"SP Services returned HTTP {response.status}")
        body = await response.json(content_type=None)

    if isinstance(body, dict):
        return body
    raise SPUsageApiError("SP Services returned an unexpected non-object response")


async def _skalbox_call(
    session: aiohttp.ClientSession,
    payload: dict[str, Any],
    token: str,
    allow_business_no_data: bool = False,
) -> dict[str, Any]:
    body = await _post_json(session, _SKALBOX_API_URL, payload, token=token)
    status = body.get("status")
    if status is None or status == 100:
        return body
    # 150 means "no data for this period", which is normal early in the day.
    if allow_business_no_data and status == 150:
        return body
    raise SPUsageApiError(f"SP Services API returned unexpected status {status}")


async def _post_text_optional(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    token: str,
) -> str:
    """Fetch a CSV export, degrading to empty text when it is unavailable.

    Expired sessions still propagate; only upstream export failures are treated
    as missing data so one flaky endpoint cannot fail the whole usage fetch.
    """
    try:
        async with session.post(
            url,
            json=payload,
            headers=_api_headers(token),
            timeout=_REQUEST_TIMEOUT,
        ) as response:
            if response.status == 401:
                raise SPUsageSessionExpired("SP Services rejected the token (HTTP 401)")
            if response.status >= 400:
                _LOGGER.debug(
                    "SP Services CSV export %s returned HTTP %s", url, response.status
                )
                return ""
            return await response.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.debug("SP Services CSV export %s failed: %s", url, err)
        return ""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def normalize_session_cookie(raw: str) -> str:
    """Turn whatever the user pasted into a Cookie header value.

    Accepts a full cookie header, a devtools copy of several cookies, or just
    the bare `auth0` value, and keeps only the cookies Auth0 needs so an
    unrelated pasted cookie is not stored or replayed.
    """
    text = raw.strip().strip(";").strip()
    if not text:
        return ""
    if "=" not in text:
        return f"auth0={text}"

    pairs: list[str] = []
    for part in text.replace("\n", ";").split(";"):
        candidate = part.strip()
        if not candidate or "=" not in candidate:
            continue
        name = candidate.split("=", 1)[0].strip()
        if name in _SESSION_COOKIE_NAMES:
            pairs.append(f"{name}={candidate.split('=', 1)[1].strip()}")
    return "; ".join(pairs)


def _response_set_cookies(response: Any) -> list[str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return []
    getall = getattr(headers, "getall", None)
    if getall is not None:
        return list(getall("Set-Cookie", []))
    value = headers.get("Set-Cookie")
    return [value] if value else []


def _merged_session_cookie(current: str, set_cookie_headers: list[str]) -> str:
    """Apply any rotated session cookies Auth0 returned to the stored value.

    Auth0 sessions are rolling: each renewal can hand back a new cookie, and
    storing it is what keeps the session alive past its inactivity window.
    """
    cookies: dict[str, str] = {}
    for pair in current.split(";"):
        candidate = pair.strip()
        if "=" in candidate:
            name, value = candidate.split("=", 1)
            cookies[name.strip()] = value.strip()

    for header in set_cookie_headers:
        first = str(header).split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, value = first.split("=", 1)
        name = name.strip()
        if name in _SESSION_COOKIE_NAMES and value.strip():
            cookies[name] = value.strip()

    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def _error_from_url(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    error = query.get("error", [None])[0]
    if not error:
        return None
    description = query.get("error_description", [None])[0]
    return f"{error}: {description}" if description else error


def _code_from_url(url: str, expected_state: str) -> str | None:
    """Extract an authorization code, requiring the OAuth state to match.

    A missing state is rejected: accepting it would discard the CSRF binding
    that state exists to provide.
    """
    if not url or not expected_state:
        return None
    query = parse_qs(urlparse(url).query)
    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]
    if code and state and secrets.compare_digest(state, expected_state):
        return code
    return None


def _extract_accounts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect every linked utility account, in the order SP returned them."""
    accounts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _walk_nodes(payload):
        if not isinstance(item, dict):
            continue
        account_no = item.get("accountNo") or item.get("accountNumber")
        if not account_no or str(account_no) in seen:
            continue
        seen.add(str(account_no))
        accounts.append(
            {
                "accountNo": account_no,
                "premiseNo": item.get("premiseNo") or item.get("premiseNumber"),
                "ebsPremiseNo": item.get("ebsPremiseNo"),
                "msslPremiseNo": item.get("msslPremiseNo"),
                "premises_id": item.get("premises_id"),
            }
        )
    return accounts


def _select_account(
    accounts: list[dict[str, Any]], account_no: str | None
) -> dict[str, Any] | None:
    if account_no is None:
        return accounts[0] if accounts else None
    for account in accounts:
        if str(account.get("accountNo")) == str(account_no):
            return account
    return None


def _parse_monthly_usage(
    payload: dict[str, Any], now: datetime
) -> tuple[float | None, float | None]:
    electricity: float | None = None
    water: float | None = None

    for path, item in _walk_with_paths(payload):
        if not isinstance(item, dict):
            continue
        if _item_period_matches(item, _MONTH_MARKER_KEYS, _month_matcher(now)) is False:
            continue
        if electricity is None:
            electricity = _extract_metric_value(item, path, "electricity")
        if water is None:
            water = _extract_metric_value(item, path, "water")
        if electricity is not None and water is not None:
            break

    return electricity, water


def _parse_daily_usage(payload: dict[str, Any], now: datetime) -> float | None:
    if payload.get("status") == 150:
        return None

    matcher = _day_matcher(now)
    for path, item in _walk_with_paths(payload):
        if not isinstance(item, dict):
            continue
        if _item_period_matches(item, _DAY_MARKER_KEYS, matcher) is False:
            continue
        electricity = _extract_metric_value(item, path, "electricity")
        if electricity is not None:
            return electricity

    return None


def _month_matcher(now: datetime) -> Callable[[str], bool]:
    def matches(value: str) -> bool:
        return _period_matches_month(value, now.year, now.month)

    return matches


def _day_matcher(now: datetime) -> Callable[[str], bool]:
    target = now.date()

    def matches(value: str) -> bool:
        return _period_matches_day(value, target)

    return matches


def _item_period_matches(
    item: dict[str, Any],
    marker_keys: tuple[str, ...],
    matcher: Callable[[str], bool],
) -> bool | None:
    """Whether an item's period labels match the wanted period.

    Returns ``None`` when the item carries no period-ish field at all, so
    callers can still consider payload shapes that omit period labels.
    """
    values = [str(item.get(key, "")).strip() for key in marker_keys]
    values = [value for value in values if value]
    if not values:
        return None
    return any(matcher(value) for value in values)


def _extract_metric_value(
    item: dict[str, Any], path: list[str], utility: str
) -> float | None:
    utility_tokens = (
        ("electric", "energy", "kwh", "ebs")
        if utility == "electricity"
        else ("water", "m3", "mssl")
    )
    metric_tokens = ("total", "usage", "consumption", "value", "amount")

    for key, value in item.items():
        if not isinstance(value, (str, int, float)):
            continue
        normalized_key = key.lower()
        if any(token in normalized_key for token in utility_tokens) and any(
            token in normalized_key for token in metric_tokens
        ):
            return _coerce_float(value)

    joined_path = " ".join(path).lower()
    if any(token in joined_path for token in utility_tokens):
        for key in metric_tokens:
            if key in item:
                return _coerce_float(item[key])

    return None


def _monthly_summary_from_sections(
    sections: dict[str, list[dict[str, str]]], now: datetime
) -> tuple[float | None, float | None, float | None, float | None]:
    previous_month = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    electricity_rows = sections.get("electricity", [])
    water_rows = sections.get("water", [])

    return (
        _section_value_for_month(electricity_rows, now.year, now.month),
        _section_value_for_month(water_rows, now.year, now.month),
        _section_value_for_month(
            electricity_rows, previous_month.year, previous_month.month
        ),
        _section_value_for_month(water_rows, previous_month.year, previous_month.month),
    )


def _parse_titled_csv_sections(csv_text: str) -> dict[str, list[dict[str, str]]]:
    sections: dict[str, list[dict[str, str]]] = {}
    current_section: str | None = None
    current_headers: list[str] | None = None

    for raw_line in csv_text.splitlines():
        line = raw_line.strip()
        if not line:
            current_headers = None
            continue

        parsed = next(csv.reader([line]))
        if len(parsed) == 1:
            title = parsed[0].strip().lower()
            if title in _KNOWN_CSV_SECTIONS:
                current_section = title
                sections.setdefault(current_section, [])
                current_headers = None
            elif current_headers is None:
                # An unrecognized heading starts a section we cannot classify.
                # Leave the previous section rather than silently appending the
                # new section's rows to it.
                current_section = None
            continue

        if current_section is None:
            continue

        if current_headers is None:
            current_headers = [header.strip() for header in parsed]
            continue

        if len(parsed) != len(current_headers):
            continue
        sections[current_section].append(
            dict(zip(current_headers, parsed, strict=False))
        )

    return sections


def _csv_rows(csv_text: str) -> list[dict[str, str]]:
    if not csv_text.strip():
        return []
    return list(csv.DictReader(io.StringIO(csv_text)))


def _row_period(row: dict[str, str]) -> str:
    return str(row.get("Period", "")).strip().strip('"')


def _parse_hourly_history(csv_text: str) -> list[UsagePoint]:
    points: list[UsagePoint] = []
    for row in _csv_rows(csv_text):
        period = _row_period(row)
        value = _coerce_float(row.get("Current"))
        if period and value is not None:
            points.append(UsagePoint(period=period, value=value))
    return points


def _parse_daily_history_csv(
    csv_text: str, now: datetime | None = None
) -> list[UsagePoint]:
    points: list[UsagePoint] = []
    max_day = now.date().isoformat() if now else None
    for row in _csv_rows(csv_text):
        period = _row_period(row)
        value = _coerce_float(row.get("Current"))
        # SP pads the export with future days; drop anything past today.
        if max_day and period and len(period) == 10 and period > max_day:
            continue
        if period and value is not None:
            points.append(UsagePoint(period=period, value=value))
    return points


def _parse_daily_csv(csv_text: str, now: datetime) -> float | None:
    """Sum the half-hourly rows belonging to today."""
    target_day = now.date().isoformat()
    values: list[float] = []
    for row in _csv_rows(csv_text):
        if not _row_period(row).startswith(target_day):
            continue
        value = _coerce_float(row.get("Current"))
        if value is not None:
            values.append(value)

    return sum(values) if values else None


def _section_value_for_period(rows: list[dict[str, str]], period: str) -> float | None:
    for row in rows:
        if _row_period(row) == period:
            return _coerce_float(row.get("Current"))
    return None


def _section_value_for_month(
    rows: list[dict[str, str]], year: int, month: int
) -> float | None:
    exact = _section_value_for_period(rows, datetime(year, month, 1).date().isoformat())
    if exact is not None:
        return exact

    # Periods are compared as dates, not as strings: a mixed export containing
    # both '2026-04-28' and 'Apr 2026' would otherwise sort by ASCII and pick
    # the text label over the more recent row.
    dated: list[tuple[date, float]] = []
    undated: list[float] = []
    for row in rows:
        period = _row_period(row)
        if not _period_matches_month(period, year, month):
            continue
        value = _coerce_float(row.get("Current"))
        if value is None:
            continue
        parsed = _parse_period_date(period)
        if parsed is not None:
            dated.append((parsed, value))
        else:
            undated.append(value)

    if dated:
        dated.sort(key=lambda item: item[0])
        return dated[-1][1]
    return undated[-1] if undated else None


def _section_history_points(rows: list[dict[str, str]]) -> list[UsagePoint]:
    points: list[UsagePoint] = []
    for row in rows:
        period = _row_period(row)
        value = _coerce_float(row.get("Current"))
        status = str(row.get("Status", "")).strip().strip('"') or None
        # Keep periods SP published without a figure as long as they carry a
        # status, so an unpublished month is visible rather than missing.
        if period and (value is not None or status is not None):
            points.append(UsagePoint(period=period, value=value, status=status))
    return points


def _latest_value_for_day(points: list[UsagePoint], now: datetime) -> float | None:
    target_day = now.date().isoformat()
    for point in reversed(points):
        if point.period == target_day:
            return point.value
    return None


def _parse_period_date(period: str) -> date | None:
    """Parse an SP period label into a date, or ``None`` if the shape is new."""
    text = period.strip().strip('"')
    if not text:
        return None
    for fmt in _PERIOD_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _period_matches_day(period: str, target: date) -> bool:
    normalized = period.strip().strip('"').lower()
    if not normalized:
        return False

    patterns = (
        target.isoformat(),
        target.strftime("%d/%m/%Y"),
        target.strftime("%d-%m-%Y"),
        target.strftime("%Y%m%d"),
    )
    return any(pattern in normalized for pattern in patterns)


def _period_matches_month(period: str, year: int, month: int) -> bool:
    lowered = period.strip().strip('"').lower()
    month_num = f"{month:02d}"
    month_short = datetime(year, month, 1).strftime("%b").lower()
    month_long = datetime(year, month, 1).strftime("%B").lower()
    year_short = str(year)[-2:]

    patterns = (
        f"{year}-{month_num}",
        f"{year}/{month_num}",
        f"{month_num}/{year}",
        f"{month_num}-{year}",
        f"{year}{month_num}",
        f"{month_short}-{year_short}",
        f"{month_short}-{year}",
        f"{month_short} {year}",
        f"{month_long} {year}",
    )
    return any(pattern in lowered for pattern in patterns)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _walk_nodes(node: Any) -> Iterator[Any]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_nodes(item)


def _walk_with_paths(
    node: Any, path: list[str] | None = None
) -> Iterator[tuple[list[str], Any]]:
    path = path or []
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_with_paths(value, [*path, str(key)])
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _walk_with_paths(value, [*path, str(idx)])
