"""Tests for the Singapore Hello World config flow."""
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.singapore_hello import DOMAIN


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """Test the full user config flow creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Test Hello"},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Hello"
    assert result["data"] == {CONF_NAME: "Test Hello"}


async def test_user_flow_duplicate_aborts(hass: HomeAssistant) -> None:
    """Test that configuring the same name twice aborts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "My Integration"},
    )

    # Second attempt with the same name
    result2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {CONF_NAME: "My Integration"},
    )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"
