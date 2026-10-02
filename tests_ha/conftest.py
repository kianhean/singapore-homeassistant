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
