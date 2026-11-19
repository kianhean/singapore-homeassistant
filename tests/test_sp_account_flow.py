"""SP account subentry flow: sign-in, MFA, reconfigure and error mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigSubentry

from custom_components.singapore import config_flow
from custom_components.singapore.config_flow import (
    SingaporeElectricityConfigFlow,
    SpAccountSubentryFlow,
    credentials_schema,
)
from custom_components.singapore.sp_account.client import (
    AuthError,
    Session,
    UsageError,
)
from custom_components.singapore.sp_account_coordinator import (
    SUBENTRY_TYPE_SP_ACCOUNT,
)

_GOOD = {"username": "me@example.com", "password": "hunter2"}
_SESSION = Session(access_token="a", id_token="i", refresh_token="r")


class _Hass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _Flow(SpAccountSubentryFlow):
    """The subentry flow with HA's flow-manager methods stubbed."""

    def __init__(self, entry: ConfigEntry, source: str = "user", subentry=None):
        super().__init__()
        self.hass = _Hass()
        self.source = source
        self._entry = entry
        self._subentry = subentry

    def async_show_form(self, *, step_id, data_schema=None, errors=None):
        return {"type": "form", "step_id": step_id, "errors": errors or {}}

    def async_create_entry(self, *, title, data, unique_id=None):
        return {"type": "create_entry", "title": title, "data": data, "uid": unique_id}

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}

    def async_update_and_abort(self, entry, subentry, *, data):
        return {"type": "abort", "reason": "reconfigure_successful", "data": data}

    def _get_entry(self):
        return self._entry

    def _get_reconfigure_subentry(self):
        return self._subentry


def _client(login=None, session=_SESSION, prepare=("otp", None)):
    """A fake SpGroupClient class; every instance shares one mock."""
    client = MagicMock()
    client.session = session
    if login is not None:
        client.login.side_effect = login
    client.prepare_mfa.return_value = prepare
    cls = MagicMock(return_value=client)
    cls.submit_mfa = MagicMock()
    cls.submit_mfa_oob = MagicMock()
    return cls, client


def _entry(*subentries) -> ConfigEntry:
    entry = ConfigEntry("entry1")
    entry.subentries = {s.subentry_id: s for s in subentries}
    return entry


async def test_user_step_shows_form_first():
    result = await _Flow(_entry()).async_step_user()
    assert result == {"type": "form", "step_id": "user", "errors": {}}


async def test_sign_in_creates_subentry_without_password():
    cls, client = _client()
    with patch.object(config_flow, "SpGroupClient", cls):
        result = await _Flow(_entry()).async_step_user(dict(_GOOD))
    assert result["type"] == "create_entry"
    assert result["title"] == "me@example.com"
    assert result["uid"] == "me@example.com"
    assert "password" not in result["data"]
    assert result["data"]["username"] == "me@example.com"
    client.login.assert_called_once_with("me@example.com", "hunter2")
    client.fetch_usage.assert_called_once()


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        ({"username": "", "password": "x"}, {"username": "invalid_username"}),
        (
            {"username": "me@example.com", "password": ""},
            {"password": "invalid_password"},
        ),
        (
            {"username": "me@example.com", "password": "x" * 10_000},
            {"password": "invalid_password"},
        ),
    ],
)
async def test_field_rules_are_checked_in_the_handler(user_input, errors):
    result = await _Flow(_entry()).async_step_user(user_input)
    assert result["type"] == "form"
    assert result["errors"] == errors


async def test_already_configured_account_aborts():
    existing = ConfigSubentry(
        data={},
        subentry_type=SUBENTRY_TYPE_SP_ACCOUNT,
        title="me",
        unique_id="me@example.com",
    )
    result = await _Flow(_entry(existing)).async_step_user(dict(_GOOD))
    assert result == {"type": "abort", "reason": "already_configured"}


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (AuthError("invalid_grant", "Wrong email or password."), "invalid_auth"),
        (AuthError("too_many_attempts"), "too_many_attempts"),
        (AuthError("requires_verification"), "requires_verification"),
        (UsageError("no readings"), "cannot_connect"),
        (OSError("network down"), "cannot_connect"),
        # mfa_required without a token can't start MFA: a plain auth error.
        (AuthError("mfa_required"), "invalid_auth"),
    ],
)
async def test_sign_in_errors_map_to_form_errors(error, key):
    cls, _client_mock = _client(login=error)
    with patch.object(config_flow, "SpGroupClient", cls):
        result = await _Flow(_entry()).async_step_user(dict(_GOOD))
    assert result["type"] == "form"
    assert result["errors"] == {"base": key}


async def test_missing_session_after_login_is_cannot_connect():
    cls, _client_mock = _client(session=None)
    with patch.object(config_flow, "SpGroupClient", cls):
        result = await _Flow(_entry()).async_step_user(dict(_GOOD))
    assert result["errors"] == {"base": "cannot_connect"}


async def test_totp_mfa_flow():
    cls, client = _client(login=AuthError("mfa_required", mfa_token="tok"))
    flow = _Flow(_entry())
    with patch.object(config_flow, "SpGroupClient", cls):
        form = await flow.async_step_user(dict(_GOOD))
        assert form == {"type": "form", "step_id": "mfa", "errors": {}}
        assert await flow.async_step_mfa() == form  # re-shown without input
        result = await flow.async_step_mfa({"mfa_code": "123456"})
    assert result["type"] == "create_entry"
    exchange = cls.submit_mfa
    exchange.assert_called_once_with(client, mfa_token="tok", otp="123456")
    client.prepare_mfa.assert_called_once_with("tok")


async def test_oob_mfa_flow():
    cls, client = _client(
        login=AuthError("mfa_required", mfa_token="tok"), prepare=("oob", "oob-1")
    )
    flow = _Flow(_entry())
    with patch.object(config_flow, "SpGroupClient", cls):
        await flow.async_step_user(dict(_GOOD))
        result = await flow.async_step_mfa({"mfa_code": "987654"})
    assert result["type"] == "create_entry"
    cls.submit_mfa_oob.assert_called_once_with(
        client, mfa_token="tok", oob_code="oob-1", binding_code="987654"
    )


async def test_wrong_mfa_code_shows_error():
    cls, client = _client(login=AuthError("mfa_required", mfa_token="tok"))
    cls.submit_mfa.side_effect = AuthError("invalid_grant", "Invalid otp_code.")
    flow = _Flow(_entry())
    with patch.object(config_flow, "SpGroupClient", cls):
        await flow.async_step_user(dict(_GOOD))
        result = await flow.async_step_mfa({"mfa_code": "000000"})
    assert result == {
        "type": "form",
        "step_id": "mfa",
        "errors": {"base": "invalid_auth"},
    }


def _reconfigure_flow(unique_id: str | None = "me@example.com") -> _Flow:
    subentry = ConfigSubentry(
        data={"username": "me@example.com"},
        subentry_type=SUBENTRY_TYPE_SP_ACCOUNT,
        title="me",
        unique_id=unique_id,
    )
    return _Flow(_entry(subentry), source="reconfigure", subentry=subentry)


async def test_reconfigure_prefills_username_and_updates():
    flow = _reconfigure_flow()
    form = await flow.async_step_reconfigure()
    assert form["step_id"] == "reconfigure"
    cls, _client_mock = _client()
    with patch.object(config_flow, "SpGroupClient", cls):
        result = await flow.async_step_reconfigure(dict(_GOOD))
    assert result["reason"] == "reconfigure_successful"
    assert "password" not in result["data"]


async def test_reconfigure_with_another_account_aborts():
    cls, _client_mock = _client()
    with patch.object(config_flow, "SpGroupClient", cls):
        result = await _reconfigure_flow().async_step_reconfigure(
            {"username": "someone@example.com", "password": "x"}
        )
    assert result == {"type": "abort", "reason": "unique_id_mismatch"}


async def test_reconfigure_without_unique_id_accepts_any_login():
    cls, _client_mock = _client()
    with patch.object(config_flow, "SpGroupClient", cls):
        result = await _reconfigure_flow(None).async_step_reconfigure(
            {"username": "someone@example.com", "password": "x"}
        )
    assert result["reason"] == "reconfigure_successful"


def test_credentials_schema_prefill():
    assert credentials_schema("me@example.com") is not None
    assert credentials_schema() is not None


class _UserFlow(SingaporeElectricityConfigFlow):
    def __init__(self):
        self.unique_ids = []

    async def async_set_unique_id(self, unique_id):
        self.unique_ids.append(unique_id)

    def _abort_if_unique_id_configured(self):
        return None

    def async_create_entry(self, *, title, data):
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(self, *, step_id, data_schema=None, errors=None):
        return {"type": "form", "step_id": step_id, "errors": errors}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("   ", {"type": "form", "step_id": "user", "errors": {"base": "empty_name"}}),
        (
            "x" * 65,
            {"type": "form", "step_id": "user", "errors": {"base": "name_too_long"}},
        ),
    ],
)
async def test_user_step_validates_name(name, expected):
    assert await _UserFlow().async_step_user({"name": name}) == expected


async def test_user_step_creates_entry():
    flow = _UserFlow()
    result = await flow.async_step_user({"name": " Home "})
    assert result == {
        "type": "create_entry",
        "title": "Home",
        "data": {"name": " Home "},
    }
    assert flow.unique_ids == ["Home"]
    assert (await flow.async_step_user())["errors"] == {}


def test_options_flow_factory():
    flow = SingaporeElectricityConfigFlow.async_get_options_flow(SimpleNamespace())
    assert isinstance(flow, config_flow.SingaporeOptionsFlow)
