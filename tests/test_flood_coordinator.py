"""Tests for the PUB flood alerts coordinator."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.flood_coordinator import (
    ALERT_MAX_AGE,
    FloodAlertCoordinator,
    FloodAlertData,
    UpdateFailed,
    _apply_messages,
    _parse_flood_messages,
)

_SGT = timezone(timedelta(hours=8))
_SENDER = "pub_joint_ops_ctr@pub.gov.sg"


def _empty_record(ts: str) -> dict:
    return {
        "datetime": ts,
        "item": {"type": "observation", "readings": [], "isStationData": False},
        "updatedTimestamp": ts,
    }


def _alert_record(
    ts: str, ident: str, desc: str = "Flash flood at Bt Timah Rd"
) -> dict:
    return {
        "datetime": ts,
        "item": {
            "type": "observation",
            "identifier": ident,
            "isStationData": False,
            "msgType": "Alert",
            "sender": _SENDER,
            "scope": "Public",
            "status": "Actual",
            "readings": [
                {
                    "area": {
                        "areaDesc": "at Bt Timah Rd from Wilby Rd to Blackmore Dr",
                        "circle": [1.33201, 103.87015, 1],
                    },
                    "category": "Met",
                    "certainty": "Observed",
                    "description": desc,
                    "event": "Flood",
                    "headline": "Flash Flood Alert",
                    "instruction": "Please avoid this area for the next one (1) hour.",
                    "responseType": "Avoid",
                    "senderName": "PUB",
                    "severity": "Minor",
                    "urgency": "Immediate",
                }
            ],
        },
    }


def _cancel_record(ts: str, ident: str, ref_ident: str, ref_ts: str) -> dict:
    return {
        "datetime": ts,
        "item": {
            "type": "observation",
            "identifier": ident,
            "isStationData": False,
            "msgType": "Cancel",
            "references": f"{_SENDER}, {ref_ident}, {ref_ts}",
            "readings": [],
        },
    }


def _payload(*records) -> dict:
    # API returns newest first
    return {"code": 0, "data": {"records": list(records)}, "errorMsg": ""}


def test_parse_no_alerts():
    messages, latest = _parse_flood_messages(
        _payload(
            _empty_record("2026-11-07T16:11:22+08:00"),
            _empty_record("2026-11-07T16:09:22+08:00"),
        )
    )
    assert messages == []
    assert latest == datetime(2026, 11, 7, 16, 11, 22, tzinfo=_SGT)


def test_parse_alert_fields():
    messages, _ = _parse_flood_messages(
        _payload(_alert_record("2026-11-07T17:05:00+08:00", "A1"))
    )
    assert len(messages) == 1
    alert = messages[0].alerts[0]
    assert alert.identifier == "A1"
    assert alert.headline == "Flash Flood Alert"
    assert alert.description == "Flash flood at Bt Timah Rd"
    assert alert.severity == "Minor"
    assert alert.latitude == pytest.approx(1.33201)
    assert alert.longitude == pytest.approx(103.87015)


def test_parse_messages_sorted_oldest_first():
    messages, _ = _parse_flood_messages(
        _payload(
            _cancel_record(
                "2026-11-07T17:40:00+08:00", "C1", "A1", "2026-11-07T17:05:00+08:00"
            ),
            _alert_record("2026-11-07T17:05:00+08:00", "A1"),
        )
    )
    assert [m.msg_type for m in messages] == ["Alert", "Cancel"]


def test_parse_bad_payload_raises():
    with pytest.raises(UpdateFailed):
        _parse_flood_messages({"code": 17, "data": None})


def test_alert_then_cancel_in_same_batch_is_inactive():
    messages, _ = _parse_flood_messages(
        _payload(
            _cancel_record(
                "2026-11-07T17:40:00+08:00", "C1", "A1", "2026-11-07T17:05:00+08:00"
            ),
            _alert_record("2026-11-07T17:05:00+08:00", "A1"),
        )
    )
    now = datetime(2026, 11, 7, 17, 45, tzinfo=_SGT)
    assert _apply_messages({}, messages, now) == {}


def test_alert_persists_after_scrolling_out_of_window_until_cancelled():
    now = datetime(2026, 11, 7, 17, 10, tzinfo=_SGT)
    first, _ = _parse_flood_messages(
        _payload(_alert_record("2026-11-07T17:05:00+08:00", "A1"))
    )
    active = _apply_messages({}, first, now)
    assert set(active) == {"A1"}

    # Later poll: alert record no longer in the window, nothing new.
    active = _apply_messages(active, [], now + timedelta(minutes=50))
    assert set(active) == {"A1"}

    # Cancel arrives.
    cancel, _ = _parse_flood_messages(
        _payload(
            _cancel_record(
                "2026-11-07T18:10:00+08:00", "C1", "A1", "2026-11-07T17:05:00+08:00"
            )
        )
    )
    assert _apply_messages(active, cancel, now + timedelta(hours=1)) == {}


def test_cancel_only_removes_referenced_alert():
    messages, _ = _parse_flood_messages(
        _payload(
            _cancel_record(
                "2026-11-07T17:40:00+08:00", "C1", "A1", "2026-11-07T17:05:00+08:00"
            ),
            _alert_record("2026-11-07T17:20:00+08:00", "A2", "Flash flood at Dunearn"),
            _alert_record("2026-11-07T17:05:00+08:00", "A1"),
        )
    )
    now = datetime(2026, 11, 7, 17, 45, tzinfo=_SGT)
    assert set(_apply_messages({}, messages, now)) == {"A2"}


def test_uncancelled_alert_expires():
    messages, _ = _parse_flood_messages(
        _payload(_alert_record("2026-11-07T17:05:00+08:00", "A1"))
    )
    issued = datetime(2026, 11, 7, 17, 5, tzinfo=_SGT)
    assert _apply_messages({}, messages, issued + ALERT_MAX_AGE) == {}


def _mock_session(status: int, payload: dict | None = None) -> MagicMock:
    response = AsyncMock()
    response.headers = {}  # real mapping: the data.gov.sg limiter reads it on 429
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


@pytest.mark.asyncio
async def test_coordinator_tracks_active_alerts():
    now = datetime.now(_SGT).replace(microsecond=0)
    payload = _payload(
        _alert_record((now - timedelta(minutes=2)).isoformat(), "A2", "Newer"),
        _alert_record((now - timedelta(minutes=10)).isoformat(), "A1", "Older"),
    )
    coordinator = FloodAlertCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.flood_coordinator.async_get_clientsession",
        return_value=_mock_session(200, payload),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert [a.description for a in coordinator.data.alerts] == ["Newer", "Older"]


@pytest.mark.asyncio
async def test_coordinator_http_error_without_cache_fails():
    coordinator = FloodAlertCoordinator(MagicMock())
    with patch(
        "custom_components.singapore.flood_coordinator.async_get_clientsession",
        return_value=_mock_session(429),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_http_error_uses_last_known_data():
    coordinator = FloodAlertCoordinator(MagicMock())
    coordinator.data = FloodAlertData()
    with patch(
        "custom_components.singapore.flood_coordinator.async_get_clientsession",
        return_value=_mock_session(503),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.alerts == []


def test_parse_skips_junk_and_handles_odd_alerts():
    from custom_components.singapore.flood_coordinator import _parse_datetime

    assert _parse_datetime(None) is None
    assert _parse_datetime("not a date") is None
    # Naive timestamps are Singapore time.
    assert _parse_datetime("2026-04-05T08:00:00").utcoffset() == timedelta(hours=8)

    payload = {
        "data": {
            "records": [
                "junk",
                {"datetime": "garbage", "item": {}},
                {
                    # No identifier and a bad circle: still an alert, with a
                    # synthesised id and no coordinates.
                    "datetime": "2026-04-05T08:00:00+08:00",
                    "item": {
                        "msgType": "Alert",
                        "readings": [
                            {
                                "description": "Jalan Bahar",
                                "area": {"circle": ["x", "y", 1]},
                            }
                        ],
                    },
                },
            ]
        }
    }
    messages, latest = _parse_flood_messages(payload)
    assert latest is not None
    assert len(messages) == 1
    message = messages[0]
    assert message.identifier == "2026-04-05T08:00:00+08:00|Jalan Bahar"
    alert = message.alerts[0]
    assert (alert.latitude, alert.longitude) == (None, None)
    assert alert.headline == "Flash Flood Alert"


def test_parse_payload_without_records_list_raises():
    from homeassistant.helpers.update_coordinator import UpdateFailed

    with pytest.raises(UpdateFailed, match="no records list"):
        _parse_flood_messages({"data": {"records": None}})
