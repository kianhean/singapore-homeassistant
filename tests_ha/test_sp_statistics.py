"""SP Services usage history lands in a real recorder's statistics."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import SP_ACCOUNT
from .test_sp_services import _add_account, _setup_entry


@pytest.fixture(autouse=True)
async def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before the hass fixture starts."""


async def test_statistics_are_imported(
    recorder_mock, hass: HomeAssistant, mock_sources, fake_sp_client
) -> None:
    from homeassistant.components.recorder.statistics import (
        list_statistic_ids,
        statistics_during_period,
    )
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    entry = await _setup_entry(hass)
    await _add_account(hass, entry, import_statistics=True)
    await async_wait_recording_done(hass)

    statistic_id = f"singapore:sp_{SP_ACCOUNT}_electricity"
    ids = await recorder_mock.async_add_executor_job(list_statistic_ids, hass)
    by_id = {row["statistic_id"]: row for row in ids}
    assert by_id[statistic_id]["statistics_unit_of_measurement"] == "kWh"
    assert (
        by_id[f"singapore:sp_{SP_ACCOUNT}_water"]["statistics_unit_of_measurement"]
        == "m³"
    )

    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utcnow() - timedelta(days=3),
        None,
        {statistic_id},
        "hour",
        None,
        {"sum", "state"},
    )
    assert [round(row["sum"], 3) for row in stats[statistic_id]] == [0.75, 1.55]

    # A second import with the same history adds nothing (no double counting).
    coordinator = entry.runtime_data.sp_accounts[next(iter(entry.subentries))]
    await coordinator.async_refresh()
    await async_wait_recording_done(hass)
    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utcnow() - timedelta(days=3),
        None,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    assert len(stats[statistic_id]) == 2
