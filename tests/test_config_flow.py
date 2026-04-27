"""Tests for config flow constants and SP Services re-auth behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_NAME

from custom_components.singapore.config_flow import (
    STEP_USER_DATA_SCHEMA,
    SingaporeElectricityConfigFlow,
    _has_callback_code_and_state,
    _normalise_callback_url,
)
from custom_components.singapore.sp_services_coordinator import (
    CONF_SP_CALLBACK_URL,
    CONF_SP_TOKEN,
)


def test_schema_has_name_field():
    """Config flow schema includes a name field."""
    assert CONF_NAME in STEP_USER_DATA_SCHEMA.schema


def test_normalise_callback_url_unescapes_and_trims():
    callback = "  <https://services.spservices.sg/callback?fromLogin=true&amp;code=x&amp;state=y>  "

    assert _normalise_callback_url(callback) == (
        "https://services.spservices.sg/callback?fromLogin=true&code=x&state=y"
    )


def test_normalise_callback_url_moves_fragment_params_to_query():
    callback = "https://services.spservices.sg/callback?fromLogin=true#code=x&state=y"

    assert _normalise_callback_url(callback) == (
        "https://services.spservices.sg/callback?fromLogin=true&code=x&state=y"
    )


def test_has_callback_code_and_state():
    assert _has_callback_code_and_state(
        "https://services.spservices.sg/callback?code=x&state=y"
    )
    assert not _has_callback_code_and_state(
        "https://identity.spdigital.auth0.com/authorize?client_id=fixed"
    )


@pytest.mark.asyncio
async def test_exchange_callback_fetches_usage_when_requested():
    flow = SingaporeElectricityConfigFlow()
    flow._sp_client = AsyncMock()
    flow._sp_client.exchange_callback_url = AsyncMock(return_value="token-123")
    flow._sp_client.fetch_usage = AsyncMock()

    token = await flow._exchange_callback_for_token(
        "https://services.spservices.sg/callback?code=x&state=y",
        fetch_usage=True,
    )

    assert token == "token-123"
    flow._sp_client.exchange_callback_url.assert_awaited_once()
    flow._sp_client.fetch_usage.assert_awaited_once_with("token-123")


@pytest.mark.asyncio
async def test_reconfigure_blank_callback_removes_saved_sp_token():
    flow = SingaporeElectricityConfigFlow()
    flow._browser_auth_url = "https://example.com/login"
    flow._close_sp_client = AsyncMock()
    flow._get_reconfigure_entry = lambda: SimpleNamespace(
        data={
            CONF_NAME: "Singapore Electricity",
            CONF_SP_TOKEN: "old-token",
            CONF_SP_CALLBACK_URL: "https://services.spservices.sg/callback?code=x&state=y",
        }
    )
    flow.async_update_reload_and_abort = lambda entry, data: {
        "entry": entry,
        "data": data,
    }

    result = await flow.async_step_reconfigure_sp_browser_auth({"callback_url": ""})

    assert result["data"] == {CONF_NAME: "Singapore Electricity"}
    flow._close_sp_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_entry_stores_callback_with_token():
    flow = SingaporeElectricityConfigFlow()
    flow._name = "Singapore Electricity"
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda title, data: {"title": title, "data": data}

    result = await flow._create_entry(
        sp_token="token-123",
        sp_callback_url="https://services.spservices.sg/callback?code=x&state=y",
    )

    assert result["data"][CONF_SP_TOKEN] == "token-123"
    assert (
        result["data"][CONF_SP_CALLBACK_URL]
        == "https://services.spservices.sg/callback?code=x&state=y"
    )
