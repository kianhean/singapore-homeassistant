"""Config flow for the Singapore integration (+ SP e-account subentries)."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_API_KEY,
    CONF_CARPARKS,
    CONF_DISABLED_SCHOOL_EVENTS,
    CONF_DISABLED_SOURCES,
    CONF_ENABLED_SOURCES,
    CONF_EV_POSTAL_CODES,
    CONF_LTA_API_KEY,
    CONF_POLLING,
    CONF_SCHOOL_EVENTS,
    CONF_SOURCES,
    CONF_TRAIN_CROWD_STATIONS,
    DOMAIN,
    LTA_DATAMALL_API_KEY_HEADER,
    LTA_DATAMALL_SOURCES,
    OPT_IN_SOURCES,
    POLLING_NORMAL,
    POLLING_OPTIONS,
    SCHOOL_EVENT_KINDS,
    SOURCE_SCHOOL,
    SOURCES,
    carpark_numbers,
    enabled_school_events,
    enabled_sources,
    ev_postal_codes,
)
from .sp_account.client import (
    AuthError,
    SpGroupClient,
    UsageError,
    session_entry_data,
)
from .sp_account.const import (
    CONF_MFA_CODE,
    OAUTH_ERROR_MFA_REQUIRED,
    OAUTH_ERROR_REQUIRES_VERIFICATION,
    fold_text,
    validate_username,
)
from .sp_account_coordinator import SUBENTRY_TYPE_SP_ACCOUNT
from .train_crowd_coordinator import PCD_REALTIME_URL

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Singapore"): str,
    }
)

# The password bound only stops a pasted blob being sent as a request body.
MAX_PASSWORD_CHARS = 1024

# Every validator in these schemas must be one voluptuous_serialize (and
# probatio, on HA 2026.9+) can render: HA serializes the schema to JSON for
# the frontend, and a bare function validator raises "Unable to convert
# schema", which the UI shows as a 500 when the form loads. That was
# maci0/home-assistant-sp issue #21. Rules that need code (the username's
# octet limit) run in the step handler instead and report a field error.
_USERNAME_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
# The data.gov.sg API key: masked, but not a login password for the browser.
_PASSWORD_SELECTOR_PLAIN = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SingaporeOptionsFlow:
        """Data sources, data.gov.sg API key and polling speed."""
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
                return self.async_create_entry(title=name, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


def options_schema(options: dict[str, Any]) -> vol.Schema:
    """The options form, pre-filled from the entry's current options."""
    enabled = enabled_sources(options)
    api_key = options.get(CONF_API_KEY) or ""
    lta_api_key = options.get(CONF_LTA_API_KEY) or ""
    return vol.Schema(
        {
            vol.Optional(
                CONF_SOURCES, default=[s for s in SOURCES if s in enabled]
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(SOURCES),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_SOURCES,
                )
            ),
            vol.Optional(
                CONF_SCHOOL_EVENTS,
                default=[
                    k for k in SCHOOL_EVENT_KINDS if k in enabled_school_events(options)
                ],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(SCHOOL_EVENT_KINDS),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_SCHOOL_EVENTS,
                )
            ),
            vol.Optional(
                CONF_API_KEY, description={"suggested_value": api_key}
            ): _PASSWORD_SELECTOR_PLAIN,
            vol.Optional(
                CONF_LTA_API_KEY, description={"suggested_value": lta_api_key}
            ): _PASSWORD_SELECTOR_PLAIN,
            vol.Optional(
                CONF_CARPARKS,
                description={"suggested_value": options.get(CONF_CARPARKS) or ""},
            ): TextSelector(),
            vol.Optional(
                CONF_TRAIN_CROWD_STATIONS,
                description={
                    "suggested_value": options.get(CONF_TRAIN_CROWD_STATIONS) or ""
                },
            ): TextSelector(),
            vol.Optional(
                CONF_EV_POSTAL_CODES,
                description={
                    "suggested_value": options.get(CONF_EV_POSTAL_CODES) or ""
                },
            ): TextSelector(),
            vol.Required(
                CONF_POLLING, default=options.get(CONF_POLLING, POLLING_NORMAL)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=POLLING_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_POLLING,
                )
            ),
        }
    )


def options_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Stored options for a submitted form.

    The form lists the enabled sources; the entry stores the disabled ones, so
    a source added in a later release starts out enabled.
    """
    enabled = set(user_input.get(CONF_SOURCES) or ())
    options: dict[str, Any] = {
        CONF_DISABLED_SOURCES: [
            s for s in SOURCES if s not in enabled and s not in OPT_IN_SOURCES
        ],
        CONF_POLLING: user_input.get(CONF_POLLING, POLLING_NORMAL),
    }
    # Opt-in sources are stored as the ones turned on (off by default).
    if opted_in := [s for s in OPT_IN_SOURCES if s in enabled]:
        options[CONF_ENABLED_SOURCES] = opted_in
    if api_key := (user_input.get(CONF_API_KEY) or "").strip():
        options[CONF_API_KEY] = api_key
    if lta_api_key := (user_input.get(CONF_LTA_API_KEY) or "").strip():
        options[CONF_LTA_API_KEY] = lta_api_key
    if carparks := carpark_numbers(user_input):
        options[CONF_CARPARKS] = ", ".join(carparks)
    if raw_stations := (user_input.get(CONF_TRAIN_CROWD_STATIONS) or "").strip():
        stations: list[str] = []
        for part in raw_stations.replace(";", ",").replace(" ", ",").split(","):
            if (code := part.strip().upper()) and code not in stations:
                stations.append(code)
        if stations:
            options[CONF_TRAIN_CROWD_STATIONS] = ", ".join(stations)
    if postal_codes := ev_postal_codes(user_input):
        options[CONF_EV_POSTAL_CODES] = ", ".join(postal_codes)
    # Absent from the form (an older frontend) means "keep them all".
    school = user_input.get(CONF_SCHOOL_EVENTS)
    if school is not None and (
        off := [k for k in SCHOOL_EVENT_KINDS if k not in set(school)]
    ):
        options[CONF_DISABLED_SCHOOL_EVENTS] = off
    return options


async def _validate_lta_api_key(hass: HomeAssistant, api_key: str) -> str | None:
    """Validate an LTA DataMall AccountKey with one lightweight call.

    Returns an error key ("invalid_lta_api_key" or "cannot_connect_lta") or None.
    """
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            PCD_REALTIME_URL,
            params={"TrainLine": "NSL"},
            headers={
                LTA_DATAMALL_API_KEY_HEADER: api_key,
                "accept": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status in (401, 403):
                return "invalid_lta_api_key"
            if response.status != 200:
                return "cannot_connect_lta"
    except (aiohttp.ClientError, TimeoutError):
        return "cannot_connect_lta"
    return None


class SingaporeOptionsFlow(OptionsFlow):
    """Choose which data sources run, and how they poll."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = set(user_input.get(CONF_SOURCES) or ())
            lta_key = (user_input.get(CONF_LTA_API_KEY) or "").strip()
            existing_lta_key = (
                self.config_entry.options.get(CONF_LTA_API_KEY) or ""
            ).strip()
            if not selected and not self.config_entry.subentries:
                errors[CONF_SOURCES] = "no_sources"
            elif SOURCE_SCHOOL in selected and not (
                user_input.get(CONF_SCHOOL_EVENTS, SCHOOL_EVENT_KINDS)
            ):
                # The school calendar is on but would have no calendars.
                errors[CONF_SCHOOL_EVENTS] = "no_school_events"
            elif (selected & set(LTA_DATAMALL_SOURCES)) and not lta_key:
                errors[CONF_LTA_API_KEY] = "lta_api_key_required"
            elif lta_key and lta_key != existing_lta_key:
                if error := await _validate_lta_api_key(self.hass, lta_key):
                    errors[CONF_LTA_API_KEY] = error
            if not errors:
                # Saving the options reloads the entry (see _async_entry_updated).
                return self.async_create_entry(data=options_from_input(user_input))
        schema = options_schema(dict(self.config_entry.options))
        if user_input is not None:
            # Re-show what was submitted, not the stored options.
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


class SpAccountSubentryFlow(ConfigSubentryFlow):
    """Sign in to an SP Group e-account (with MFA), or sign in to it again.

    Ported from maci0/home-assistant-sp's config flow. Only the tokens are
    stored, never the password: it buys one session and the refresh token
    every session after it.
    """

    def __init__(self) -> None:
        self._mfa_context: dict[str, Any] = {}

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
            return self.async_create_entry(
                title=username, data=data, unique_id=fold_text(username)
            )
        subentry = self._get_reconfigure_subentry()
        if subentry.unique_id and subentry.unique_id != fold_text(username):
            return self.async_abort(reason="unique_id_mismatch")
        return self.async_update_and_abort(self._get_entry(), subentry, data=data)

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
            if step_id == "user" and self._already_configured(
                user_input[CONF_USERNAME]
            ):
                return self.async_abort(reason="already_configured")
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
        """Sign in again (expired session, changed password or MFA)."""
        subentry = self._get_reconfigure_subentry()
        return await self._credentials_step(
            "reconfigure", user_input, subentry.data.get(CONF_USERNAME)
        )
