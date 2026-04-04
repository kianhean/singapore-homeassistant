"""Tests for SP Group tariff coordinator and HTML parser."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.coordinator import (
    SPGroupCoordinator,
    TariffData,
    _parse_tariff,
)

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_HTML_TABLE = """
<html><body>
<h2>Electricity Tariff – 1 January 2025 to 31 March 2025</h2>
<table>
  <thead><tr><th>Component</th><th>Rate (¢/kWh)</th></tr></thead>
  <tbody>
    <tr><td>Energy</td><td>14.32</td></tr>
    <tr><td>Network</td><td>7.61</td></tr>
    <tr><td>Market Support Services</td><td>0.43</td></tr>
    <tr><td>Power System Operator</td><td>0.22</td></tr>
    <tr><td>Market Admin</td><td>0.08</td></tr>
    <tr><td>Total (incl. GST)</td><td>29.29</td></tr>
  </tbody>
</table>
</body></html>
"""

_HTML_NO_TABLE = """
<html><body>
<p>Current Residential Electricity Tariff</p>
<p>Effective 1 April 2025 to 30 June 2025</p>
<p>Network: 7.50 cents/kWh</p>
<p>Total: 28.54 cents/kWh</p>
</body></html>
"""

_HTML_UNPARSEABLE = """
<html><body><p>Service unavailable. Please try again.</p></body></html>
"""

_HTML_NO_NETWORK = """
<html><body>
<h2>1 January 2025 to 31 March 2025</h2>
<table>
  <tbody>
    <tr><td>Total (incl. GST)</td><td>29.29</td></tr>
  </tbody>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


def test_parse_tariff_from_table():
    data = _parse_tariff(_HTML_TABLE)
    assert data.price == 29.29
    assert data.network_cost == 7.61
    assert data.quarter == "Q1"
    assert data.year == 2025


def test_parse_tariff_from_text():
    data = _parse_tariff(_HTML_NO_TABLE)
    assert data.price == 28.54
    assert data.network_cost == 7.50
    assert data.quarter == "Q2"
    assert data.year == 2025


def test_solar_export_price_calculation():
    data = _parse_tariff(_HTML_TABLE)
    assert data.solar_export_price == round(29.29 - 7.61, 2)


def test_parse_tariff_raises_on_no_price():
    with pytest.raises(UpdateFailed, match="Could not find electricity price"):
        _parse_tariff(_HTML_UNPARSEABLE)


def test_parse_tariff_unknown_quarter():
    html = "<html><body><p>Total: 27.00 cents/kWh</p></body></html>"
    data = _parse_tariff(html)
    assert data.quarter == "Unknown"
    assert data.price == 27.00


def test_parse_tariff_missing_network_defaults_zero():
    """Network cost defaults to 0 when not found; solar export price = total."""
    data = _parse_tariff(_HTML_NO_NETWORK)
    assert data.network_cost == 0.0
    assert data.solar_export_price == data.price


# ---------------------------------------------------------------------------
# Coordinator integration tests
# ---------------------------------------------------------------------------


async def test_coordinator_fetches_and_parses(hass: HomeAssistant) -> None:
    coordinator = SPGroupCoordinator(hass)

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value=_HTML_TABLE)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    with patch(
        "custom_components.singapore_hello.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.data is not None
    assert coordinator.data.price == 29.29
    assert coordinator.data.network_cost == 7.61
    assert coordinator.data.quarter == "Q1"
    assert coordinator.data.year == 2025


async def test_coordinator_raises_on_http_error(hass: HomeAssistant) -> None:
    coordinator = SPGroupCoordinator(hass)

    mock_response = AsyncMock()
    mock_response.status = 403
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    with patch(
        "custom_components.singapore_hello.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False


async def test_coordinator_raises_on_network_error(hass: HomeAssistant) -> None:
    coordinator = SPGroupCoordinator(hass)

    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=Exception("Connection refused"))

    with patch(
        "custom_components.singapore_hello.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
