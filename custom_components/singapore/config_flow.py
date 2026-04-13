"""Config flow for Singapore integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME

from . import DOMAIN
from .sp_services_coordinator import CONF_SP_TOKEN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore Electricity"): str,
    }
)

# callback_url is optional — leaving it empty skips SP Services login.
STEP_SP_BROWSER_AUTH_SCHEMA = vol.Schema(
    {
        vol.Optional("callback_url", default=""): str,
    }
)


class SingaporeOptionsFlow(OptionsFlow):
    """Options flow — lets existing users add or refresh their SP Services login."""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry
        self._sp_client = None
        self._browser_auth_url: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_sp_browser_auth(user_input)

    async def async_step_sp_browser_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show browser login URL; accept resulting callback URL."""
        errors: dict[str, str] = {}

        if self._browser_auth_url is None:
            try:
                from sp_services import SpServicesClient

                self._sp_client = SpServicesClient()
                self._browser_auth_url = await self._sp_client.begin_browser_login()
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_cannot_connect"

        if user_input is not None and not errors:
            callback_url = user_input.get("callback_url", "").strip()

            if not callback_url:
                await self._close_sp_client()
                return self.async_abort(reason="no_sp_token")

            try:
                token = await self._sp_client.exchange_callback_url(callback_url)
                await self._close_sp_client()
                return self.async_update_reload_and_abort(
                    self._entry,
                    data={**self._entry.data, CONF_SP_TOKEN: token},
                )
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_invalid_callback"

        return self.async_show_form(
            step_id="sp_browser_auth",
            data_schema=STEP_SP_BROWSER_AUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                "auth_url": self._browser_auth_url or "",
                "callback_url_prefix": "https://services.spservices.sg/callback?fromLogin=true",
            },
        )

    async def _close_sp_client(self) -> None:
        if self._sp_client is not None:
            await self._sp_client.close()
            self._sp_client = None


class SingaporeElectricityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Singapore integration."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> SingaporeOptionsFlow:
        return SingaporeOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._name: str = ""
        self._sp_client = None
        self._browser_auth_url: str | None = None

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
                self._name = name
                return await self.async_step_sp_browser_auth()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_sp_browser_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show Auth0 browser-login URL; accept the resulting callback URL.

        The user opens ``auth_url`` in a browser, completes the SP Services
        login (including MFA), then copies the full redirect URL from the
        browser address bar and pastes it here.  Leave the field empty to
        skip SP Services and set up the integration without usage sensors.
        """
        errors: dict[str, str] = {}

        # Start the browser auth session exactly once.
        if self._browser_auth_url is None:
            try:
                from sp_services import SpServicesClient

                self._sp_client = SpServicesClient()
                self._browser_auth_url = await self._sp_client.begin_browser_login()
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_cannot_connect"

        if user_input is not None and not errors:
            callback_url = user_input.get("callback_url", "").strip()

            if not callback_url:
                # User chose to skip SP Services login.
                await self._close_sp_client()
                return await self._create_entry()

            try:
                token = await self._exchange_callback_for_token(callback_url)
                await self._close_sp_client()
                return await self._create_entry(sp_token=token)
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_invalid_callback"

        return self.async_show_form(
            step_id="sp_browser_auth",
            data_schema=STEP_SP_BROWSER_AUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                "auth_url": self._browser_auth_url or "",
                "callback_url_prefix": "https://services.spservices.sg/callback?fromLogin=true",
            },
        )

    async def _close_sp_client(self) -> None:
        if self._sp_client is not None:
            await self._sp_client.close()
            self._sp_client = None

    async def _exchange_callback_for_token(
        self, callback_url: str, *, fetch_usage: bool = False
    ) -> str:
        """Exchange callback URL for token and optionally validate usage fetch."""
        token = await self._sp_client.exchange_callback_url(callback_url)
        if fetch_usage:
            await self._sp_client.fetch_usage(token)
        return token

    async def _create_entry(self, sp_token: str | None = None) -> ConfigFlowResult:
        """Finalise and create the config entry."""
        data: dict[str, Any] = {CONF_NAME: self._name}
        if sp_token:
            data[CONF_SP_TOKEN] = sp_token

        await self.async_set_unique_id(self._name)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._name, data=data)

    # ------------------------------------------------------------------
    # Re-authentication (triggered when ConfigEntryAuthFailed is raised)
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start re-auth flow after token expiry."""
        return await self.async_step_reauth_browser_auth()

    async def async_step_reauth_browser_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-authenticate via browser callback URL."""
        errors: dict[str, str] = {}

        if self._browser_auth_url is None:
            try:
                from sp_services import SpServicesClient

                self._sp_client = SpServicesClient()
                self._browser_auth_url = await self._sp_client.begin_browser_login()
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_cannot_connect"

        if user_input is not None and not errors:
            try:
                token = await self._exchange_callback_for_token(
                    user_input["callback_url"].strip(),
                    fetch_usage=True,
                )
                await self._close_sp_client()
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_SP_TOKEN: token},
                )
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_invalid_callback"

        return self.async_show_form(
            step_id="reauth_browser_auth",
            data_schema=vol.Schema({vol.Required("callback_url"): str}),
            errors=errors,
            description_placeholders={
                "auth_url": self._browser_auth_url or "",
                "callback_url_prefix": "https://services.spservices.sg/callback?fromLogin=true",
            },
        )
