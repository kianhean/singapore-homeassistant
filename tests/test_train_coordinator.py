"""Tests for train status parser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.singapore.train_coordinator import _parse_train_status


def test_parse_train_status_planned():
    html = """
    <html><body>
      <div>North-South Line planned disruption due to engineering works.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "planned"
    assert data.line_statuses["North-South Line"] == "planned"
    assert data.line_statuses["East-West Line"] == "normal"


def test_parse_train_status_disruption():
    html = """
    <html><body>
      <div>Circle Line service disruption due to track fault.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "disruption"
    assert data.line_statuses["Circle Line"] == "disruption"


def test_parse_train_status_normal():
    html = """
    <html><body>
      <div>All train lines are operating normally.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "normal"
    assert all(status == "normal" for status in data.line_statuses.values())


def test_parse_train_status_mixed_per_line():
    html = """
    <html><body>
      <div>North-South Line operating normally.</div>
      <div>East-West Line service disruption due to signaling fault.</div>
      <div>Circle Line planned disruption tonight.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.line_statuses["North-South Line"] == "normal"
    assert data.line_statuses["East-West Line"] == "disruption"
    assert data.line_statuses["Circle Line"] == "planned"


def test_parse_train_status_planned_disruptions_plural():
    html = """
    <html><body>
      <div>Circle Line planned disruptions due to engineering works.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "planned"
    assert data.line_statuses["Circle Line"] == "planned"


def test_parse_train_status_ccl_planned_service_adjustments():
    html = """
    <html><body>
      <div>Circle Line</div>
      <div>21:00-CCL-Planned train service adjustments from 17 January to 9 April 2026.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "planned"
    assert data.line_statuses["Circle Line"] == "planned"


def test_parse_train_status_keeps_full_details_for_planned_or_disruption():
    long_notice = "Circle Line planned disruption due to engineering works. " + (
        "A" * 400
    )
    html = f"""
    <html><body>
      <div>{long_notice}</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "planned"
    assert data.details.endswith("A" * 400)
    assert len(data.details) > 240


@pytest.mark.asyncio
async def test_train_coordinator_http_error_without_cache_fails():
    from custom_components.singapore.train_coordinator import TrainStatusCoordinator

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

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
    mock_session.get = MagicMock(return_value=mock_response)

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
