"""Tests for COE coordinator and parser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.coe_coordinator import (
    CoeData,
    _backoff_delay_seconds,
    _parse_coe,
)

# ---------------------------------------------------------------------------
# API response fixtures
# ---------------------------------------------------------------------------

_API_RESPONSE_OK = {
    "success": True,
    "result": {
        "records": [
            {
                "month": "2026-03",
                "bidding_no": "1",
                "vehicle_class": "Category A",
                "quota": 697,
                "bids_success": 697,
                "premium": 95501,
            },
            {
                "month": "2026-03",
                "bidding_no": "1",
                "vehicle_class": "Category B",
                "quota": 432,
                "bids_success": 432,
                "premium": 112001,
            },
            {
                "month": "2026-03",
                "bidding_no": "1",
                "vehicle_class": "Category C",
                "quota": 275,
                "bids_success": 275,
                "premium": 73001,
            },
            {
                "month": "2026-03",
                "bidding_no": "1",
                "vehicle_class": "Category D",
                "quota": 1234,
                "bids_success": 1234,
                "premium": 9801,
            },
            {
                "month": "2026-03",
                "bidding_no": "1",
                "vehicle_class": "Category E",
                "quota": 567,
                "bids_success": 567,
                "premium": 118001,
            },
            # Older exercise — should be ignored
            {
                "month": "2026-02",
                "bidding_no": "2",
                "vehicle_class": "Category A",
                "quota": 700,
                "bids_success": 700,
                "premium": 91001,
            },
        ]
    },
}

_API_RESPONSE_EMPTY = {"success": True, "result": {"records": []}}

_API_RESPONSE_BAD_STRUCTURE = {"success": False}


# ---------------------------------------------------------------------------
# _parse_coe unit tests
# ---------------------------------------------------------------------------


def test_parse_coe_ok():
    data = _parse_coe(_API_RESPONSE_OK)
    assert isinstance(data, CoeData)
    assert data.month == "2026-03"
    assert data.bidding_no == 1
    assert data.premiums["A"] == 95501
    assert data.premiums["B"] == 112001
    assert data.premiums["C"] == 73001
    assert data.premiums["D"] == 9801
    assert data.premiums["E"] == 118001


def test_parse_coe_ignores_older_exercise():
    data = _parse_coe(_API_RESPONSE_OK)
    # Only 5 categories from the latest exercise; older record excluded
    assert len(data.premiums) == 5


def test_parse_coe_empty_raises():
    with pytest.raises(UpdateFailed, match="no records"):
        _parse_coe(_API_RESPONSE_EMPTY)


def test_parse_coe_bad_structure_raises():
    with pytest.raises(UpdateFailed):
        _parse_coe(_API_RESPONSE_BAD_STRUCTURE)


def test_parse_coe_two_bidding_exercises_picks_latest():
    payload = {
        "result": {
            "records": [
                {
                    "month": "2026-03",
                    "bidding_no": "2",
                    "vehicle_class": "Category A",
                    "premium": 99001,
                },
                {
                    "month": "2026-03",
                    "bidding_no": "1",
                    "vehicle_class": "Category A",
                    "premium": 95001,
                },
            ]
        }
    }
    data = _parse_coe(payload)
    assert data.bidding_no == 2
    assert data.premiums["A"] == 99001


def test_backoff_delay_seconds_exponential():
    assert _backoff_delay_seconds(1) == 1
    assert _backoff_delay_seconds(2) == 2
    assert _backoff_delay_seconds(3) == 4


# ---------------------------------------------------------------------------
# CoeCoordinator fetch tests (mock HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coe_coordinator_success():
    from custom_components.singapore.coe_coordinator import CoeCoordinator

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=_API_RESPONSE_OK)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = CoeCoordinator(hass)

    with patch(
        "custom_components.singapore.coe_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.data.premiums["A"] == 95501
    assert coordinator.data.month == "2026-03"
    assert coordinator.data.bidding_no == 1


@pytest.mark.asyncio
async def test_coe_coordinator_http_error():
    from custom_components.singapore.coe_coordinator import CoeCoordinator

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = CoeCoordinator(hass)

    with patch(
        "custom_components.singapore.coe_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coe_coordinator_network_error():
    from custom_components.singapore.coe_coordinator import CoeCoordinator

    hass = MagicMock()
    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=Exception("Connection refused"))

    coordinator = CoeCoordinator(hass)

    with patch(
        "custom_components.singapore.coe_coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coe_coordinator_http_error_uses_last_known_data():
    from custom_components.singapore.coe_coordinator import CoeCoordinator

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = CoeCoordinator(hass)
    coordinator.data = CoeData(
        premiums={"A": 95501},
        month="2026-03",
        bidding_no=1,
    )

    with (
        patch(
            "custom_components.singapore.coe_coordinator.async_get_clientsession",
            return_value=mock_session,
        ),
        patch("custom_components.singapore.coe_coordinator.asyncio.sleep", AsyncMock()),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.premiums["A"] == 95501
