"""Tests for the config and options flows."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME

from custom_components.singapore.config_flow import (
    CONF_CALLBACK_URL,
    CONF_LINK_SP,
    STEP_USER_DATA_SCHEMA,
    SingaporeElectricityConfigFlow,
    SingaporeOptionsFlow,
)
from custom_components.singapore.sp_usage_client import (
    SPUsageAuthError,
    TokenSet,
)
from custom_components.singapore.usage_coordinator import (
    CONF_SP_ACCESS_TOKEN,
    CONF_SP_ACCESS_TOKEN_EXPIRES_AT,
    CONF_SP_ACCOUNT_NO,
    CONF_SP_REFRESH_TOKEN,
)

_CALLBACK_URL = "https://services.spservices.sg/callback?code=abc&state=xyz"
_ONE_ACCOUNT = [{"accountNo": "8949049293"}]
_TWO_ACCOUNTS = [{"accountNo": "111"}, {"accountNo": "222"}]


def test_schema_has_name_field():
    """Config flow schema includes a name field."""
    assert CONF_NAME in STEP_USER_DATA_SCHEMA.schema


def test_schema_has_optional_sp_link():
    """Linking an SP Services account is opt-in, so the entry works without it."""
    assert CONF_LINK_SP in STEP_USER_DATA_SCHEMA.schema


def _flow():
    flow = SingaporeElectricityConfigFlow()
    flow.hass = MagicMock()
    return flow


def _patch_login(
    token=TokenSet(access_token="at", refresh_token="rt"), accounts=_ONE_ACCOUNT
):
    return (
        patch(
            "custom_components.singapore.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.config_flow.async_exchange_callback_url",
            AsyncMock(return_value=token),
        ),
        patch(
            "custom_components.singapore.config_flow.async_list_accounts",
            AsyncMock(return_value=accounts),
        ),
    )


@pytest.mark.asyncio
async def test_user_step_without_sp_creates_entry():
    result = await _flow().async_step_user(
        {CONF_NAME: "Singapore", CONF_LINK_SP: False}
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_NAME: "Singapore"}


@pytest.mark.asyncio
async def test_user_step_rejects_empty_name():
    result = await _flow().async_step_user({CONF_NAME: "   ", CONF_LINK_SP: False})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "empty_name"


@pytest.mark.asyncio
async def test_user_step_with_sp_shows_authorize_url():
    result = await _flow().async_step_user({CONF_NAME: "Singapore", CONF_LINK_SP: True})

    assert result["step_id"] == "sp_login"
    authorize_url = result["description_placeholders"]["authorize_url"]
    assert authorize_url.startswith("https://identity.spdigital.auth0.com/authorize?")


@pytest.mark.asyncio
async def test_sp_login_single_account_creates_entry():
    flow = _flow()
    await flow.async_step_user({CONF_NAME: "Singapore", CONF_LINK_SP: True})

    with _patch_login()[0], _patch_login()[1], _patch_login()[2]:
        result = await flow.async_step_sp_login({CONF_CALLBACK_URL: _CALLBACK_URL})

    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_NAME: "Singapore",
        CONF_SP_REFRESH_TOKEN: "rt",
        CONF_SP_ACCESS_TOKEN: "at",
        CONF_SP_ACCESS_TOKEN_EXPIRES_AT: None,
        CONF_SP_ACCOUNT_NO: "8949049293",
    }


@pytest.mark.asyncio
async def test_sp_login_multiple_accounts_asks_which():
    flow = _flow()
    await flow.async_step_user({CONF_NAME: "Singapore", CONF_LINK_SP: True})

    patches = _patch_login(accounts=_TWO_ACCOUNTS)
    with patches[0], patches[1], patches[2]:
        result = await flow.async_step_sp_login({CONF_CALLBACK_URL: _CALLBACK_URL})
        assert result["step_id"] == "sp_account"

        chosen = await flow.async_step_sp_account({CONF_SP_ACCOUNT_NO: "222"})

    assert chosen["type"] == "create_entry"
    assert chosen["data"][CONF_SP_ACCOUNT_NO] == "222"


@pytest.mark.asyncio
async def test_sp_login_bad_callback_url_restarts_login():
    flow = _flow()
    await flow.async_step_user({CONF_NAME: "Singapore", CONF_LINK_SP: True})
    first_url = flow._login.authorize_url

    with (
        patch(
            "custom_components.singapore.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.singapore.config_flow.async_exchange_callback_url",
            AsyncMock(side_effect=SPUsageAuthError("state mismatch")),
        ),
    ):
        result = await flow.async_step_sp_login({CONF_CALLBACK_URL: "junk"})

    assert result["errors"]["base"] == "invalid_callback"
    # A burnt login attempt must not be retried with the same PKCE state.
    assert result["description_placeholders"]["authorize_url"] != first_url


@pytest.mark.asyncio
async def test_sp_login_without_refresh_token_still_links():
    """SP does not always issue a refresh token; the access token still works.

    Rejecting the login would leave those accounts with no usage data at all;
    instead the entry is created and reauth asks for a new sign-in on expiry.
    """
    flow = _flow()
    await flow.async_step_user({CONF_NAME: "Singapore", CONF_LINK_SP: True})

    expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
    patches = _patch_login(
        token=TokenSet(access_token="at", refresh_token=None, expires_at=expires_at)
    )
    with patches[0], patches[1], patches[2]:
        result = await flow.async_step_sp_login({CONF_CALLBACK_URL: _CALLBACK_URL})

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SP_ACCESS_TOKEN] == "at"
    assert result["data"][CONF_SP_ACCESS_TOKEN_EXPIRES_AT] == expires_at.isoformat()
    assert result["data"][CONF_SP_REFRESH_TOKEN] is None


@pytest.mark.asyncio
async def test_sp_login_without_accounts_is_rejected():
    flow = _flow()
    await flow.async_step_user({CONF_NAME: "Singapore", CONF_LINK_SP: True})

    patches = _patch_login(accounts=[])
    with patches[0], patches[1], patches[2]:
        result = await flow.async_step_sp_login({CONF_CALLBACK_URL: _CALLBACK_URL})

    assert result["errors"]["base"] == "no_accounts"


@pytest.mark.asyncio
async def test_reauth_keeps_selected_account_and_reloads():
    entry = ConfigEntry(
        data={
            CONF_NAME: "Singapore",
            CONF_SP_REFRESH_TOKEN: "old",
            CONF_SP_ACCOUNT_NO: "222",
        }
    )
    flow = _flow()
    flow.context = {"entry_id": entry.entry_id}
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    await flow.async_step_reauth(dict(entry.data))

    patches = _patch_login(accounts=_TWO_ACCOUNTS)
    with patches[0], patches[1], patches[2]:
        result = await flow.async_step_reauth_confirm(
            {CONF_CALLBACK_URL: _CALLBACK_URL}
        )

    assert result["reason"] == "reauth_successful"
    updated = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert updated[CONF_SP_REFRESH_TOKEN] == "rt"
    assert updated[CONF_SP_ACCOUNT_NO] == "222"
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with(
        entry.entry_id
    )


@pytest.mark.asyncio
async def test_reauth_falls_back_when_account_no_longer_linked():
    entry = ConfigEntry(data={CONF_SP_REFRESH_TOKEN: "old", CONF_SP_ACCOUNT_NO: "gone"})
    flow = _flow()
    flow.context = {"entry_id": entry.entry_id}
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    await flow.async_step_reauth(dict(entry.data))

    patches = _patch_login(accounts=_TWO_ACCOUNTS)
    with patches[0], patches[1], patches[2]:
        await flow.async_step_reauth_confirm({CONF_CALLBACK_URL: _CALLBACK_URL})

    updated = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert updated[CONF_SP_ACCOUNT_NO] == "111"


def _options_flow(entry):
    flow = SingaporeOptionsFlow()
    flow.hass = MagicMock()
    flow.config_entry = entry
    return flow


@pytest.mark.asyncio
async def test_options_unlinked_entry_goes_straight_to_login():
    flow = _options_flow(ConfigEntry(data={CONF_NAME: "Singapore"}))

    result = await flow.async_step_init()

    assert result["step_id"] == "sp_login"


@pytest.mark.asyncio
async def test_options_linked_entry_offers_relink_and_unlink():
    flow = _options_flow(ConfigEntry(data={CONF_SP_REFRESH_TOKEN: "rt"}))

    result = await flow.async_step_init()

    assert result["type"] == "menu"
    assert result["menu_options"] == ["sp_login", "sp_unlink"]


@pytest.mark.asyncio
async def test_options_link_stores_token_and_reloads():
    entry = ConfigEntry(data={CONF_NAME: "Singapore"})
    flow = _options_flow(entry)
    await flow.async_step_init()

    patches = _patch_login()
    with patches[0], patches[1], patches[2]:
        await flow.async_step_sp_login({CONF_CALLBACK_URL: _CALLBACK_URL})

    stored = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert stored[CONF_SP_REFRESH_TOKEN] == "rt"
    assert stored[CONF_NAME] == "Singapore"
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with(
        entry.entry_id
    )


@pytest.mark.asyncio
async def test_options_unlink_drops_credentials():
    entry = ConfigEntry(
        data={
            CONF_NAME: "Singapore",
            CONF_SP_REFRESH_TOKEN: "rt",
            CONF_SP_ACCOUNT_NO: "111",
        }
    )
    flow = _options_flow(entry)

    await flow.async_step_sp_unlink()

    stored = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert stored == {CONF_NAME: "Singapore"}
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with(
        entry.entry_id
    )


@pytest.mark.asyncio
async def test_relink_without_refresh_token_clears_the_stale_one():
    """A dead refresh token must not survive a re-link that returned none."""
    entry = ConfigEntry(
        data={
            CONF_NAME: "Singapore",
            CONF_SP_REFRESH_TOKEN: "dead",
            CONF_SP_ACCOUNT_NO: "111",
        }
    )
    flow = _options_flow(entry)
    await flow.async_step_init()
    await flow.async_step_sp_login()

    patches = _patch_login(token=TokenSet(access_token="fresh", refresh_token=None))
    with patches[0], patches[1], patches[2]:
        await flow.async_step_sp_login({CONF_CALLBACK_URL: _CALLBACK_URL})

    stored = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert stored[CONF_SP_REFRESH_TOKEN] is None
    assert stored[CONF_SP_ACCESS_TOKEN] == "fresh"
