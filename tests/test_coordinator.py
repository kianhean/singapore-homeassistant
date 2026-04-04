"""Tests for SP Group tariff coordinator and HTML parser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.coordinator import TariffData, _parse_tariff

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_HTML_FULL = """
<html><body>
<h2>Tariffs – 1 January 2025 to 31 March 2025</h2>
<h3>Electricity</h3>
<table>
  <thead><tr><th>Component</th><th>Rate (¢/kWh)</th></tr></thead>
  <tbody>
    <tr><td>Energy</td><td>14.32</td></tr>
    <tr><td>Network</td><td>7.61</td></tr>
    <tr><td>Market Support Services</td><td>0.43</td></tr>
    <tr><td>Total (incl. GST)</td><td>29.29</td></tr>
  </tbody>
</table>
<h3>Gas</h3>
<table>
  <tbody>
    <tr><td>Gas tariff (incl. GST)</td><td>20.14</td></tr>
  </tbody>
</table>
<h3>Water</h3>
<table>
  <tbody>
    <tr><td>Water tariff (incl. GST)</td><td>3.69</td></tr>
  </tbody>
</table>
</body></html>
"""

_HTML_TEXT_ONLY = """
<html><body>
<p>Effective 1 April 2025 to 30 June 2025</p>
<p>Electricity total: 28.54 cents/kWh</p>
<p>Network costs: 7.50 cents/kWh</p>
<p>Gas tariff: 19.80 cents/kWh</p>
<p>Water tariff: 3.50 SGD/m3</p>
</body></html>
"""

_HTML_NO_PRICE = """
<html><body><p>Service unavailable.</p></body></html>
"""

# SP Group banner format as of 2026 (Next.js SSR, no tables)
_HTML_BANNER = """
<html><body>
<p>29.72 cents/kWh 27.27 cents/kWh (w/o GST) ELECTRICITY TARIFF (wef 1 Apr - 30 Jun 26)</p>
<p>23.89 cents/kWh 21.92 cents/kWh (w/o GST) GAS TARIFF (wef 1 Apr - 30 Jun 26)</p>
<p>$1.56 or $1.97/m 3 $1.43 or $1.81/m 3 (w/o GST) WATER TARIFF (&lt;40m 3 or &gt; 40m 3)</p>
<p>Network charges 6.25 cents/kWh</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# TariffData unit tests (no I/O)
# ---------------------------------------------------------------------------


def test_solar_export_price():
    data = TariffData(
        electricity_price=29.29,
        network_cost=7.61,
        gas_price=20.14,
        water_price=3.69,
        quarter="Q1",
        year=2025,
    )
    assert data.solar_export_price == round(29.29 - 7.61, 2)


def test_solar_export_price_zero_network():
    data = TariffData(
        electricity_price=29.29,
        network_cost=0.0,
        gas_price=0.0,
        water_price=0.0,
        quarter="Q1",
        year=2025,
    )
    assert data.solar_export_price == 29.29


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_full_html():
    data = _parse_tariff(_HTML_FULL)
    assert data.electricity_price == 29.29
    assert data.network_cost == 7.61
    assert data.gas_price == 20.14
    assert data.water_price == 3.69
    assert data.quarter == "Q1"
    assert data.year == 2025


def test_parse_text_fallback():
    data = _parse_tariff(_HTML_TEXT_ONLY)
    assert data.electricity_price == 28.54
    assert data.network_cost == 7.50
    assert data.quarter == "Q2"
    assert data.year == 2025


def test_parse_raises_when_no_electricity():
    with pytest.raises(UpdateFailed, match="Could not find electricity price"):
        _parse_tariff(_HTML_NO_PRICE)


def test_parse_missing_gas_defaults_zero():
    html = """
    <html><body>
    <p>1 January 2025 to 31 March 2025</p>
    <table><tr><td>Total</td><td>29.29</td></tr></table>
    </body></html>
    """
    data = _parse_tariff(html)
    assert data.gas_price == 0.0


def test_parse_missing_water_defaults_zero():
    html = """
    <html><body>
    <p>1 January 2025 to 31 March 2025</p>
    <table><tr><td>Total</td><td>29.29</td></tr></table>
    </body></html>
    """
    data = _parse_tariff(html)
    assert data.water_price == 0.0


def test_parse_banner_format():
    """Parser must handle the 2026 Next.js banner layout."""
    data = _parse_tariff(_HTML_BANNER)
    assert data.electricity_price == 29.72  # with-GST, not 27.27
    assert data.gas_price == 23.89  # with-GST, not 21.92
    assert data.water_price == 1.56  # lower residential tier
    assert data.network_cost == 6.25
    assert data.quarter == "Q2"
    assert data.year == 2026


def test_parse_banner_excludes_ex_gst():
    """Electricity and gas must be the with-GST values, not the w/o GST ones."""
    data = _parse_tariff(_HTML_BANNER)
    assert data.electricity_price != 27.27
    assert data.gas_price != 21.92


def test_parse_unknown_quarter():
    html = (
        "<html><body><table><tr><td>Total</td><td>27.00</td></tr></table></body></html>"
    )
    data = _parse_tariff(html)
    assert data.quarter == "Unknown"
    assert data.year == 0


@pytest.mark.parametrize(
    "quarter,date_str",
    [
        ("Q1", "1 January 2025 to 31 March 2025"),
        ("Q2", "1 April 2025 to 30 June 2025"),
        ("Q3", "1 July 2025 to 30 September 2025"),
        ("Q4", "1 October 2025 to 31 December 2025"),
    ],
)
def test_parse_all_quarters(quarter, date_str):
    html = f"""
    <html><body>
    <p>{date_str}</p>
    <table><tr><td>Total</td><td>29.00</td></tr></table>
    </body></html>
    """
    data = _parse_tariff(html)
    assert data.quarter == quarter
    assert data.year == 2025


# ---------------------------------------------------------------------------
# Coordinator fetch tests (mock HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_success():
    from custom_components.singapore.coordinator import SPGroupCoordinator

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value=_HTML_FULL)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = SPGroupCoordinator(hass)

    with patch(
        "custom_components.singapore.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.data.electricity_price == 29.29
    assert coordinator.data.gas_price == 20.14
    assert coordinator.data.water_price == 3.69


@pytest.mark.asyncio
async def test_coordinator_http_error():
    from custom_components.singapore.coordinator import SPGroupCoordinator

    hass = MagicMock()

    mock_response = AsyncMock()
    mock_response.status = 403
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)

    coordinator = SPGroupCoordinator(hass)

    with patch(
        "custom_components.singapore.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_network_error():
    from custom_components.singapore.coordinator import SPGroupCoordinator

    hass = MagicMock()
    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=Exception("Connection refused"))

    coordinator = SPGroupCoordinator(hass)

    with patch(
        "custom_components.singapore.coordinator.async_get_clientsession",
        return_value=mock_session,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
