"""SP Group e-account subentries against a real Home Assistant instance.

The SP protocol client is driven by the recorded fixtures under
``tests/sp_account/fixtures`` (vendored from maci0/home-assistant-sp), so these
tests exercise the real flow manager, HTTP views, entity platform, repairs,
diagnostics and recorder statistics without touching the network.
"""

from __future__ import annotations

from http import HTTPStatus

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.singapore.const import DOMAIN
from custom_components.singapore.sp_account import client as client_module
from custom_components.singapore.sp_account_coordinator import (
    SUBENTRY_TYPE_SP_ACCOUNT,
    auth_issue_id,
)

USERNAME = "user@example.com"
CREDENTIALS = {"username": USERNAME, "password": "secret"}


async def _setup(hass: HomeAssistant, subentries=()) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Singapore",
        data={"name": "Singapore"},
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _add_account(hass: HomeAssistant, entry: MockConfigEntry, **extra):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SP_ACCOUNT),
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**CREDENTIALS, **extra}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    return result


def _sp_entities(hass: HomeAssistant, entry: MockConfigEntry):
    registry = er.async_get(hass)
    return [
        ent
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id)
        if ent.unique_id.startswith(f"{entry.entry_id}_sp_")
    ]


async def test_subentry_forms_serialize_over_http(
    hass: HomeAssistant, hass_client, mock_sources, sp_transport
) -> None:
    """Every SP form loads through the HTTP API (maci0/home-assistant-sp #21).

    HA serializes the schema to JSON for the frontend; a bare function
    validator made this request fail with 500 "Unable to convert schema".
    """
    await async_setup_component(hass, "config", {})
    entry = await _setup(hass)
    client = await hass_client()
    url = "/api/config/config_entries/subentries/flow"

    resp = await client.post(
        url, json={"handler": [entry.entry_id, SUBENTRY_TYPE_SP_ACCOUNT]}
    )
    assert resp.status == HTTPStatus.OK, await resp.text()
    body = await resp.json()
    assert body["step_id"] == "user"
    assert {field["name"] for field in body["data_schema"]} == {
        "username",
        "password",
        "electricity_price",
    }

    # MFA form, reached via the same HTTP view.
    sp_transport.require_mfa = True
    resp = await client.post(f"{url}/{body['flow_id']}", json=CREDENTIALS)
    assert resp.status == HTTPStatus.OK, await resp.text()
    body = await resp.json()
    assert body["step_id"] == "mfa"
    sp_transport.require_mfa = False
    sp_transport.mfa_success = True
    resp = await client.post(f"{url}/{body['flow_id']}", json={"mfa_code": "123456"})
    assert resp.status == HTTPStatus.OK, await resp.text()
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)

    subentry_id = next(iter(entry.subentries))
    for step in ("credentials", "price"):
        resp = await client.post(
            url,
            json={
                "handler": [entry.entry_id, SUBENTRY_TYPE_SP_ACCOUNT],
                "subentry_id": subentry_id,
            },
        )
        assert resp.status == HTTPStatus.OK, await resp.text()
        menu = await resp.json()
        assert menu["type"] == "menu"
        resp = await client.post(
            f"{url}/{menu['flow_id']}", json={"next_step_id": step}
        )
        assert resp.status == HTTPStatus.OK, await resp.text()
        assert (await resp.json())["step_id"] == step


async def test_add_account_creates_sensors_without_storing_password(
    hass: HomeAssistant, mock_sources, sp_transport
) -> None:
    entry = await _setup(hass)
    result = await _add_account(hass, entry, electricity_price=0.3)
    assert result["type"] is FlowResultType.CREATE_ENTRY

    subentry = next(iter(entry.subentries.values()))
    assert subentry.subentry_type == SUBENTRY_TYPE_SP_ACCOUNT
    assert subentry.unique_id == USERNAME
    assert "password" not in subentry.data
    assert subentry.data["access_token"]
    assert subentry.data["electricity_price"] == 0.3

    entities = _sp_entities(hass, entry)
    assert entities, "SP account sensors were not created"
    assert all(ent.config_subentry_id == subentry.subentry_id for ent in entities)
    electricity = next(e for e in entities if e.unique_id.endswith("_electricity"))
    state = hass.states.get(electricity.entity_id)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["attribution"] == "Data provided by SP Group"

    # The rest of the integration is untouched by the reload.
    assert hass.states.get("sensor.energy_electricity_tariff") is not None

    # Same account again is refused before any login.
    result = await _add_account(hass, entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_invalid_username_is_a_field_error(
    hass: HomeAssistant, mock_sources, sp_transport
) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SP_ACCOUNT), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"username": "x" * 300, "password": "secret"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"username": "invalid_username"}


async def test_bad_password_shows_invalid_auth(
    hass: HomeAssistant, mock_sources, sp_transport
) -> None:
    entry = await _setup(hass)
    sp_transport.fail_login = True
    result = await _add_account(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not entry.subentries


async def test_rejected_session_opens_repair_issue_and_reconfigure_fixes_it(
    hass: HomeAssistant, mock_sources, sp_transport
) -> None:
    sp_transport.fail_login = True
    entry = await _setup(
        hass,
        subentries=[
            {
                "data": {"username": USERNAME},  # no tokens -> must log in
                "subentry_type": SUBENTRY_TYPE_SP_ACCOUNT,
                "title": USERNAME,
                "unique_id": USERNAME,
            }
        ],
    )
    subentry_id = next(iter(entry.subentries))
    coordinator = entry.runtime_data.sp_accounts[subentry_id]
    assert not coordinator.last_update_success
    # The rest of the integration still loaded.
    assert hass.states.get("sensor.energy_electricity_tariff") is not None

    issue = ir.async_get(hass).async_get_issue(DOMAIN, auth_issue_id(subentry_id))
    assert issue is not None
    assert issue.translation_placeholders == {"title": USERNAME}

    # The user signs in again after the client's anti-lockout cooldown.
    sp_transport.fail_login = False
    client_module._LOGIN_FAILURES.clear()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SP_ACCOUNT),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "credentials"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)

    assert coordinator.last_update_success
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, auth_issue_id(subentry_id)) is None
    )
    assert _sp_entities(hass, entry)


async def test_reconfigure_price_and_remove_account(
    hass: HomeAssistant, mock_sources, sp_transport
) -> None:
    entry = await _setup(hass)
    await _add_account(hass, entry)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SP_ACCOUNT),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "price"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"electricity_price": 0.29}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry_id].data["electricity_price"] == 0.29
    assert entry.subentries[subentry_id].data["access_token"]

    assert hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not _sp_entities(hass, entry)
    assert not entry.runtime_data.sp_accounts


async def test_diagnostics_redact_sp_account(
    hass: HomeAssistant, hass_client, mock_sources, sp_transport
) -> None:
    entry = await _setup(hass)
    await _add_account(hass, entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    [account] = diag["sp_accounts"]
    assert account["last_update_success"] is True
    assert account["has_refresh_token"] is True
    assert "electricity" in account["sensor_keys"]
    text = str(diag)
    usage = entry.runtime_data.sp_accounts[next(iter(entry.subentries))].data
    assert usage.premise_id not in text
    assert USERNAME not in text
    if usage.premise.address:
        assert usage.premise.address not in text
