"""Tests for config flow constants and SP Services re-auth behavior."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_NAME

from custom_components.singapore.config_flow import (
    STEP_USER_DATA_SCHEMA,
    SingaporeElectricityConfigFlow,
)


def test_schema_has_name_field():
    """Config flow schema includes a name field."""
    assert CONF_NAME in STEP_USER_DATA_SCHEMA.schema


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
