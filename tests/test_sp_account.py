"""SP Group e-account glue: coordinator, sensors, flow helpers, reload listener.

The protocol layer (client/mapper/history) has its own tests under
``tests/sp_account/`` (vendored from maci0/home-assistant-sp).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore import SingaporeData, _async_entry_updated
from custom_components.singapore.config_flow import (
    MAX_PASSWORD_CHARS,
    SingaporeElectricityConfigFlow,
    SpAccountSubentryFlow,
    _check_credentials,
)
from custom_components.singapore.sp_account.client import AuthError, SpGroupClient
from custom_components.singapore.sp_account.const import (
    CONF_ACCESS_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
)
from custom_components.singapore.sp_account.mapper import sensors_from_usage
from custom_components.singapore.sp_account_coordinator import (
    ISSUE_AUTH_FAILED,
    SUBENTRY_TYPE_SP_ACCOUNT,
    SpAccountCoordinator,
    auth_issue_id,
    same_tokens,
    session_from_data,
)
from custom_components.singapore.sp_account_sensor import (
    TRANSLATION_PREFIX,
    SpAccountSensor,
    async_setup_sp_account_sensors,
)

from .sp_account.conftest import FIXED_NOW, FixtureTransport, fixture_client

COMPONENT = Path(__file__).parent.parent / "custom_components" / "singapore"


def _hass() -> MagicMock:
    hass = MagicMock()
    hass.config.components = set()

    async def _executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = _executor
    # Background tasks (statistics import) are not run by these tests.
    hass.async_create_task = lambda coro, *a, **k: coro.close()
    return hass


def _subentry(data: dict | None = None, subentry_id: str = "sub1") -> ConfigSubentry:
    return ConfigSubentry(
        data=MappingProxyType(
            data or {"username": "user@example.com", CONF_ACCESS_TOKEN: "old"}
        ),
        subentry_type=SUBENTRY_TYPE_SP_ACCOUNT,
        title="user@example.com",
        unique_id="user@example.com",
        subentry_id=subentry_id,
    )


def _entry(*subentries: ConfigSubentry) -> ConfigEntry:
    entry = ConfigEntry()
    entry.subentries = {sub.subentry_id: sub for sub in subentries}
    return entry


def _coordinator(client: SpGroupClient | None = None, hass=None):
    hass = hass or _hass()
    sub = _subentry()
    entry = _entry(sub)
    coordinator = SpAccountCoordinator(hass, entry, sub, client=client)
    return hass, entry, sub, coordinator


@pytest.fixture(autouse=True)
def _reset_issue_mocks():
    ir.async_create_issue.reset_mock()
    ir.async_delete_issue.reset_mock()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def test_session_from_data_requires_both_tokens():
    assert session_from_data({}) is None
    assert session_from_data({CONF_ACCESS_TOKEN: "a"}) is None
    session = session_from_data(
        {CONF_ACCESS_TOKEN: "a", CONF_ID_TOKEN: "i", CONF_REFRESH_TOKEN: "r"}
    )
    assert session is not None
    assert (session.access_token, session.id_token, session.refresh_token) == (
        "a",
        "i",
        "r",
    )


def test_same_tokens_ignores_expiry_and_scope():
    client = fixture_client()
    live = client.session
    stored = session_from_data(
        {
            CONF_ACCESS_TOKEN: live.access_token,
            CONF_ID_TOKEN: live.id_token,
            CONF_REFRESH_TOKEN: live.refresh_token,
        }
    )
    assert same_tokens(stored, live)
    assert not same_tokens(None, live)
    assert same_tokens(None, None)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


async def test_poll_returns_usage_persists_tokens_and_clears_issue():
    hass, entry, sub, coordinator = _coordinator(fixture_client())
    usage = await coordinator._async_update_data()

    assert usage.premise_id
    assert coordinator.last_error is None
    assert coordinator.last_success is not None
    # The fixture login issued new tokens, so they are written to the subentry.
    hass.config_entries.async_update_subentry.assert_called_once()
    payload = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert payload[CONF_ACCESS_TOKEN] == coordinator.client.session.access_token
    assert payload["username"] == "user@example.com"
    ir.async_delete_issue.assert_called_once_with(
        hass, "singapore", auth_issue_id("sub1")
    )


async def test_poll_does_not_rewrite_unchanged_tokens():
    client = fixture_client()
    session = client.session
    hass = _hass()
    sub = _subentry(
        {
            "username": "user@example.com",
            CONF_ACCESS_TOKEN: session.access_token,
            CONF_ID_TOKEN: session.id_token,
            CONF_REFRESH_TOKEN: session.refresh_token,
        }
    )
    coordinator = SpAccountCoordinator(hass, _entry(sub), sub, client=client)
    await coordinator._async_update_data()
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_auth_failure_raises_update_failed_and_opens_repair_issue():
    hass, _entry_, _sub, coordinator = _coordinator()
    coordinator.client = MagicMock()
    coordinator.client.fetch_usage.side_effect = AuthError("invalid_grant", "nope")

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()

    assert raised.value.translation_kwargs["translation_key"] == "auth_failed"
    assert coordinator.last_error
    ir.async_create_issue.assert_called_once()
    kwargs = ir.async_create_issue.call_args.kwargs
    assert kwargs["translation_key"] == ISSUE_AUTH_FAILED
    assert kwargs["translation_placeholders"] == {"title": "user@example.com"}


async def test_requires_verification_is_retried_without_repair_issue():
    _hass_, _entry_, _sub, coordinator = _coordinator()
    coordinator.client = MagicMock()
    coordinator.client.fetch_usage.side_effect = AuthError(
        "requires_verification", "suspicious"
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    ir.async_create_issue.assert_not_called()


async def test_subentry_update_with_new_tokens_swaps_client_and_refreshes():
    _hass_, entry, sub, coordinator = _coordinator(fixture_client())
    coordinator.async_request_refresh = AsyncMock()
    entry.subentries[sub.subentry_id] = _subentry(
        {
            "username": "user@example.com",
            CONF_ACCESS_TOKEN: "fresh",
            CONF_ID_TOKEN: "fresh-id",
        }
    )
    await coordinator.async_subentry_updated()
    assert coordinator.client.session.access_token == "fresh"
    coordinator.async_request_refresh.assert_awaited_once()


async def test_price_comes_from_scraped_tariff_in_sgd():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    assert coordinator.electricity_price is None  # no tariff coordinator
    coordinator.tariff = MagicMock(data=None)
    assert coordinator.electricity_price is None  # tariff not loaded yet
    coordinator.tariff.data = MagicMock(electricity_price=29.29)  # ¢/kWh
    assert coordinator.electricity_price == 0.2929


async def test_tariff_change_reprices_cost_series():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    coordinator.async_import_billed_history = AsyncMock()
    coordinator.tariff = MagicMock(data=MagicMock(electricity_price=29.29))
    await coordinator.async_tariff_updated()
    coordinator.async_import_billed_history.assert_awaited_once()

    # Same price as already imported: nothing to re-write.
    coordinator._imported_price = 0.2929
    await coordinator.async_tariff_updated()
    coordinator.async_import_billed_history.assert_awaited_once()


async def test_statistics_import_skipped_without_recorder():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    coordinator.data = await coordinator._async_update_data()
    coordinator._async_import_billed_history = AsyncMock()
    await coordinator.async_import_billed_history()
    coordinator._async_import_billed_history.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------


async def test_sensors_added_on_first_data_with_subentry_and_prefixed_keys():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    listeners = []
    coordinator.async_add_listener = lambda cb: listeners.append(cb) or (lambda: None)
    add_entities = MagicMock()

    unsubs = async_setup_sp_account_sensors(
        "entry1", {"sub1": coordinator}, add_entities
    )
    assert len(unsubs) == 1
    add_entities.assert_not_called()  # no data yet

    coordinator.data = await coordinator._async_update_data()
    listeners[0]()
    entities = add_entities.call_args.args[0]
    assert add_entities.call_args.kwargs == {"config_subentry_id": "sub1"}
    expected = {spec.key for spec in sensors_from_usage(coordinator.data, FIXED_NOW)}
    assert {e.entity_description.key for e in entities} == expected

    premise = coordinator.data.premise_id
    by_key = {e.entity_description.key: e for e in entities}
    electricity = by_key["electricity"]
    assert electricity.unique_id == f"entry1_sp_{premise}_electricity"
    assert electricity._attr_translation_key == "sp_electricity"
    assert electricity.native_value is not None
    assert electricity._attr_device_info["identifiers"] == {
        ("singapore", f"entry1_sp_{premise}")
    }

    # A second update with the same keys adds nothing new.
    listeners[0]()
    assert add_entities.call_count == 1


async def test_sensor_unavailable_when_key_disappears():
    _hass_, _entry_, _sub, coordinator = _coordinator(fixture_client())
    coordinator.data = await coordinator._async_update_data()
    spec = coordinator.sensor_specs[0]
    sensor = SpAccountSensor(coordinator, "entry1", spec)
    assert sensor.available
    coordinator.spec_for = lambda key: None
    sensor._handle_coordinator_update()
    assert not sensor.available
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Config flow helpers (issue #21 in maci0/home-assistant-sp)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_input", "expected"),
    [
        ({"username": "a@b.sg", "password": "pw"}, {}),
        ({"username": "", "password": "pw"}, {"username": "invalid_username"}),
        (
            {"username": "x" * 255, "password": "pw"},
            {"username": "invalid_username"},
        ),
        ({"username": "\ud800", "password": "pw"}, {"username": "invalid_username"}),
        ({"username": "a@b.sg", "password": ""}, {"password": "invalid_password"}),
        (
            {"username": "a@b.sg", "password": "p" * (MAX_PASSWORD_CHARS + 1)},
            {"password": "invalid_password"},
        ),
    ],
)
def test_check_credentials(user_input, expected):
    errors: dict[str, str] = {}
    assert _check_credentials(user_input, errors) is (not expected)
    assert errors == expected


def test_config_flow_offers_sp_account_subentry():
    types = SingaporeElectricityConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert types == {SUBENTRY_TYPE_SP_ACCOUNT: SpAccountSubentryFlow}


# ---------------------------------------------------------------------------
# Update listener
# ---------------------------------------------------------------------------


def _runtime(accounts: dict) -> SingaporeData:
    others = {
        name: MagicMock()
        for name in (
            "tariff coe weather holiday train psi flood traffic dengue lightning uv radar"
        ).split()
    }
    return SingaporeData(**others, sp_accounts=accounts)


async def test_listener_reloads_when_account_added():
    hass = _hass()
    entry = _entry(_subentry())
    entry.runtime_data = _runtime({})
    await _async_entry_updated(hass, entry)
    hass.config_entries.async_schedule_reload.assert_called_once_with(entry.entry_id)


async def test_listener_updates_in_place_for_token_changes():
    hass = _hass()
    entry = _entry(_subentry())
    coordinator = MagicMock()
    coordinator.async_subentry_updated = AsyncMock()
    entry.runtime_data = _runtime({"sub1": coordinator})
    await _async_entry_updated(hass, entry)
    hass.config_entries.async_schedule_reload.assert_not_called()
    coordinator.async_subentry_updated.assert_awaited_once()


# ---------------------------------------------------------------------------
# Strings / icons
# ---------------------------------------------------------------------------


def test_every_sp_sensor_key_has_translation_and_strings_mirror_en():
    strings = json.loads((COMPONENT / "strings.json").read_text())
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert strings == en
    usage = fixture_client(FixtureTransport()).fetch_usage()
    for spec in sensors_from_usage(usage, FIXED_NOW):
        key = f"{TRANSLATION_PREFIX}{spec.translation_key}"
        assert key in strings["entity"]["sensor"], key
    sub = strings["config_subentries"][SUBENTRY_TYPE_SP_ACCOUNT]
    assert set(sub["step"]) == {"user", "mfa", "reconfigure"}
    assert "electricity_price" not in sub["step"]["user"]["data"]
    assert ISSUE_AUTH_FAILED in strings["issues"]
    for key in ("auth_failed", "requires_verification", "usage_failed"):
        assert key in strings["exceptions"]
