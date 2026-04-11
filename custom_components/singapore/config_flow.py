"""Config flow for the Singapore integration.

Initial setup
-------------
Step 1 (user): Enter an integration name plus optional SP Services credentials.
  - If both username + password are provided the flow triggers an OTP SMS and
    moves to step 2.
  - If credentials are left blank the entry is created without SP Services
    household-usage sensors.

Step 2 (otp): Enter the OTP to complete the SP Services login and store the token.

Re-authentication (triggered automatically by HA when ConfigEntryAuthFailed is raised)
---------------------------------------------------------------------------------------
Step 1 (reauth_confirm): Informs the user their session has expired and asks them to
  confirm they want to re-enter credentials.  Choosing "Yes" routes back to the regular
  user step which, when running in reauth context, updates the existing entry and reloads
  rather than creating a new one.  Choosing "No" just reloads the entry as-is.

This mirrors the pattern used by ha-smartthinq-sensors.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME

from . import DOMAIN
from .sp_services_coordinator import SpServicesClient

_LOGGER = logging.getLogger(__name__)

CONF_REAUTH_CONFIRM = "reauth_confirm"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore"): str,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)

STEP_OTP_SCHEMA = vol.Schema(
    {
        vol.Required("otp"): str,
    }
)


class SingaporeElectricityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for the Singapore integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_name: str = ""
        self._sp_username: str = ""
        self._sp_password: str = ""
        # Raw JSON body returned by the SP Services login endpoint; passed
        # unchanged to verify_otp() so the server can match the OTP to the
        # correct in-flight session.
        self._login_response: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _is_reauth(self) -> bool:
        return self.source == SOURCE_REAUTH

    # ------------------------------------------------------------------
    # Initial setup — step 1
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect integration name and optional SP Services credentials.

        This step is also reached from the reauth flow when the user
        chooses to re-enter their credentials.  In that case we skip the
        unique-id check and update the existing entry instead of creating
        a new one.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input.get(CONF_NAME, "").strip()
            username = user_input.get(CONF_USERNAME, "").strip()
            password = user_input.get(CONF_PASSWORD, "").strip()

            # In reauth context the name comes from the existing entry.
            if self._is_reauth:
                name = self._get_reauth_entry().title

            if not name:
                errors["base"] = "empty_name"
            elif len(name) > 64:
                errors["base"] = "name_too_long"
            elif bool(username) != bool(password):
                errors["base"] = "credentials_incomplete"
            elif username and password:
                self._user_name = name
                self._sp_username = username
                self._sp_password = password
                client = SpServicesClient()
                try:
                    self._login_response = await client.login(username, password)
                except ValueError:
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("SP Services login error")
                    errors["base"] = "cannot_connect"
                finally:
                    await client.close()

                if not errors:
                    return await self.async_step_otp()
            else:
                # No credentials — create / update entry without SP Services.
                if self._is_reauth:
                    entry = self._get_reauth_entry()
                    return self.async_update_reload_and_abort(
                        entry,
                        data={k: v for k, v in entry.data.items()},
                    )
                await self.async_set_unique_id(name)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name},
                )

        # Pre-fill username from existing entry when reauthenticating.
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
    # Initial setup / reauth — step 2 (OTP)
    # ------------------------------------------------------------------

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the OTP to complete the SP Services login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = SpServicesClient()
            try:
                token = await client.verify_otp(user_input["otp"], self._login_response)
            except ValueError:
                errors["base"] = "invalid_otp"
            except Exception:
                _LOGGER.exception("SP Services OTP verify error")
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

            if not errors:
                new_data = {
                    CONF_NAME: self._user_name,
                    CONF_USERNAME: self._sp_username,
                    CONF_PASSWORD: self._sp_password,
                    "sp_token": token,
                }
                if self._is_reauth:
                    # Merge with existing entry data and reload.
                    entry = self._get_reauth_entry()
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, **new_data},
                    )
                await self.async_set_unique_id(self._user_name)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._user_name,
                    data=new_data,
                )

        return self.async_show_form(
            step_id="otp",
            data_schema=STEP_OTP_SCHEMA,
            errors=errors,
            description_placeholders={"username": self._sp_username},
        )

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
          - "Yes" → re-enter credentials (async_step_user)
          - "No"  → reload the entry as-is (async_update_reload_and_abort)
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
            # User wants to re-enter credentials → go to user step.
            return await self.async_step_user()

        # User wants to try reloading without changing credentials.
        return self.async_update_reload_and_abort(self._get_reauth_entry())
