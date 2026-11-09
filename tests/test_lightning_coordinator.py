"""Tests for the NEA lightning coordinator and sensors."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.lightning_coordinator import (
    STRIKE_WINDOW,
    LightningCoordinator,
    LightningData,
    LightningStrike,
    UpdateFailed,
    _merge_strikes,
    _parse_lightning,
    nearest_strike,
)
from custom_components.singapore.sensor import (
    SingaporeLightningStrikesSensor,
    SingaporeNearestLightningSensor,
)

_PATCH_SESSION = (
    "custom_components.singapore.lightning_coordinator.async_get_clientsession"
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _reading(lat, lon, kind="G", when=None):
    return {
        "location": {"latitude": str(lat), "longitude": str(lon)},
        "type": kind,
        "text": "Cloud to Ground" if kind == "G" else "Cloud to Cloud",
        "datetime": _iso(when or datetime.now(UTC)),
    }


def _payload(*readings, observed=None):
    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "datetime": _iso(observed or datetime.now(UTC)),
                    "item": {"type": "observation", "readings": list(readings)},
                }
            ]
        },
    }


def test_parse_live_shape():
    # Trimmed from the live API on 2026-11-09.
    payload = {
        "code": 0,
        "data": {
            "records": [
                {
                    "datetime": "2026-11-09T08:24:00+08:00",
                    "item": {
                        "type": "observation",
                        "isStationData": False,
                        "readings": [
                            {
                                "location": {
                                    "latitude": "1.2008",
                                    "longitude": "104.1714",
                                },
                                "type": "C",
                                "text": "Cloud to Cloud",
                                "datetime": "2026-11-09T08:22:47.772+08:00",
                            },
                            {
                                "location": {
                                    "latitude": "1.1470",
                                    "longitude": "104.1745",
                                },
                                "type": "G",
                                "text": "Cloud to Ground",
                                "datetime": "2026-11-09T08:22:47.96+08:00",
                            },
                        ],
                    },
                    "updatedTimestamp": "2026-11-09T08:26:03+08:00",
                }
            ]
        },
        "errorMsg": "",
    }
    strikes, latest = _parse_lightning(payload)
    assert [s.type for s in strikes] == ["cloud_to_cloud", "cloud_to_ground"]
    assert strikes[1].latitude == 1.147 and strikes[1].longitude == 104.1745
    assert strikes[1].time.microsecond == 960000
    assert latest == datetime.fromisoformat("2026-11-09T08:24:00+08:00")


def test_parse_no_lightning_is_valid():
    strikes, latest = _parse_lightning(_payload())
    assert strikes == []
    assert latest is not None


def test_parse_skips_bad_readings():
    bad = {"location": {"latitude": "x", "longitude": "103.8"}, "type": "G"}
    strikes, _ = _parse_lightning(_payload(bad, _reading(1.3, 103.8, "Z")))
    assert len(strikes) == 1
    assert strikes[0].type == "unknown"


def test_parse_bad_envelope_raises():
    with pytest.raises(UpdateFailed):
        _parse_lightning({"code": 0, "data": {}})


def test_merge_dedupes_and_drops_old():
    now = datetime.now(UTC)
    fresh = LightningStrike(now, 1.3, 103.8, "cloud_to_ground")
    old = LightningStrike(now - STRIKE_WINDOW - timedelta(seconds=1), 1.3, 103.8, "x")
    merged = _merge_strikes({fresh}, [fresh, old], now)
    assert merged == {fresh}


def test_nearest_strike():
    now = datetime.now(UTC)
    near = LightningStrike(now, 1.30, 103.80, "cloud_to_ground")
    far = LightningStrike(now, 1.20, 104.17, "cloud_to_cloud")
    strike, dist = nearest_strike(1.31, 103.80, LightningData(strikes=[far, near]))
    assert strike is near
    assert dist == pytest.approx(1.11, abs=0.01)
    assert nearest_strike(1.3, 103.8, LightningData()) is None


def _response(status=200, payload=None):
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


@pytest.mark.asyncio
async def test_coordinator_keeps_strikes_across_polls():
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            _response(payload=_payload(_reading(1.30, 103.80))),
            _response(payload=_payload(_reading(1.35, 103.85, "C"))),
            _response(payload=_payload()),
        ]
    )
    coordinator = LightningCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert len(coordinator.data.strikes) == 1
        await coordinator.async_refresh()
        assert len(coordinator.data.strikes) == 2
        await coordinator.async_refresh()  # quiet poll: strikes stay in window
    assert coordinator.last_update_success is True
    assert len(coordinator.data.strikes) == 2
    assert coordinator.data.cloud_to_ground == 1


@pytest.mark.asyncio
async def test_coordinator_http_error_fallback_and_failure():
    session = MagicMock()
    session.get = MagicMock(return_value=_response(status=429))
    coordinator = LightningCoordinator(MagicMock())
    with patch(_PATCH_SESSION, return_value=session):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        coordinator.data = LightningData()
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True


def _coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def _hass(lat=1.31, lon=103.80):
    hass = MagicMock()
    hass.config.latitude = lat
    hass.config.longitude = lon
    return hass


def test_lightning_strikes_sensor():
    now = datetime.now(UTC)
    data = LightningData(
        strikes=[
            LightningStrike(now, 1.30, 103.80, "cloud_to_ground"),
            LightningStrike(now, 1.20, 104.17, "cloud_to_cloud"),
        ],
        last_observation=now,
    )
    sensor = SingaporeLightningStrikesSensor(_coordinator(data), "entry")
    assert sensor.unique_id == "entry_lightning_strikes"
    assert sensor.native_value == 2
    attrs = sensor.extra_state_attributes
    assert attrs["cloud_to_ground"] == 1
    assert attrs["cloud_to_cloud"] == 1
    assert attrs["strikes"][0]["type"] == "cloud_to_ground"

    empty = SingaporeLightningStrikesSensor(_coordinator(None), "entry")
    assert empty.native_value is None
    assert empty.extra_state_attributes == {}


def test_nearest_lightning_sensor():
    now = datetime.now(UTC)
    data = LightningData(
        strikes=[
            LightningStrike(now, 1.30, 103.80, "cloud_to_ground"),
            LightningStrike(now, 1.20, 104.17, "cloud_to_cloud"),
        ]
    )
    sensor = SingaporeNearestLightningSensor(_coordinator(data), "entry")
    sensor.hass = _hass()
    assert sensor.unique_id == "entry_nearest_lightning"
    assert sensor.native_value == pytest.approx(1.11, abs=0.01)
    attrs = sensor.extra_state_attributes
    assert attrs["type"] == "cloud_to_ground"
    assert attrs["strikes_within_10_km"] == 1


def test_nearest_lightning_sensor_no_strikes_is_unknown():
    sensor = SingaporeNearestLightningSensor(_coordinator(LightningData()), "entry")
    sensor.hass = _hass()
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"strikes_within_10_km": 0}
