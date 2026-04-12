"""Config flow for Singapore integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME

from . import DOMAIN
from .sp_services_coordinator import CONF_SP_TOKEN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore Electricity"): str,
    }
)

STEP_SP_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_USERNAME, default=""): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)

STEP_SP_OTP_SCHEMA = vol.Schema(
    {
        vol.Required("otp"): str,
    }
)


class SingaporeElectricityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Singapore integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._name: str = ""
        self._sp_username: str = ""
        self._sp_password: str = ""
        self._sp_client = None
        self._login_challenge = None

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
                return await self.async_step_sp_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_sp_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally collect SP Services credentials. Leave blank to skip."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input.get(CONF_USERNAME, "").strip()
            password = user_input.get(CONF_PASSWORD, "").strip()

            if not username and not password:
                # User skipped SP login
                return await self._create_entry()

            if not username or not password:
                errors["base"] = "sp_credentials_incomplete"
            else:
                try:
                    from sp_services import AuthenticationError, SpServicesClient

                    self._sp_client = SpServicesClient()
                    self._login_challenge = await self._sp_client.login(
                        username, password
                    )
                    self._sp_username = username
                    self._sp_password = password
                    return await self.async_step_sp_otp()
                except AuthenticationError:
                    errors["base"] = "sp_invalid_auth"
                    if self._sp_client:
                        await self._sp_client.close()
                        self._sp_client = None
                except Exception:  # noqa: BLE001
                    errors["base"] = "sp_cannot_connect"
                    if self._sp_client:
                        await self._sp_client.close()
                        self._sp_client = None

        return self.async_show_form(
            step_id="sp_credentials",
            data_schema=STEP_SP_CREDENTIALS_SCHEMA,
            errors=errors,
        )

    async def async_step_sp_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle OTP verification for SP Services login."""
        errors: dict[str, str] = {}

        phone = (
            self._login_challenge.phone_number
            if self._login_challenge and self._login_challenge.phone_number
            else "your registered phone"
        )

        if user_input is not None:
            try:
                token = await self._sp_client.verify_otp(
                    user_input["otp"].strip(), self._login_challenge
                )
                await self._sp_client.close()
                self._sp_client = None
                return await self._create_entry(sp_token=token)
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_invalid_otp"

        return self.async_show_form(
            step_id="sp_otp",
            data_schema=STEP_SP_OTP_SCHEMA,
            errors=errors,
            description_placeholders={"phone": phone},
        )

    async def _create_entry(self, sp_token: str | None = None) -> ConfigFlowResult:
        """Finalise and create the config entry."""
        data: dict[str, Any] = {CONF_NAME: self._name}
        if sp_token:
            data[CONF_SP_TOKEN] = sp_token
            data[CONF_USERNAME] = self._sp_username
            data[CONF_PASSWORD] = self._sp_password

        await self.async_set_unique_id(self._name)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._name, data=data)

    # ------------------------------------------------------------------
    # Re-authentication (triggered when ConfigEntryAuthFailed is raised)
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start re-auth flow after token expiry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect fresh SP credentials for re-authentication."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD].strip()
            try:
                from sp_services import AuthenticationError, SpServicesClient

                self._sp_client = SpServicesClient()
                self._login_challenge = await self._sp_client.login(username, password)
                self._sp_username = username
                self._sp_password = password
                return await self.async_step_reauth_otp()
            except AuthenticationError:
                errors["base"] = "sp_invalid_auth"
                if self._sp_client:
                    await self._sp_client.close()
                    self._sp_client = None
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_cannot_connect"
                if self._sp_client:
                    await self._sp_client.close()
                    self._sp_client = None

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify OTP during re-authentication and update entry token."""
        errors: dict[str, str] = {}

        phone = (
            self._login_challenge.phone_number
            if self._login_challenge and self._login_challenge.phone_number
            else "your registered phone"
        )

        if user_input is not None:
            try:
                token = await self._sp_client.verify_otp(
                    user_input["otp"].strip(), self._login_challenge
                )
                await self._sp_client.close()
                self._sp_client = None
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={
                        CONF_SP_TOKEN: token,
                        CONF_USERNAME: self._sp_username,
                        CONF_PASSWORD: self._sp_password,
                    },
                )
            except Exception:  # noqa: BLE001
                errors["base"] = "sp_invalid_otp"

        return self.async_show_form(
            step_id="reauth_otp",
            data_schema=STEP_SP_OTP_SCHEMA,
            errors=errors,
            description_placeholders={"phone": phone},
        )
