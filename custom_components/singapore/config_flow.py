"""Config flow for the Singapore integration.

Initial setup (config flow)
---------------------------
Step 1 (user): Enter an integration name.  No credentials required.
  All public data sources (tariffs, COE, weather, trains, holidays) are set up
  immediately.  SP Services household-usage sensors are NOT created yet.

Configuring SP Services later (options flow)
--------------------------------------------
After setup the user can open the integration's "Configure" dialog to add or
update SP Services credentials.  The flow mirrors the SmartThinQ pattern:

  Step 1 (init): Enter username + password.
    • Both blank → remove SP Services credentials and reload.
    • Both filled → trigger OTP SMS, continue to step 2.
  Step 2 (otp): Enter the OTP to complete login and store the token.
    → Updates entry.data with credentials + token, then reloads.

Re-authentication (triggered automatically by HA when ConfigEntryAuthFailed is raised)
---------------------------------------------------------------------------------------
Step 1 (reauth_confirm): Inform the user their session has expired.
  • Yes → route back to async_step_user (which in reauth context updates the
           existing entry via async_update_reload_and_abort).
  • No  → attempt a reload with the existing token.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from . import DOMAIN
from .sp_services_coordinator import SpServicesClient

_LOGGER = logging.getLogger(__name__)

CONF_REAUTH_CONFIRM = "reauth_confirm"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore"): str,
    }
)

STEP_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)

STEP_OTP_SCHEMA = vol.Schema(
    {
        vol.Required("otp"): str,
    }
)


# ---------------------------------------------------------------------------
# Config flow (initial setup)
# ---------------------------------------------------------------------------


class SingaporeElectricityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for the Singapore integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_name: str = ""
        self._sp_username: str = ""
        self._sp_password: str = ""
        self._login_response: dict[str, Any] = {}
        self._sp_client: SpServicesClient | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SingaporeOptionsFlow:
        """Return the options flow handler."""
        return SingaporeOptionsFlow(config_entry)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _is_reauth(self) -> bool:
        return self.source == SOURCE_REAUTH

    # ------------------------------------------------------------------
    # Step 1: name only
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the integration name.

        In reauth context (reached from reauth_confirm) this also handles
        SP Services credential re-entry via the pre-filled credentials schema.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            if self._is_reauth:
                # In reauth we re-enter SP credentials (name stays the same).
                name = self._get_reauth_entry().title
                username = user_input.get(CONF_USERNAME, "").strip()
                password = user_input.get(CONF_PASSWORD, "").strip()
            else:
                name = user_input.get(CONF_NAME, "").strip()
                username = ""
                password = ""

            if not name:
                errors["base"] = "empty_name"
            elif len(name) > 64:
                errors["base"] = "name_too_long"
            elif self._is_reauth and bool(username) != bool(password):
                errors["base"] = "credentials_incomplete"
            elif self._is_reauth and username and password:
                self._user_name = name
                self._sp_username = username
                self._sp_password = password
                await self._close_client()
                self._sp_client = SpServicesClient()
                try:
                    self._login_response = await self._sp_client.login(username, password)
                except ValueError:
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("SP Services login error during reauth")
                    errors["base"] = "cannot_connect"

                if not errors:
                    return await self.async_step_otp()
                await self._close_client()
            elif self._is_reauth:
                # No credentials supplied during reauth — just reload as-is.
                return self.async_update_reload_and_abort(self._get_reauth_entry())
            else:
                # Normal initial setup — create entry without SP Services.
                await self.async_set_unique_id(name)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name},
                )

        if self._is_reauth:
            entry = self._get_reauth_entry()
            stored_username = entry.data.get(CONF_USERNAME, "")
            schema = vol.Schema(
                {
                    vol.Optional(CONF_USERNAME, default=stored_username): str,
                    vol.Optional(CONF_PASSWORD): str,
                }
            )
        else:
            schema = STEP_USER_DATA_SCHEMA

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2 (OTP) — reauth only
    # ------------------------------------------------------------------

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the OTP to complete the SP Services login (reauth only)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                if self._sp_client is None:
                    raise RuntimeError("SP Services login session missing during OTP step")
                token = await self._sp_client.verify_otp(
                    user_input["otp"], self._login_response
                )
            except ValueError:
                errors["base"] = "invalid_otp"
            except Exception:
                _LOGGER.exception("SP Services OTP verify error during reauth")
                errors["base"] = "cannot_connect"

            if not errors:
                entry = self._get_reauth_entry()
                await self._close_client()
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_USERNAME: self._sp_username,
                        CONF_PASSWORD: self._sp_password,
                        "sp_token": token,
                    },
                )
            await self._close_client()

        return self.async_show_form(
            step_id="otp",
            data_schema=STEP_OTP_SCHEMA,
            errors=errors,
            description_placeholders={"username": self._sp_username},
        )

    async def _close_client(self) -> None:
        if self._sp_client is not None:
            await self._sp_client.close()
            self._sp_client = None

    # ------------------------------------------------------------------
    # Re-authentication entry point
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Triggered by HA when ConfigEntryAuthFailed is raised."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Inform the user that their SP Services session has expired.

        Mirrors the SmartThinQ pattern:
          Yes → re-enter credentials (async_step_user in reauth context)
          No  → reload entry as-is
        """
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema(
                    {vol.Required(CONF_REAUTH_CONFIRM, default=False): bool}
                ),
                description_placeholders={
                    "username": self._get_reauth_entry().data.get(CONF_USERNAME, "")
                },
            )

        if user_input[CONF_REAUTH_CONFIRM]:
            return await self.async_step_user()

        return self.async_update_reload_and_abort(self._get_reauth_entry())


# ---------------------------------------------------------------------------
# Options flow (post-setup SP Services configuration)
# ---------------------------------------------------------------------------


class SingaporeOptionsFlow(OptionsFlow):
    """Allow the user to add, update, or remove SP Services credentials.

    Accessible via the integration card's "Configure" button.
    Credentials are stored in entry.data (not entry.options) so they
    remain available to the coordinator on reload.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry
        self._sp_username: str = config_entry.data.get(CONF_USERNAME, "")
        self._sp_password: str = ""
        self._login_response: dict[str, Any] = {}
        self._sp_client: SpServicesClient | None = None

    # ------------------------------------------------------------------
    # Step 1: credentials
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show username + password form, pre-filled with existing username."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input.get(CONF_USERNAME, "").strip()
            password = user_input.get(CONF_PASSWORD, "").strip()

            if bool(username) != bool(password):
                errors["base"] = "credentials_incomplete"
            elif username and password:
                self._sp_username = username
                self._sp_password = password
                await self._close_client()
                self._sp_client = SpServicesClient()
                try:
                    self._login_response = await self._sp_client.login(
                        username, password
                    )
                except ValueError:
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("SP Services login error in options flow")
                    errors["base"] = "cannot_connect"

                if not errors:
                    return await self.async_step_otp()
                await self._close_client()
            else:
                # Both blank — remove SP Services credentials and reload.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        k: v
                        for k, v in self.config_entry.data.items()
                        if k not in (CONF_USERNAME, CONF_PASSWORD, "sp_token")
                    },
                )
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Optional(CONF_USERNAME, default=self._sp_username): str,
                vol.Optional(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "configured": "yes" if self._sp_username else "no"
            },
        )

    # ------------------------------------------------------------------
    # Step 2: OTP
    # ------------------------------------------------------------------

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the OTP to complete the SP Services login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                if self._sp_client is None:
                    raise RuntimeError("SP Services login session missing during OTP step")
                token = await self._sp_client.verify_otp(
                    user_input["otp"], self._login_response
                )
            except ValueError:
                errors["base"] = "invalid_otp"
            except Exception:
                _LOGGER.exception("SP Services OTP verify error in options flow")
                errors["base"] = "cannot_connect"

            if not errors:
                # Store credentials in entry.data and reload.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_USERNAME: self._sp_username,
                        CONF_PASSWORD: self._sp_password,
                        "sp_token": token,
                    },
                )
                await self._close_client()
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data={})
            await self._close_client()

        return self.async_show_form(
            step_id="otp",
            data_schema=STEP_OTP_SCHEMA,
            errors=errors,
            description_placeholders={"username": self._sp_username},
        )

    async def _close_client(self) -> None:
        if self._sp_client is not None:
            await self._sp_client.close()
            self._sp_client = None
