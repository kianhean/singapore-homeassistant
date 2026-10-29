"""Config flow for the Singapore integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from sp_services import (
    SessionExpiredError,
    SessionState,
    SpServicesClient,
    SpServicesError,
)

from .const import (
    CONF_ACCOUNT_NO,
    CONF_IMPORT_STATISTICS,
    CONF_SESSION,
    DOMAIN,
    SP_SERVICES_EXPORT_COMMAND,
    SUBENTRY_SP_ACCOUNT,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore Electricity"): str,
    }
)

SP_ACCOUNT_GUIDE_URL = (
    "https://github.com/kianhean/singapore-homeassistant/blob/main/docs/sp_services.md"
)


def _sp_account_schema(import_statistics: bool = True) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SESSION): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_IMPORT_STATISTICS, default=import_statistics): bool,
        }
    )


class SingaporeElectricityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Singapore Electricity Tariff."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """SP Services accounts are added under the single Singapore entry."""
        return {SUBENTRY_SP_ACCOUNT: SpAccountSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors["base"] = "empty_name"
            elif len(name) > 64:
                errors["base"] = "name_too_long"
            else:
                await self.async_set_unique_id(name)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=name, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


async def validate_sp_session(session: str) -> tuple[str, str]:
    """Log in with a pasted session string.

    Returns ``(account_no, refreshed_session)``; the refreshed session is
    stored instead of the pasted one so its 3-day expiry starts now. Raises
    ``ValueError`` for a malformed string and the ``sp_services`` errors
    otherwise.
    """
    state = SessionState.from_string(session)
    client = SpServicesClient(session_state=state)
    try:
        tokens = await client.renew_silently()
        usage = await client.fetch_usage(tokens.access_token)
        if not usage.account_no:
            raise SpServicesError("SP Services returned no account number")
        return usage.account_no, client.export_session().to_string()
    finally:
        await client.close()


class SpAccountSubentryFlow(ConfigSubentryFlow):
    """Add or log in again to an SP Services account."""

    async def _async_validate(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> tuple[str, str] | None:
        try:
            return await validate_sp_session(user_input[CONF_SESSION])
        except ValueError:
            errors[CONF_SESSION] = "invalid_session"
        except SessionExpiredError:
            errors[CONF_SESSION] = "session_expired"
        except SpServicesError:
            errors["base"] = "cannot_connect"
        return None

    def _show_form(
        self, step_id: str, errors: dict[str, str], import_statistics: bool = True
    ) -> SubentryFlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=_sp_account_schema(import_statistics),
            errors=errors,
            description_placeholders={
                "command": SP_SERVICES_EXPORT_COMMAND,
                "guide_url": SP_ACCOUNT_GUIDE_URL,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_validate(user_input, errors)
            if result is not None:
                account_no, session = result
                for subentry in self._get_entry().subentries.values():
                    if subentry.unique_id == account_no:
                        return self.async_abort(reason="already_configured")
                return self.async_create_entry(
                    title=f"SP Services {account_no}",
                    data={
                        CONF_ACCOUNT_NO: account_no,
                        CONF_SESSION: session,
                        CONF_IMPORT_STATISTICS: user_input[CONF_IMPORT_STATISTICS],
                    },
                    unique_id=account_no,
                )
        return self._show_form("user", errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_validate(user_input, errors)
            if result is not None:
                account_no, session = result
                if account_no != subentry.data[CONF_ACCOUNT_NO]:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    data_updates={
                        CONF_SESSION: session,
                        CONF_IMPORT_STATISTICS: user_input[CONF_IMPORT_STATISTICS],
                    },
                )
        return self._show_form(
            "reconfigure",
            errors,
            subentry.data.get(CONF_IMPORT_STATISTICS, True),
        )
