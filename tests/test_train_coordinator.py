"""Tests for train status parser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.train_coordinator import _parse_train_status


def _payload(messages=None, affected_segments=None):
    """Build a minimal TrainServiceAlerts API payload."""
    return {
        "value": {
            "Status": 1,
            "AffectedSegments": affected_segments or [],
            "Message": [
                {"Content": c, "CreatedDate": "2026-04-06 10:00:00"}
                for c in (messages or [])
            ],
        }
    }


def test_parse_train_status_planned():
    data = _payload(["21:00-NSL-Planned train service adjustments tonight."])
    result = _parse_train_status(data)
    assert result.status == "planned"
    assert result.line_statuses["North-South Line"] == "planned"
    assert result.line_statuses["East-West Line"] == "normal"


def test_parse_train_status_disruption():
    data = _payload(["Circle Line service disruption due to track fault."])
    result = _parse_train_status(data)
    assert result.status == "disruption"
    assert result.line_statuses["Circle Line"] == "disruption"


def test_parse_train_status_normal():
    data = _payload()
    result = _parse_train_status(data)
    assert result.status == "normal"
    assert all(s == "normal" for s in result.line_statuses.values())


def test_parse_train_status_mixed_per_line():
    data = _payload(
        [
            "EWL service disruption due to signaling fault.",
            "21:00-CCL-Planned train service adjustments tonight.",
        ]
    )
    result = _parse_train_status(data)
    assert result.line_statuses["East-West Line"] == "disruption"
    assert result.line_statuses["Circle Line"] == "planned"
    assert result.line_statuses["North-South Line"] == "normal"


def test_parse_train_status_planned_disruptions_plural():
    data = _payload(["CCL planned disruptions due to engineering works."])
    result = _parse_train_status(data)
    assert result.status == "planned"
    assert result.line_statuses["Circle Line"] == "planned"


def test_parse_train_status_ccl_planned_service_adjustments():
    data = _payload(
        ["21:00-CCL-Planned train service adjustments from 17 January to 9 April 2026."]
    )
    result = _parse_train_status(data)
    assert result.status == "planned"
    assert result.line_statuses["Circle Line"] == "planned"


def test_parse_affected_segments_marks_disruption():
    data = _payload(affected_segments=[{"Line": "NSL", "Direction": "both"}])
    result = _parse_train_status(data)
    assert result.status == "disruption"
    assert result.line_statuses["North-South Line"] == "disruption"


def test_disruption_not_downgraded_by_planned_message():
    """A line already marked disrupted via AffectedSegments should stay disrupted."""
    data = {
        "value": {
            "Status": 2,
            "AffectedSegments": [{"Line": "CCL", "Direction": "both"}],
            "Message": [
                {
                    "Content": "CCL planned maintenance works tonight.",
                    "CreatedDate": "2026-04-06 10:00:00",
                }
            ],
        }
    }
    result = _parse_train_status(data)
    assert result.line_statuses["Circle Line"] == "disruption"


def test_non_train_messages_ignored():
    """Road/bus alerts without a line code should not affect line statuses."""
    data = _payload(
        [
            "14:30-Accident on SLE (towards BKE) with delays to buses 161 and 168.",
        ]
    )
    result = _parse_train_status(data)
    assert result.status == "normal"
    assert all(s == "normal" for s in result.line_statuses.values())


def test_details_populated_when_not_normal():
    data = _payload(["21:00-CCL-Planned train service adjustments."])
    result = _parse_train_status(data)
    assert "CCL" in result.details


def test_details_empty_when_normal():
    data = _payload()
    result = _parse_train_status(data)
    assert result.details == ""


def test_parse_empty_payload():
    result = _parse_train_status({})
    assert result.status == "normal"
    assert all(s == "normal" for s in result.line_statuses.values())


@pytest.mark.asyncio
async def test_train_coordinator_http_error_without_cache_fails():
    from custom_components.singapore.train_coordinator import TrainStatusCoordinator

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)

    coordinator = TrainStatusCoordinator(hass)

    with patch(
        "custom_components.singapore.train_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_train_coordinator_http_error_uses_last_known_data():
    from custom_components.singapore.train_coordinator import (
        TRAIN_LINES,
        TrainStatusCoordinator,
        TrainStatusData,
    )

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)

    coordinator = TrainStatusCoordinator(hass)
    coordinator.data = TrainStatusData(
        status="normal",
        details="All lines are operating normally.",
        line_statuses={line: "normal" for line in TRAIN_LINES},
    )

    with patch(
        "custom_components.singapore.train_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.status == "normal"
