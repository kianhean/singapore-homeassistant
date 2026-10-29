"""Fixtures for tests that run against a real Home Assistant install.

Unlike ``tests/``, nothing here is mocked at the module level: these tests
load the integration into an actual Home Assistant instance via
pytest-homeassistant-custom-component, so they catch breaking HA API changes.
HTTP is still mocked so the suite stays offline and deterministic.
"""

from __future__ import annotations

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.singapore.coe_coordinator import COE_API_URL
from custom_components.singapore.coordinator import TARIFF_URL
from custom_components.singapore.holiday_coordinator import PUBLIC_HOLIDAYS_URL
from custom_components.singapore.psi_coordinator import PSI_URL
from custom_components.singapore.train_coordinator import TRAIN_STATUS_URL
from custom_components.singapore.weather_coordinator import (
    _READINGS_ENDPOINTS,
    FOUR_DAY_URL,
    WEATHER_URL,
)

pytest_plugins = "pytest_homeassistant_custom_component"

_TARIFF_HTML = """
<html><body>
<h2>Tariffs – 1 January 2025 to 31 March 2025</h2>
<table><tbody>
  <tr><td>Network</td><td>7.61</td></tr>
  <tr><td>Total (incl. GST)</td><td>29.29</td></tr>
  <tr><td>Gas tariff (incl. GST)</td><td>20.14</td></tr>
  <tr><td>Water tariff (incl. GST)</td><td>3.69</td></tr>
</tbody></table>
</body></html>
"""

_COE_PAYLOAD = {
    "success": True,
    "result": {
        "records": [
            {
                "month": "2026-03",
                "bidding_no": "1",
                "vehicle_class": f"Category {cat}",
                "quota": "1000",
                "bids_success": "990",
                "premium": premium,
            }
            for cat, premium in zip("ABCDE", (95501, 112000, 70000, 9000, 113000))
        ]
    },
}

_TWO_HR_PAYLOAD = {
    "items": [
        {
            "timestamp": "2026-04-05T08:00:00+08:00",
            "valid_period": {
                "start": "2026-04-05T08:00:00+08:00",
                "end": "2026-04-05T10:00:00+08:00",
            },
            "forecasts": [
                {"area": "Bedok", "forecast": "Thundery Showers"},
                {"area": "Woodlands", "forecast": "Partly Cloudy (Day)"},
            ],
        }
    ]
}

_TRAIN_PAYLOAD = {
    "value": {
        "Status": 1,
        "AffectedSegments": [{"Line": "CCL", "Direction": "both"}],
        "Message": [],
    }
}


_PSI_PAYLOAD = {
    "code": 0,
    "errorMsg": "",
    "data": {
        "regionMetadata": [
            {"name": "West", "labelLocation": {"latitude": 1.35735, "longitude": 103.7}}
        ],
        "items": [
            {
                "timestamp": "2026-04-05T08:00:00+08:00",
                "readings": {
                    "psi_twenty_four_hourly": {
                        "north": 40,
                        "south": 45,
                        "east": 52,
                        "west": 48,
                        "central": 50,
                    },
                    "pm25_twenty_four_hourly": {
                        "north": 10,
                        "south": 12,
                        "east": 14,
                        "west": 11,
                        "central": 13,
                    },
                    "co_eight_hour_max": {
                        "north": 0.4,
                        "south": 0.5,
                        "east": 0.6,
                        "west": 0.3,
                        "central": 0.5,
                    },
                },
            }
        ],
    },
}


def _holiday_html() -> str:
    year = dt_util.now().year
    return f"""
<html><body><table>
  <thead><tr><th>Holiday</th><th>Date</th></tr></thead>
  <tbody>
    <tr><td>New Year's Day</td><td>1 January {year + 1}, Friday</td></tr>
  </tbody>
</table></body></html>
"""


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load custom_components/singapore."""
    return


@pytest.fixture
def mock_sources(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Serve canned responses for every upstream data source."""
    aioclient_mock.get(TARIFF_URL, text=_TARIFF_HTML)
    aioclient_mock.get(COE_API_URL, json=_COE_PAYLOAD)
    aioclient_mock.get(WEATHER_URL, json=_TWO_HR_PAYLOAD)
    aioclient_mock.get(FOUR_DAY_URL, status=404)
    for url in _READINGS_ENDPOINTS.values():
        aioclient_mock.get(
            url, json={"items": [{"readings": [{"value": 30}, {"value": 32}]}]}
        )
    aioclient_mock.get(PUBLIC_HOLIDAYS_URL, text=_holiday_html())
    aioclient_mock.post(TRAIN_STATUS_URL, json=_TRAIN_PAYLOAD)
    aioclient_mock.get(PSI_URL, json=_PSI_PAYLOAD)
    return aioclient_mock


SP_ACCOUNT = "8949049293"


class FakeSpClient:
    """Stands in for ``sp_services.SpServicesClient`` (no network)."""

    renew_error: Exception | None = None
    session_value = "rolled"
    instances: list[FakeSpClient] = []

    def __init__(self, *, session_state=None, debug=False) -> None:
        self.session_state = session_state
        self.token_set = None
        self.renew_calls = 0
        FakeSpClient.instances.append(self)

    async def renew_silently(self):
        from datetime import UTC, datetime, timedelta

        from sp_services import TokenSet

        self.renew_calls += 1
        if FakeSpClient.renew_error is not None:
            raise FakeSpClient.renew_error
        self.token_set = TokenSet(
            access_token="token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        return self.token_set

    def export_session(self):
        from sp_services import SessionCookie, SessionState

        return SessionState(
            cookies=[
                SessionCookie(
                    name="auth0",
                    value=FakeSpClient.session_value,
                    domain="identity.spdigital.auth0.com",
                )
            ]
        )

    async def fetch_usage(self, token, account_no=None):
        from datetime import datetime, timedelta

        from sp_services import UsageData, UsagePoint

        from custom_components.singapore.sp_statistics import SP_TIMEZONE

        now = datetime.now(SP_TIMEZONE)
        yesterday = (now - timedelta(days=1)).date()
        last_month = now.replace(day=1) - timedelta(days=1)
        return UsageData(
            electricity_today_kwh=None,
            electricity_month_kwh=None,
            water_today_m3=None,
            water_month_m3=None,
            account_no=SP_ACCOUNT,
            last_updated=now,
            electricity_daily_history=[UsagePoint(now.date().isoformat(), 9.5)],
            electricity_monthly_history=[
                UsagePoint(last_month.replace(day=1).isoformat(), 572.0, "Actual")
            ],
            water_monthly_history=[
                UsagePoint(last_month.replace(day=1).isoformat(), 33.9, "Actual")
            ],
            electricity_hourly_history=[
                UsagePoint(f"{yesterday.isoformat()} 10:00:00", 0.5),
                UsagePoint(f"{yesterday.isoformat()} 10:30:00", 0.25),
                UsagePoint(f"{yesterday.isoformat()} 11:00:00", 0.4),
                UsagePoint(f"{yesterday.isoformat()} 11:30:00", 0.4),
            ],
        )

    async def close(self) -> None:
        return None


def sp_session(value: str = "pasted") -> str:
    from sp_services import SessionCookie, SessionState

    return SessionState(
        cookies=[
            SessionCookie(
                name="auth0", value=value, domain="identity.spdigital.auth0.com"
            )
        ]
    ).to_string()


@pytest.fixture
def fake_sp_client():
    """Patch the SP Services client used by the config flow and coordinator."""
    from unittest.mock import patch

    FakeSpClient.renew_error = None
    FakeSpClient.session_value = "rolled"
    FakeSpClient.instances = []
    with (
        patch("custom_components.singapore.config_flow.SpServicesClient", FakeSpClient),
        patch(
            "custom_components.singapore.sp_services_coordinator.SpServicesClient",
            FakeSpClient,
        ),
    ):
        yield FakeSpClient
