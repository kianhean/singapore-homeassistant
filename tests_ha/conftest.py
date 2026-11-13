"""Fixtures for tests that run against a real Home Assistant install.

Unlike ``tests/``, nothing here is mocked at the module level: these tests
load the integration into an actual Home Assistant instance via
pytest-homeassistant-custom-component, so they catch breaking HA API changes.
HTTP is still mocked so the suite stays offline and deterministic.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.singapore.coe_coordinator import COE_API_URL
from custom_components.singapore.coordinator import TARIFF_URL
from custom_components.singapore.dengue_coordinator import POLL_DOWNLOAD_URL
from custom_components.singapore.flood_coordinator import FLOOD_ALERTS_URL
from custom_components.singapore.holiday_coordinator import PUBLIC_HOLIDAYS_URL
from custom_components.singapore.lightning_coordinator import LIGHTNING_URL
from custom_components.singapore.psi_coordinator import PSI_URL
from custom_components.singapore.sp_account import client as client_module
from custom_components.singapore.sp_account.client import SpGroupClient
from custom_components.singapore.traffic_coordinator import TRAFFIC_IMAGES_URL
from custom_components.singapore.train_coordinator import TRAIN_STATUS_URL
from custom_components.singapore.uv_coordinator import UV_URL
from custom_components.singapore.weather_coordinator import (
    _READINGS_ENDPOINTS,
    FOUR_DAY_URL,
    TWENTY_FOUR_HR_URL,
    WEATHER_URL,
)

# Recorded SP Group (Auth0/Jarvis/Njord) responses, vendored with the client
# from maci0/home-assistant-sp.
from tests.sp_account.conftest import FixedClock, FixtureTransport

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
    "area_metadata": [
        {"name": "Bedok", "label_location": {"latitude": 1.321, "longitude": 103.924}},
        {
            "name": "Woodlands",
            "label_location": {"latitude": 1.432, "longitude": 103.786},
        },
    ],
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
    ],
}

# Periods are far in the future so none has ended when the test runs.
_TWENTY_FOUR_HR_PAYLOAD = {
    "code": 0,
    "data": {
        "records": [
            {
                "periods": [
                    {
                        "timePeriod": {
                            "start": "2099-01-01T18:00:00+08:00",
                            "end": "2099-01-02T06:00:00+08:00",
                        },
                        "regions": {
                            "east": {"code": "TL", "text": "Thundery Showers"},
                            "north": {"code": "CL", "text": "Cloudy"},
                        },
                    },
                    {
                        "timePeriod": {
                            "start": "2099-01-02T06:00:00+08:00",
                            "end": "2099-01-02T12:00:00+08:00",
                        },
                        "regions": {
                            "east": {"code": "FD", "text": "Fair (Day)"},
                            "north": {"code": "CL", "text": "Cloudy"},
                        },
                    },
                ]
            }
        ]
    },
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


def _flood_payload() -> dict:
    # Issued "now" so the 3-hour safety expiry doesn't drop it.
    issued = dt_util.now().replace(microsecond=0).isoformat()
    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "datetime": issued,
                    "item": {
                        "type": "observation",
                        "identifier": "2.49.0.0.702.2-BCM-1",
                        "msgType": "Alert",
                        "isStationData": False,
                        "readings": [
                            {
                                "area": {
                                    "areaDesc": "at Bt Timah Rd",
                                    "circle": [1.33201, 103.87015, 1],
                                },
                                "description": "Flash flood at Bt Timah Rd",
                                "headline": "Flash Flood Alert",
                                "instruction": "Please avoid this area.",
                                "severity": "Minor",
                                "urgency": "Immediate",
                            }
                        ],
                    },
                }
            ]
        },
        "errorMsg": "",
    }


_TRAFFIC_PAYLOAD = {
    "items": [
        {
            "timestamp": "2026-04-05T08:00:00+08:00",
            "cameras": [
                {
                    "camera_id": cam_id,
                    "timestamp": "2026-04-05T07:59:00+08:00",
                    "image": f"https://images.data.gov.sg/api/traffic-images/{cam_id}.jpg",
                    "location": {"latitude": 1.447, "longitude": 103.771},
                }
                # 2702 deliberately absent: its entity must go unavailable.
                for cam_id in ("2701", "4703", "4713", "1001")
            ],
        }
    ]
}

_DENGUE_FILE_URL = (
    "https://s3.ap-southeast-1.amazonaws.com/blobs.data.gov.sg/dengue.geojson"
)

_DENGUE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"LOCALITY": "Lentor Gdns", "CASE_SIZE": 21},
            "geometry": {
                "type": "Polygon",
                # Square around the pytest-homeassistant default home
                # location (32.87336, -117.22743), so home is inside it.
                "coordinates": [
                    [
                        [-117.23, 32.87],
                        [-117.22, 32.87],
                        [-117.22, 32.88],
                        [-117.23, 32.88],
                        [-117.23, 32.87],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"LOCALITY": "Bishan St 12", "CASE_SIZE": 20},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[103.84, 1.34], [103.85, 1.34], [103.85, 1.35], [103.84, 1.34]]
                ],
            },
        },
    ],
}


def _lightning_payload() -> dict:
    now = dt_util.utcnow().isoformat()
    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "datetime": now,
                    "item": {
                        "type": "observation",
                        "readings": [
                            # ~1.1 km north of the harness home location.
                            {
                                "location": {
                                    "latitude": "32.88336",
                                    "longitude": "-117.22743",
                                },
                                "type": "G",
                                "text": "Cloud to Ground",
                                "datetime": now,
                            },
                            {
                                "location": {
                                    "latitude": "1.2008",
                                    "longitude": "104.1714",
                                },
                                "type": "C",
                                "text": "Cloud to Cloud",
                                "datetime": now,
                            },
                        ],
                    },
                }
            ]
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
    aioclient_mock.get(TWENTY_FOUR_HR_URL, json=_TWENTY_FOUR_HR_PAYLOAD)
    aioclient_mock.get(
        UV_URL,
        json={
            "code": 0,
            "data": {
                "records": [
                    {
                        "index": [
                            {"hour": "2026-11-13T13:00:00+08:00", "value": 9},
                            {"hour": "2026-11-13T12:00:00+08:00", "value": 7},
                        ]
                    }
                ]
            },
        },
    )
    for url in _READINGS_ENDPOINTS.values():
        aioclient_mock.get(
            url, json={"items": [{"readings": [{"value": 30}, {"value": 32}]}]}
        )
    aioclient_mock.get(PUBLIC_HOLIDAYS_URL, text=_holiday_html())
    aioclient_mock.post(TRAIN_STATUS_URL, json=_TRAIN_PAYLOAD)
    aioclient_mock.get(PSI_URL, json=_PSI_PAYLOAD)
    aioclient_mock.get(FLOOD_ALERTS_URL, json=_flood_payload())
    aioclient_mock.get(TRAFFIC_IMAGES_URL, json=_TRAFFIC_PAYLOAD)
    aioclient_mock.get(
        "https://images.data.gov.sg/api/traffic-images/2701.jpg", content=b"jpeg-2701"
    )
    # The live poll-download endpoint answers 201 Created.
    aioclient_mock.get(
        POLL_DOWNLOAD_URL,
        status=201,
        json={"code": 0, "data": {"url": _DENGUE_FILE_URL}},
    )
    aioclient_mock.get(_DENGUE_FILE_URL, json=_DENGUE_GEOJSON)
    aioclient_mock.get(LIGHTNING_URL, json=_lightning_payload())
    return aioclient_mock


@pytest.fixture(autouse=True)
def clear_sp_login_cooldown() -> Iterator[None]:
    """The client's per-account sign-in cooldown is process state."""
    client_module._LOGIN_FAILURES.clear()
    client_module._LOGIN_ATTEMPTS.clear()
    yield
    client_module._LOGIN_FAILURES.clear()
    client_module._LOGIN_ATTEMPTS.clear()


@pytest.fixture
def sp_transport() -> Iterator[FixtureTransport]:
    """Route every SpGroupClient the integration creates to recorded fixtures."""
    transport = FixtureTransport()

    class _FixtureClient(SpGroupClient):
        def __init__(self, session=None, **_kwargs) -> None:
            super().__init__(session=session, transport=transport, clock=FixedClock())

    with (
        patch("custom_components.singapore.config_flow.SpGroupClient", _FixtureClient),
        patch(
            "custom_components.singapore.sp_account_coordinator.SpGroupClient",
            _FixtureClient,
        ),
    ):
        yield transport
