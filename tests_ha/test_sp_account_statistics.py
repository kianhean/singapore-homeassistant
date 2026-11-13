"""SP account Energy statistics land in a real recorder.

Separate module because the recorder must be set up before ``hass`` (the
``mock_recorder_before_hass`` override applies to every test in a module).
"""

from __future__ import annotations

import pytest
from homeassistant.components.recorder.statistics import list_statistic_ids
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.singapore.sp_account.history import external_statistic_id

from .test_sp_account import _add_account, _setup


@pytest.fixture
async def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder test database before hass starts."""


async def test_energy_statistics_are_imported(
    recorder_mock, hass: HomeAssistant, mock_sources, sp_transport
) -> None:
    entry = await _setup(hass)
    await _add_account(hass, entry, electricity_price=0.3)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    ids = await recorder_mock.async_add_executor_job(list_statistic_ids, hass)
    statistic_ids = {item["statistic_id"] for item in ids}
    usage = entry.runtime_data.sp_accounts[next(iter(entry.subentries))].data
    for key in ("electricity", "electricity_cost"):
        assert external_statistic_id(usage.premise_id, key) in statistic_ids
