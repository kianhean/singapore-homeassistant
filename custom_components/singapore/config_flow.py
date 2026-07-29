"""Config flow for the Singapore integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN
from .sp_usage_client import (
    LoginSession,
    SPUsageApiError,
    SPUsageAuthError,
    TokenSet,
    async_exchange_callback_url,
    async_list_accounts,
    async_renew_with_session_cookie,
    normalize_session_cookie,
)
from .usage_coordinator import (
    CONF_SP_ACCESS_TOKEN,
    CONF_SP_ACCESS_TOKEN_EXPIRES_AT,
    CONF_SP_ACCOUNT_NO,
    CONF_SP_REFRESH_TOKEN,
    CONF_SP_SESSION_COOKIE,
    has_sp_credentials,
    stored_token,
    token_entry_data,
)

_LOGGER = logging.getLogger(__name__)

CONF_LINK_SP = "link_sp_services"
CONF_CALLBACK_URL = "callback_url"
CONF_SESSION_COOKIE = "session_cookie"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore Electricity"): str,
        vol.Optional(CONF_LINK_SP, default=False): bool,
    }
)

STEP_SP_LOGIN_SCHEMA = vol.Schema({vol.Required(CONF_CALLBACK_URL): str})
STEP_SP_SESSION_SCHEMA = vol.Schema(
    {vol.Optional(CONF_SESSION_COOKIE, default=""): str}
)


class _SPLoginMixin:
    """Shared browser-assisted SP Services login steps.

    SP enforces a captcha on the Auth0 password endpoint, so the user logs in
    (and completes OTP) in their own browser and pastes the resulting callback
    URL back here; we exchange it for a refresh token.
    """

    _login: LoginSession | None = None
    _token: TokenSet | None = None
    _session_cookie: str | None = None
    _account_no: str | None = None
    _accounts: list[dict[str, Any]]

    def _sp_login_form(
        self, step_id: str, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        # Every attempt gets fresh PKCE state: an authorize URL the user has
        # already (partly) used cannot be replayed.
        self._login = LoginSession.create()
        return self.async_show_form(
            step_id=step_id,
            data_schema=STEP_SP_LOGIN_SCHEMA,
            errors=errors or {},
            description_placeholders={"authorize_url": self._login.authorize_url},
        )

    async def _async_exchange_callback(
        self, callback_url: str
    ) -> tuple[TokenSet | None, list[dict[str, Any]], str | None]:
        """Exchange a pasted callback URL, returning (token, accounts, error)."""
        if self._login is None:
            return None, [], "invalid_callback"

        session = async_get_clientsession(self.hass)
        try:
            token = await async_exchange_callback_url(
                session, self._login, callback_url
            )
            accounts = await async_list_accounts(session, token.access_token)
        except SPUsageAuthError:
            return None, [], "invalid_callback"
        except (SPUsageApiError, aiohttp.ClientError, TimeoutError):
            return None, [], "cannot_connect"

        if not accounts:
            return None, [], "no_accounts"
        if not token.refresh_token:
            # SP does not always honour `offline_access`. The access token still
            # works, so link the account anyway; the coordinator falls back to
            # HA's reauth flow once it expires.
            _LOGGER.info(
                "SP Services issued no refresh token; usage data will need "
                "another browser sign-in when the access token expires"
            )
        return token, accounts, None

    def _account_select_form(self, step_id: str = "sp_account") -> ConfigFlowResult:
        choices = {
            str(account["accountNo"]): str(account["accountNo"])
            for account in self._accounts
        }
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_SP_ACCOUNT_NO): vol.In(choices)}),
        )

    def _session_cookie_form(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="sp_session",
            data_schema=STEP_SP_SESSION_SCHEMA,
            errors=errors or {},
        )

    async def async_step_sp_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exchange the pasted SP Services callback URL for tokens."""
        if user_input is None:
            return self._sp_login_form("sp_login")

        token, accounts, error = await self._async_exchange_callback(
            user_input[CONF_CALLBACK_URL]
        )
        if error is not None:
            return self._sp_login_form("sp_login", {"base": error})

        self._token = token
        self._accounts = accounts
        if len(accounts) == 1:
            return self._async_account_chosen(str(accounts[0]["accountNo"]))
        return self._account_select_form()

    async def async_step_sp_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which linked SP account to track."""
        if user_input is None:
            return self._account_select_form()
        return self._async_account_chosen(user_input[CONF_SP_ACCOUNT_NO])

    async def async_step_sp_session(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the Auth0 session cookie used to renew a login unattended."""
        if user_input is None:
            return self._session_cookie_form()

        cookie = normalize_session_cookie(str(user_input.get(CONF_SESSION_COOKIE, "")))
        if not cookie:
            # Skipping is allowed: the entry still works until the access token
            # expires, it just needs a browser sign-in more often.
            return self._async_store()

        token, cookie, error = await self._async_validate_session_cookie(cookie)
        if error is not None:
            return self._session_cookie_form({"base": error})

        # Prefer the token the renewal just minted: it proves the cookie works
        # and starts the entry with a full token lifetime.
        self._token = token
        self._session_cookie = cookie
        return self._async_store()

    async def _async_validate_session_cookie(
        self, cookie: str
    ) -> tuple[TokenSet | None, str, str | None]:
        session = async_get_clientsession(self.hass)
        try:
            token, rotated = await async_renew_with_session_cookie(session, cookie)
        except SPUsageAuthError:
            return None, cookie, "invalid_session_cookie"
        except (SPUsageApiError, aiohttp.ClientError, TimeoutError):
            return None, cookie, "cannot_connect"
        return token, rotated, None

    def _async_account_chosen(self, account_no: str) -> ConfigFlowResult:
        self._account_no = account_no
        if self._token is not None and not self._token.refresh_token:
            # No refresh token: offer the session cookie so the link can renew
            # itself instead of expiring with the access token.
            return self._session_cookie_form()
        return self._async_store()

    def _sp_entry_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            CONF_SP_ACCOUNT_NO: self._account_no,
            CONF_SP_SESSION_COOKIE: self._session_cookie,
        }
        if self._token is not None:
            data.update(token_entry_data(self._token))
        return data

    def _async_store(self) -> ConfigFlowResult:
        """Persist the linked account; implemented per flow."""
        raise NotImplementedError


class SingaporeElectricityConfigFlow(_SPLoginMixin, ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Singapore integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._name: str = ""
        self._accounts: list[dict[str, Any]] = []
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SingaporeOptionsFlow:
        return SingaporeOptionsFlow()

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
                self._name = name
                if user_input.get(CONF_LINK_SP):
                    return self._sp_login_form("sp_login")
                return self.async_create_entry(title=name, data={CONF_NAME: name})

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle an expired SP Services session."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-link the SP Services account after the session expired."""
        if user_input is None:
            return self._sp_login_form("reauth_confirm")

        token, accounts, error = await self._async_exchange_callback(
            user_input[CONF_CALLBACK_URL]
        )
        if error is not None:
            return self._sp_login_form("reauth_confirm", {"base": error})

        self._token = token
        self._accounts = accounts
        entry = self._reauth_entry
        assert entry is not None
        # Keep the previously selected account when it is still linked, so a
        # multi-account household does not silently switch premises on reauth.
        account_no = entry.data.get(CONF_SP_ACCOUNT_NO)
        if account_no is None or all(
            str(account["accountNo"]) != str(account_no) for account in accounts
        ):
            account_no = str(accounts[0]["accountNo"])
        return self._async_account_chosen(str(account_no))

    def _async_store(self) -> ConfigFlowResult:
        entry = self._reauth_entry
        if entry is not None:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, **self._sp_entry_data()}
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        return self.async_create_entry(
            title=self._name,
            data={CONF_NAME: self._name, **self._sp_entry_data()},
        )


class SingaporeOptionsFlow(_SPLoginMixin, OptionsFlow):
    """Link, re-link, or unlink an SP Services account after setup."""

    def __init__(self) -> None:
        self._accounts: list[dict[str, Any]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what can be done with the SP Services link."""
        if not has_sp_credentials(self.config_entry.data):
            return self._sp_login_form("sp_login")
        return self.async_show_menu(
            step_id="init", menu_options=["sp_login", "sp_session", "sp_unlink"]
        )

    async def async_step_sp_session(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or replace the renewal cookie without redoing the whole login.

        Auth0 sessions do eventually end, so this is reachable straight from the
        menu; carry over what the entry already knows so submitting the step
        cannot blank the linked account or the working token.
        """
        if self._account_no is None:
            self._account_no = self.config_entry.data.get(CONF_SP_ACCOUNT_NO)
        if self._token is None:
            self._token = stored_token(self.config_entry.data)
        return await super().async_step_sp_session(user_input)

    async def async_step_sp_unlink(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Forget the stored SP Services token and drop the usage sensors."""
        entry = self.config_entry
        data = {
            key: value
            for key, value in entry.data.items()
            if key
            not in (
                CONF_SP_REFRESH_TOKEN,
                CONF_SP_ACCESS_TOKEN,
                CONF_SP_ACCESS_TOKEN_EXPIRES_AT,
                CONF_SP_SESSION_COOKIE,
                CONF_SP_ACCOUNT_NO,
            )
        }
        return self._async_update_entry(entry, data)

    def _async_store(self) -> ConfigFlowResult:
        entry = self.config_entry
        return self._async_update_entry(entry, {**entry.data, **self._sp_entry_data()})

    def _async_update_entry(
        self, entry: ConfigEntry, data: dict[str, Any]
    ) -> ConfigFlowResult:
        self.hass.config_entries.async_update_entry(entry, data=data)
        # Entities are built from entry data at setup, so the entry has to be
        # reloaded for usage sensors to appear or disappear.
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(title="", data=dict(entry.options))
