"""SP Services account subentries against a real Home Assistant instance."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from sp_services import SessionExpiredError

from custom_components.singapore.const import (
    CONF_ACCOUNT_NO,
    CONF_IMPORT_STATISTICS,
    CONF_SESSION,
    DOMAIN,
    SUBENTRY_SP_ACCOUNT,
)

from .conftest import SP_ACCOUNT, sp_session


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Singapore", data={"name": "x"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _add_account(
    hass: HomeAssistant, entry: MockConfigEntry, *, import_statistics: bool = False
):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_SP_ACCOUNT), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert (
        "sp-services-e2e --export-session"
        in (result["description_placeholders"]["command"])
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_SESSION: sp_session(), CONF_IMPORT_STATISTICS: import_statistics},
    )
    await hass.async_block_till_done()
    return result


async def test_add_account_creates_sensors_on_its_own_device(
    hass: HomeAssistant, mock_sources, fake_sp_client
) -> None:
    entry = await _setup_entry(hass)

    result = await _add_account(hass, entry)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"SP Services {SP_ACCOUNT}"
    (subentry,) = (
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_SP_ACCOUNT
    )
    assert subentry.unique_id == SP_ACCOUNT
    assert subentry.data[CONF_ACCOUNT_NO] == SP_ACCOUNT
    # The re-exported session is stored, not the pasted one.
    assert subentry.data[CONF_SESSION] == sp_session("rolled")

    registry = er.async_get(hass)
    sp_entities = [
        e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.config_subentry_id == subentry.subentry_id
    ]
    assert len(sp_entities) == 5
    assert all(e.unique_id.startswith(entry.entry_id) for e in sp_entities)

    today = hass.states.get(f"sensor.sp_services_{SP_ACCOUNT}_electricity_today")
    assert today is not None
    assert float(today.state) == 9.5
    assert today.attributes["unit_of_measurement"] == "kWh"
    assert today.attributes["device_class"] == "energy"
    water = hass.states.get(f"sensor.sp_services_{SP_ACCOUNT}_water_last_month")
    assert water is not None and float(water.state) == 33.9

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    (device,) = (
        d
        for d in devices
        if (DOMAIN, f"{entry.entry_id}_sp_{SP_ACCOUNT}") in d.identifiers
    )
    assert subentry.subentry_id in device.config_entries_subentries[entry.entry_id]

    # The same account cannot be added twice.
    result = await _add_account(hass, entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("session", "renew_error", "field", "error"),
    [
        ("not a session!", None, CONF_SESSION, "invalid_session"),
        (None, SessionExpiredError("ended"), CONF_SESSION, "session_expired"),
    ],
)
async def test_add_account_errors(
    hass: HomeAssistant,
    mock_sources,
    fake_sp_client,
    session,
    renew_error,
    field,
    error,
) -> None:
    entry = await _setup_entry(hass)
    fake_sp_client.renew_error = renew_error

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_SP_ACCOUNT), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_SESSION: session or sp_session(), CONF_IMPORT_STATISTICS: False},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_ended_session_raises_issue_and_reconfigure_recovers(
    hass: HomeAssistant, mock_sources, fake_sp_client
) -> None:
    entry = await _setup_entry(hass)
    await _add_account(hass, entry)
    subentry = next(
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_SP_ACCOUNT
    )
    coordinator = entry.runtime_data.sp_accounts[subentry.subentry_id]
    sensor_id = f"sensor.sp_services_{SP_ACCOUNT}_electricity_today"

    # The session ends while HA is running.
    fake_sp_client.renew_error = SessionExpiredError("login_required")
    coordinator._client.token_set = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    issue_id = f"sp_session_expired_{subentry.subentry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert hass.states.get(sensor_id).state == "unavailable"

    # The user logs in again through Reconfigure.
    fake_sp_client.renew_error = None
    fake_sp_client.session_value = "after-relogin"
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_SP_ACCOUNT),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_SESSION: sp_session("new"), CONF_IMPORT_STATISTICS: False},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=15))
    await hass.async_block_till_done()

    assert entry.subentries[subentry.subentry_id].data[CONF_SESSION] == sp_session(
        "after-relogin"
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert float(hass.states.get(sensor_id).state) == 9.5
    # Reconfiguring must not have reloaded the whole Singapore entry.
    assert entry.runtime_data.sp_accounts[subentry.subentry_id] is coordinator


async def test_hourly_session_write_back_does_not_reload(
    hass: HomeAssistant, mock_sources, fake_sp_client
) -> None:
    entry = await _setup_entry(hass)
    await _add_account(hass, entry)
    subentry_id = next(iter(entry.runtime_data.sp_accounts))
    coordinator = entry.runtime_data.sp_accounts[subentry_id]

    fake_sp_client.session_value = "rolled-again"
    coordinator._client.token_set = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert entry.subentries[subentry_id].data[CONF_SESSION] == sp_session(
        "rolled-again"
    )
    assert entry.runtime_data.sp_accounts[subentry_id] is coordinator
