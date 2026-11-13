"""Run the shipped automation blueprints in a real Home Assistant.

Each blueprint is copied into a temporary config dir, instantiated with
`use_blueprint`, and driven by setting sensor states the way the integration
would. This checks that the blueprint validates on the HA version under test
and that its templates produce the expected notifications and actions.
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

BLUEPRINTS = Path(__file__).parents[1] / "blueprints" / "automation" / "singapore"
ALL_DAY = {
    "start_time": "00:00:00",
    "end_time": "23:59:59",
    "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
}


@pytest.fixture
async def notify_calls(hass: HomeAssistant, tmp_path: Path) -> list[ServiceCall]:
    """Point HA at a config dir holding the blueprints; mock notify.notify."""
    hass.config.config_dir = str(tmp_path)
    shutil.copytree(BLUEPRINTS, tmp_path / "blueprints" / "automation" / "singapore")
    hass.config.latitude, hass.config.longitude = 1.3236, 103.9273  # Bedok
    return async_mock_service(hass, "notify", "notify")


async def _automation(hass: HomeAssistant, blueprint: str, inputs: dict) -> None:
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "test",
                "use_blueprint": {
                    "path": f"singapore/{blueprint}",
                    "input": inputs,
                },
            }
        },
    )
    await hass.async_block_till_done()
    # An invalid blueprint leaves the automation unavailable instead of "on".
    assert hass.states.get("automation.test").state == "on"


async def _set(hass: HomeAssistant, entity_id: str, state, **attrs) -> None:
    hass.states.async_set(entity_id, state, attrs)
    await hass.async_block_till_done()


def test_every_blueprint_is_tested() -> None:
    tested = {
        "lightning_nearby.yaml",
        "mrt_line_disruption.yaml",
        "flood_alert.yaml",
        "haze_purifier.yaml",
        "coe_results.yaml",
    }
    assert {p.name for p in BLUEPRINTS.glob("*.yaml")} == tested


async def test_lightning_nearby(hass: HomeAssistant, notify_calls) -> None:
    sensor = "sensor.nea_nearest_lightning"
    await _set(hass, sensor, "12.4", type="cloud_to_cloud")
    await _automation(hass, "lightning_nearby.yaml", {"distance": 5})

    await _set(hass, sensor, "8.0", type="cloud_to_cloud")
    assert notify_calls == []

    await _set(hass, sensor, "3.214", type="cloud_to_ground")
    assert len(notify_calls) == 1
    assert notify_calls[0].data == {
        "title": "Lightning nearby",
        "message": "Lightning 3.2 km away (cloud to ground)",
    }


async def test_mrt_line_disruption(hass: HomeAssistant, notify_calls) -> None:
    line = "sensor.mrt_lrt_circle_line_status"
    other = "sensor.mrt_lrt_north_south_line_status"
    details = "07:28-CCL-No train service between Paya Lebar and Dhoby Ghaut."
    await _set(hass, "sensor.mrt_lrt_status", "normal", details="")
    await _set(hass, line, "normal", friendly_name="MRT/LRT Circle Line Status")
    await _set(hass, other, "normal")
    await _automation(hass, "mrt_line_disruption.yaml", ALL_DAY)

    # Planned works are ignored by default; unwatched lines are ignored.
    await _set(hass, line, "planned", friendly_name="MRT/LRT Circle Line Status")
    await _set(hass, other, "disruption")
    assert notify_calls == []

    await _set(hass, "sensor.mrt_lrt_status", "disruption", details=details)
    await _set(hass, line, "disruption", friendly_name="MRT/LRT Circle Line Status")
    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"].startswith("Circle Line: ")
    assert notify_calls[0].data["message"] == details

    await _set(hass, line, "normal", friendly_name="MRT/LRT Circle Line Status")
    assert len(notify_calls) == 2
    assert notify_calls[1].data["title"] == "Circle Line: back to normal"


async def test_mrt_line_outside_travel_window(hass: HomeAssistant, notify_calls):
    line = "sensor.mrt_lrt_circle_line_status"
    await _set(hass, line, "normal")
    await _automation(hass, "mrt_line_disruption.yaml", {"weekdays": []})

    await _set(hass, line, "disruption")
    assert notify_calls == []


def _alert(identifier: str, description: str, lat: float, lon: float) -> dict:
    return {
        "identifier": identifier,
        "description": description,
        "area": description,
        "latitude": lat,
        "longitude": lon,
    }


async def test_flood_alert_anywhere(hass: HomeAssistant, notify_calls) -> None:
    sensor = "sensor.pub_flood_alerts"
    far = _alert("a1", "Flash flood at Jalan Seaview", 1.300, 103.885)
    near = _alert("a2", "Flash flood at Bedok North Ave 3", 1.330, 103.930)
    await _set(hass, sensor, "0", alerts=[])
    await _automation(hass, "flood_alert.yaml", {"radius": 0})

    await _set(hass, sensor, "2", alerts=[near, far])
    assert [c.data["message"] for c in notify_calls] == [
        "Flash flood at Bedok North Ave 3; Flash flood at Jalan Seaview"
    ]

    # The same alerts again, or one being cancelled, are not new.
    await _set(hass, sensor, "1", alerts=[near])
    assert len(notify_calls) == 1


async def test_flood_alert_only_near_home(hass: HomeAssistant, notify_calls) -> None:
    sensor = "sensor.pub_flood_alerts"
    far = _alert("a1", "Flash flood at Jalan Seaview", 1.300, 103.885)  # ~5 km
    near = _alert("a2", "Flash flood at Bedok North Ave 3", 1.330, 103.930)  # ~1 km
    await _set(hass, sensor, "0", alerts=[])
    await _automation(hass, "flood_alert.yaml", {"radius": 2})

    await _set(hass, sensor, "1", alerts=[far])
    assert notify_calls == []

    await _set(hass, sensor, "2", alerts=[near, far])
    assert [c.data["message"] for c in notify_calls] == [
        "Flash flood at Bedok North Ave 3"
    ]


async def test_flood_alert_ignores_startup(hass: HomeAssistant, notify_calls) -> None:
    sensor = "sensor.pub_flood_alerts"
    await _set(hass, sensor, "unavailable")
    await _automation(hass, "flood_alert.yaml", {})

    await _set(hass, sensor, "1", alerts=[_alert("a1", "Old alert", 1.32, 103.92)])
    assert notify_calls == []


async def test_haze_purifier(hass: HomeAssistant, notify_calls) -> None:
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    sensor = "sensor.air_quality_psi"
    await _set(hass, sensor, "42", band="good")
    await _automation(
        hass, "haze_purifier.yaml", {"devices": {"entity_id": "fan.purifier"}}
    )

    await _set(hass, sensor, "112", band="unhealthy")
    assert [c.data["entity_id"] for c in turn_on] == [["fan.purifier"]]
    assert notify_calls[0].data["message"] == "PSI is 112 (unhealthy). Purifiers on."

    await _set(hass, sensor, "45", band="good")
    assert [c.data["entity_id"] for c in turn_off] == [["fan.purifier"]]
    assert notify_calls[1].data["title"] == "Haze mode off"


async def test_coe_results(hass: HomeAssistant, notify_calls) -> None:
    sensors = [f"sensor.coe_category_{c}" for c in "abcde"]
    old = (95501, 113890, 66000, 8889, 114002)
    new = (96000, 114500, 66101, 9001, 115000)
    for c, entity_id, premium in zip("ABCDE", sensors, old):
        await _set(hass, entity_id, premium, category=f"Category {c}")
    await _automation(hass, "coe_results.yaml", {})

    # One coordinator refresh updates every category at once.
    for c, entity_id, premium in zip("ABCDE", sensors, new):
        hass.states.async_set(
            entity_id,
            premium,
            {"category": f"Category {c}", "month": "2026-11", "bidding_no": 2},
        )
    # The blueprint waits 5 s so all five categories land in one message.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()

    assert len(notify_calls) == 1
    assert notify_calls[0].data["message"] == (
        "2026-11, bidding 2\n"
        "Category A: 96,000 SGD\n"
        "Category B: 114,500 SGD\n"
        "Category C: 66,101 SGD\n"
        "Category D: 9,001 SGD\n"
        "Category E: 115,000 SGD"
    )
