"""Config flow for the Singapore integration.

Setup steps
-----------
1. **user** — enter an integration name plus optional SP Services credentials.
   If credentials are supplied the flow triggers an OTP and continues to
   step 2; otherwise the entry is created immediately (without SP Services
   household-usage sensors).

2. **otp** — enter the OTP delivered to the user's registered mobile to
   complete the SP Services login.  The resulting auth token is stored in
   the config entry's data dict under ``"sp_token"``.

Re-authentication steps (triggered when the token expires)
-----------------------------------------------------------
1. **reauth_confirm** — re-enter the SP Services password to request a new OTP.
2. **reauth_otp** — enter the new OTP; the refreshed token replaces the old one.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME

from . import DOMAIN
from .sp_services_coordinator import SpServicesClient

_LOGGER = logging.getLogger(__name__)

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
        # Raw JSON body returned by the SP Services login endpoint.
        # Passed unchanged to verify_otp() so the server can match it to the
        # correct in-flight login session.
        self._login_response: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Initial setup — step 1
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect integration name and optional SP Services credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            username = user_input.get(CONF_USERNAME, "").strip()
            password = user_input.get(CONF_PASSWORD, "").strip()

            if not name:
                errors["base"] = "empty_name"
            elif len(name) > 64:
                errors["base"] = "name_too_long"
            elif bool(username) != bool(password):
                # One field filled but not the other
                errors["base"] = "credentials_incomplete"
            elif username and password:
                # Credentials supplied — trigger OTP then continue to step 2
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
                # No credentials — create entry without SP Services
                await self.async_set_unique_id(name)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Initial setup — step 2 (OTP)
    # ------------------------------------------------------------------

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the OTP to complete SP Services login."""
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
                await self.async_set_unique_id(self._user_name)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._user_name,
                    data={
                        CONF_NAME: self._user_name,
                        CONF_USERNAME: self._sp_username,
                        CONF_PASSWORD: self._sp_password,
                        "sp_token": token,
                    },
                )

        return self.async_show_form(
            step_id="otp",
            data_schema=STEP_OTP_SCHEMA,
            errors=errors,
            description_placeholders={"username": self._sp_username},
        )

    # ------------------------------------------------------------------
    # Re-authentication — triggered by ConfigEntryAuthFailed
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Entry point for the reauth flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-enter the SP Services password to request a new OTP."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._sp_username = entry.data.get(CONF_USERNAME, "")

        if user_input is not None:
            self._sp_password = user_input[CONF_PASSWORD].strip()
            client = SpServicesClient()
            try:
                self._login_response = await client.login(
                    self._sp_username, self._sp_password
                )
            except ValueError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("SP Services reauth login error")
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

            if not errors:
                return await self.async_step_reauth_otp()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": self._sp_username},
        )

    async def async_step_reauth_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the new OTP to refresh the SP Services token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = SpServicesClient()
            try:
                token = await client.verify_otp(user_input["otp"], self._login_response)
            except ValueError:
                errors["base"] = "invalid_otp"
            except Exception:
                _LOGGER.exception("SP Services reauth OTP error")
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

            if not errors:
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_PASSWORD: self._sp_password,
                        "sp_token": token,
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_otp",
            data_schema=STEP_OTP_SCHEMA,
            errors=errors,
        )
