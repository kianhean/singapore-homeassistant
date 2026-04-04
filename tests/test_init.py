"""Tests for Singapore electricity tariff integration setup."""
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.singapore_hello import DOMAIN
from custom_components.singapore_hello.coordinator import SPGroupCoordinator

_HTML_TABLE = """
<html><body>
<h2>Electricity Tariff – 1 January 2025 to 31 March 2025</h2>
<table>
  <tbody>
    <tr><td>Total (incl. GST)</td><td>29.29</td></tr>
  </tbody>
</table>
</body></html>
"""


def _mock_session(html: str) -> MagicMock:
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value=html)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=mock_response)
    return session


async def test_setup_entry_stores_coordinator(hass: HomeAssistant) -> None:
    """Setup stores an SPGroupCoordinator in hass.data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Test"},
        unique_id="Test",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.singapore_hello.coordinator.async_get_clientsession",
        return_value=_mock_session(_HTML_TABLE),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert DOMAIN in hass.data
    assert isinstance(hass.data[DOMAIN][entry.entry_id], SPGroupCoordinator)


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Unloading removes the coordinator from hass.data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Test"},
        unique_id="Test",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.singapore_hello.coordinator.async_get_clientsession",
        return_value=_mock_session(_HTML_TABLE),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
