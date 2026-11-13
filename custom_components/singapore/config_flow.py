"""Config flow for the Singapore integration (+ SP e-account subentries)."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import DOMAIN
from .sp_account.client import (
    AuthError,
    SpGroupClient,
    UsageError,
    session_entry_data,
)
from .sp_account.const import (
    CONF_ELECTRICITY_PRICE,
    CONF_MFA_CODE,
    OAUTH_ERROR_MFA_REQUIRED,
    OAUTH_ERROR_REQUIRES_VERIFICATION,
    fold_text,
    parse_electricity_price,
    validate_username,
)
from .sp_account_coordinator import SUBENTRY_TYPE_SP_ACCOUNT

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore Electricity"): str,
    }
)

# The password bound only stops a pasted blob being sent as a request body.
MAX_PASSWORD_CHARS = 1024

# Every validator in these schemas must be one voluptuous_serialize (and
# probatio, on HA 2026.9+) can render: HA serializes the schema to JSON for
# the frontend, and a bare function validator raises "Unable to convert
# schema", which the UI shows as a 500 when the form loads. That was
# maci0/home-assistant-sp issue #21. Rules that need code (the username's
# octet limit, the price) run in the step handler instead and report a
# field error.
_USERNAME_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
_PRICE_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0,
        step="any",
        mode=NumberSelectorMode.BOX,
        unit_of_measurement="SGD/kWh",
    )
)


def credentials_schema(username: str | None = None) -> vol.Schema:
    """The e-account sign-in form (optionally pre-filled with the username)."""
    username_key = (
        vol.Required(CONF_USERNAME, default=username)
        if username
        else vol.Required(CONF_USERNAME)
    )
    return vol.Schema(
        {
            username_key: _USERNAME_SELECTOR,
            vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
        }
    )


def price_schema(current: float | None) -> vol.Schema:
    """The optional fixed electricity price form."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_ELECTRICITY_PRICE,
                description={"suggested_value": current},
            ): _PRICE_SELECTOR,
        }
    )


def _check_credentials(user_input: dict[str, Any], errors: dict[str, str]) -> bool:
    """Run the rules the schema cannot carry; set field errors and say if valid."""
    try:
        validate_username(user_input.get(CONF_USERNAME) or "")
    except ValueError:
        errors[CONF_USERNAME] = "invalid_username"
    password = user_input.get(CONF_PASSWORD) or ""
    if not password or len(password) > MAX_PASSWORD_CHARS:
        errors[CONF_PASSWORD] = "invalid_password"
    return not errors


def _auth_error_key(exc: AuthError) -> str:
    """The strings.json error key: the Auth0 code, or invalid_auth for the rest."""
    if exc.error_folded in {OAUTH_ERROR_REQUIRES_VERIFICATION, "too_many_attempts"}:
        return exc.error_folded
    return "invalid_auth"


def _report_failure(step: str, exc: Exception, errors: dict[str, str]) -> None:
    key = _auth_error_key(exc) if isinstance(exc, AuthError) else "cannot_connect"
    errors["base"] = key
    _LOGGER.warning("SP account %s failed: %s", step, exc, exc_info=exc)


class SingaporeElectricityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Singapore integration."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """SP e-accounts are added as subentries of the single entry."""
        return {SUBENTRY_TYPE_SP_ACCOUNT: SpAccountSubentryFlow}

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


class SpAccountSubentryFlow(ConfigSubentryFlow):
    """Sign in to an SP Group e-account (with MFA), or change its settings.

    Ported from maci0/home-assistant-sp's config flow. Only the tokens are
    stored, never the password: it buys one session and the refresh token
    every session after it.
    """

    def __init__(self) -> None:
        self._mfa_context: dict[str, Any] = {}
        self._price: float | None = None

    # -- helpers ----------------------------------------------------------

    async def _login(
        self, username: str, password: str, exchange: Any = None
    ) -> dict[str, str]:
        """Log in (or finish an MFA exchange) and read usage, in one executor job."""
        client = SpGroupClient()

        def _login_and_fetch() -> None:
            if exchange is None:
                client.login(username, password)
            else:
                exchange(client)
            client.fetch_usage()

        await self.hass.async_add_executor_job(_login_and_fetch)
        session = client.session
        if session is None:
            raise UsageError("session missing after login")
        return session_entry_data(session, username)

    async def _start_mfa(
        self, exc: AuthError, user_input: dict[str, Any]
    ) -> SubentryFlowResult | None:
        """Show the verification form when Auth0 asked for a code, else None.

        The challenge that sends an SMS/email code is triggered here, before
        the form, so the code arrives while it is shown. An authenticator app
        (TOTP) is preferred when the account has one.
        """
        if exc.error_folded != OAUTH_ERROR_MFA_REQUIRED or not exc.mfa_token:
            return None
        self._mfa_context = {
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            "mfa_token": exc.mfa_token,
        }
        channel, oob_code = await self.hass.async_add_executor_job(
            SpGroupClient().prepare_mfa, exc.mfa_token
        )
        self._mfa_context["mfa_channel"] = channel
        if oob_code is not None:
            self._mfa_context["mfa_oob_code"] = oob_code
        return self._mfa_form()

    def _mfa_form(self, errors: dict[str, str] | None = None) -> SubentryFlowResult:
        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema({vol.Required(CONF_MFA_CODE): str}),
            errors=errors,
        )

    def _finish(self, data: dict[str, Any]) -> SubentryFlowResult:
        """Create the subentry, or store a new login on the one being edited."""
        username = data[CONF_USERNAME]
        if self.source == SOURCE_USER:
            if self._price is not None:
                data = {**data, CONF_ELECTRICITY_PRICE: self._price}
            return self.async_create_entry(
                title=username, data=data, unique_id=fold_text(username)
            )
        subentry = self._get_reconfigure_subentry()
        if subentry.unique_id and subentry.unique_id != fold_text(username):
            return self.async_abort(reason="unique_id_mismatch")
        kept = {
            key: value
            for key, value in subentry.data.items()
            if key == CONF_ELECTRICITY_PRICE
        }
        return self.async_update_and_abort(
            self._get_entry(), subentry, data={**kept, **data}
        )

    def _already_configured(self, username: str) -> bool:
        folded = fold_text(username)
        return any(
            sub.subentry_type == SUBENTRY_TYPE_SP_ACCOUNT and sub.unique_id == folded
            for sub in self._get_entry().subentries.values()
        )

    async def _credentials_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        default_username: str | None = None,
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and _check_credentials(user_input, errors):
            if step_id == "user":
                if self._already_configured(user_input[CONF_USERNAME]):
                    return self.async_abort(reason="already_configured")
                if CONF_ELECTRICITY_PRICE in user_input:
                    self._price = parse_electricity_price(
                        user_input[CONF_ELECTRICITY_PRICE]
                    )
            try:
                data = await self._login(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except (AuthError, UsageError, OSError) as exc:
                if isinstance(exc, AuthError):
                    mfa = await self._start_mfa(exc, user_input)
                    if mfa is not None:
                        return mfa
                _report_failure(step_id, exc, errors)
            else:
                return self._finish(data)
        schema = credentials_schema(
            (user_input or {}).get(CONF_USERNAME) or default_username
        )
        if step_id == "user":
            schema = schema.extend(price_schema(self._price).schema)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    # -- steps ------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add an SP e-account."""
        return await self._credentials_step("user", user_input)

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Finish sign-in with the one-time code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            context = self._mfa_context
            if context.get("mfa_channel") == "oob":
                exchange = partial(
                    SpGroupClient.submit_mfa_oob,
                    mfa_token=context["mfa_token"],
                    oob_code=context["mfa_oob_code"],
                    binding_code=user_input[CONF_MFA_CODE],
                )
            else:
                exchange = partial(
                    SpGroupClient.submit_mfa,
                    mfa_token=context["mfa_token"],
                    otp=user_input[CONF_MFA_CODE],
                )
            try:
                data = await self._login(
                    context[CONF_USERNAME], context[CONF_PASSWORD], exchange
                )
            except (AuthError, UsageError, OSError) as exc:
                _report_failure("MFA code exchange", exc, errors)
            else:
                return self._finish(data)
        return self._mfa_form(errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Sign in again, or change the electricity price."""
        return self.async_show_menu(
            step_id="reconfigure", menu_options=["credentials", "price"]
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Sign in again (expired session, changed password or MFA)."""
        subentry = self._get_reconfigure_subentry()
        return await self._credentials_step(
            "credentials", user_input, subentry.data.get(CONF_USERNAME)
        )

    async def async_step_price(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Set or clear the fixed electricity price used for the cost series."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            price = parse_electricity_price(user_input.get(CONF_ELECTRICITY_PRICE))
            data = {
                key: value
                for key, value in subentry.data.items()
                if key != CONF_ELECTRICITY_PRICE
            }
            if price is not None:
                data[CONF_ELECTRICITY_PRICE] = price
            return self.async_update_and_abort(self._get_entry(), subentry, data=data)
        try:
            current = parse_electricity_price(subentry.data.get(CONF_ELECTRICITY_PRICE))
        except ValueError:
            current = None
        return self.async_show_form(step_id="price", data_schema=price_schema(current))
