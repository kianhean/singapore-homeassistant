"""Coordinator for SP Services household energy and water usage data.

Authentication flow
-------------------
1. ``SpServicesClient.login(username, password)``
   POSTs credentials to the portal.  On success the server sends an OTP to
   the user's registered mobile number and returns a session payload.

2. ``SpServicesClient.verify_otp(otp, login_response)``
   POSTs the OTP together with the session identifier from step 1.
   On success the server returns an auth token.

3. ``SpServicesClient.fetch_usage(token)``
   GETs electricity and water usage with the token in the Authorization
   header.  Raises ``ConfigEntryAuthFailed`` on HTTP 401 so Home Assistant
   can trigger the reauth flow.

IMPORTANT – endpoint paths
--------------------------
The SP Services portal (https://services.spservices.sg) is a JavaScript
single-page application.  The REST API paths below are *unverified
placeholders* derived from common Angular SPA conventions.

To find the real paths:
1. Open https://services.spservices.sg in Chrome.
2. Open DevTools → Network → filter by "Fetch/XHR".
3. Log in, complete the OTP step, navigate to the usage page.
4. Update the ``_LOGIN_URL``, ``_OTP_VERIFY_URL``, and usage URL constants
   to match the real network requests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import niquests
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

_UPDATE_INTERVAL = timedelta(hours=1)

# ---------------------------------------------------------------------------
# Endpoint constants — update these once real paths are confirmed
# ---------------------------------------------------------------------------
_BASE_URL = "https://services.spservices.sg"

# POST {userId, password} → triggers OTP SMS; returns session payload
_LOGIN_URL = f"{_BASE_URL}/api/account/login"

# POST {otp, sessionId} → returns {"token": "..."} (or similar field)
_OTP_VERIFY_URL = f"{_BASE_URL}/api/account/otp/verify"

# GET → daily electricity usage records
_ELECTRICITY_USAGE_URL = f"{_BASE_URL}/api/usage/electricity"

# GET → daily water usage records
_WATER_USAGE_URL = f"{_BASE_URL}/api/usage/water"

# ---------------------------------------------------------------------------
# HTTP headers mimicking the Angular SPA's own requests
# ---------------------------------------------------------------------------
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
}


@dataclass
class SpServicesData:
    """Household utility consumption data fetched from SP Services."""

    electricity_today_kwh: float | None
    electricity_month_kwh: float | None
    water_today_m3: float | None
    water_month_m3: float | None
    account_no: str | None
    last_updated: datetime


class SpServicesClient:
    """Low-level HTTP client for the SP Services portal.

    Each instance owns a private ``niquests.AsyncSession`` so that
    authentication cookies are completely isolated from other integrations
    or the shared HA aiohttp session.
    """

    def __init__(self) -> None:
        self._session: niquests.AsyncSession = niquests.AsyncSession()

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """Step 1 — submit credentials and trigger OTP delivery.

        Returns the raw JSON response body; pass it unchanged to
        ``verify_otp()`` so the session identifier travels with it.

        Raises:
            ValueError: key ``"invalid_auth"`` when HTTP 401 (bad password).
            UpdateFailed: on any other non-2xx response.
        """
        payload = {"userId": username, "password": password}
        resp = await self._session.post(
            _LOGIN_URL,
            json=payload,
            headers={**_HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 401:
            raise ValueError("invalid_auth")
        if not resp.ok:
            raise UpdateFailed(f"SP Services login returned HTTP {resp.status_code}")
        return resp.json()

    async def verify_otp(self, otp: str, login_response: dict[str, Any]) -> str:
        """Step 2 — submit OTP and retrieve the auth token.

        Args:
            otp: The one-time password typed by the user.
            login_response: The dict returned by ``login()``.

        Returns:
            The auth token string to store in the config entry.

        Raises:
            ValueError: key ``"invalid_otp"`` on HTTP 401 (wrong OTP).
            UpdateFailed: on any other non-2xx response or missing token.
        """
        # The server uses the sessionId returned in the login response to
        # tie the OTP to the correct in-flight login attempt.
        session_id = (
            login_response.get("sessionId")
            or login_response.get("session_id")
            or login_response.get("requestId")
            or ""
        )
        payload = {"otp": otp.strip(), "sessionId": session_id}
        resp = await self._session.post(
            _OTP_VERIFY_URL,
            json=payload,
            headers={**_HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 401:
            raise ValueError("invalid_otp")
        if not resp.ok:
            raise UpdateFailed(
                f"SP Services OTP verify returned HTTP {resp.status_code}"
            )
        body = resp.json()
        token = (
            body.get("token")
            or body.get("access_token")
            or body.get("accessToken")
            or body.get("authToken")
        )
        if not token:
            raise UpdateFailed("SP Services OTP response contained no auth token")
        return token

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    async def fetch_usage(self, token: str) -> SpServicesData:
        """Fetch today's and this month's electricity and water usage.

        Raises:
            ConfigEntryAuthFailed: on HTTP 401 (token expired).
            UpdateFailed: on other HTTP errors.
        """
        auth_headers = {**_HEADERS, "Authorization": f"Bearer {token}"}

        elec_resp = await self._session.get(
            _ELECTRICITY_USAGE_URL,
            headers=auth_headers,
            params={"type": "daily"},
            timeout=30,
        )
        if elec_resp.status_code == 401:
            raise ConfigEntryAuthFailed("SP Services token expired")
        if not elec_resp.ok:
            raise UpdateFailed(
                f"SP Services electricity usage returned HTTP {elec_resp.status_code}"
            )

        water_resp = await self._session.get(
            _WATER_USAGE_URL,
            headers=auth_headers,
            params={"type": "daily"},
            timeout=30,
        )
        if water_resp.status_code == 401:
            raise ConfigEntryAuthFailed("SP Services token expired")
        if not water_resp.ok:
            raise UpdateFailed(
                f"SP Services water usage returned HTTP {water_resp.status_code}"
            )

        return _parse_usage(elec_resp.json(), water_resp.json())

    async def close(self) -> None:
        """Release the underlying HTTP session."""
        await self._session.close()


def _parse_usage(elec_data: dict, water_data: dict) -> SpServicesData:
    """Parse electricity and water API payloads into ``SpServicesData``.

    The field names are educated guesses; update them once real API
    responses are available (see module docstring for how to capture them).
    """

    def _today(payload: dict) -> float | None:
        records: list = (
            payload.get("data")
            or payload.get("records")
            or payload.get("usageData")
            or []
        )
        if records:
            last = records[-1]
            for key in ("consumption", "value", "usage", "amount"):
                raw = last.get(key)
                if raw is not None:
                    try:
                        return float(raw)
                    except (TypeError, ValueError):
                        pass
        return None

    def _month(payload: dict) -> float | None:
        for key in (
            "monthTotal",
            "totalUsage",
            "monthlyUsage",
            "monthlyTotal",
            "total",
        ):
            raw = payload.get(key)
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
        return None

    return SpServicesData(
        electricity_today_kwh=_today(elec_data),
        electricity_month_kwh=_month(elec_data),
        water_today_m3=_today(water_data),
        water_month_m3=_month(water_data),
        account_no=(elec_data.get("accountNo") or elec_data.get("accountNumber")),
        last_updated=datetime.now(),
    )


class SpServicesCoordinator(DataUpdateCoordinator[SpServicesData]):
    """Polls SP Services for household electricity and water usage.

    Update interval: every hour (usage data is updated at most daily, but
    polling hourly ensures the dashboard reflects the latest reading shortly
    after SP Services posts it).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="SP Services Usage",
            update_interval=_UPDATE_INTERVAL,
        )
        self._entry = entry
        self.client = SpServicesClient()

    @property
    def _sp_token(self) -> str | None:
        return self._entry.data.get("sp_token")

    async def _async_update_data(self) -> SpServicesData:
        if not self._sp_token:
            raise ConfigEntryAuthFailed(
                "SP Services auth token is missing — please re-authenticate"
            )
        return await self.client.fetch_usage(self._sp_token)
