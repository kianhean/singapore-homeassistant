"""SP account long-term statistics import, with a fake recorder.

tests_ha/test_sp_account_statistics.py checks the same import lands in a real
recorder; these cover the branches (watermarks, re-pricing, unit_class, the
background task) without one.
"""

from __future__ import annotations

import asyncio
import re
import sys
from types import ModuleType
from typing import TypedDict
from unittest.mock import MagicMock

import pytest

from custom_components.singapore.sp_account_coordinator import SpAccountCoordinator

from .sp_account.conftest import fixture_client
from .test_sp_account import _entry, _hass, _subentry


class _OldMetaData(TypedDict):
    """StatisticMetaData before HA 2025.10: no unit_class."""

    has_sum: bool
    statistic_id: str


class _NewMetaData(_OldMetaData, total=False):
    unit_class: str | None


class _Calls(list):
    """Recorded (metadata, rows) calls, plus the fake models module."""

    models: ModuleType


@pytest.fixture
def recorder(monkeypatch):
    """Fake recorder modules; returns the recorded add_external_statistics calls."""
    calls = _Calls()
    statistics = ModuleType("homeassistant.components.recorder.statistics")
    statistics.async_add_external_statistics = lambda hass, meta, stats: calls.append(
        (meta, stats)
    )
    models = ModuleType("homeassistant.components.recorder.models.statistics")
    models.StatisticMeanType = MagicMock(NONE="none")
    models.StatisticMetaData = _NewMetaData
    for name, module in {
        "homeassistant.components.recorder": ModuleType("recorder"),
        "homeassistant.components.recorder.models": ModuleType("models"),
        "homeassistant.components.recorder.statistics": statistics,
        "homeassistant.components.recorder.models.statistics": models,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    calls.models = models
    return calls


async def _coordinator(price: float | None = 29.29):
    hass = _hass()
    hass.config.components = {"recorder"}
    sub = _subentry()
    coordinator = SpAccountCoordinator(hass, _entry(sub), sub, client=fixture_client())
    coordinator.data = await coordinator._async_update_data()
    if price is not None:
        coordinator.tariff = MagicMock(data=MagicMock(electricity_price=price))
    return coordinator


def _ids(calls) -> list[str]:
    """The statistic keys written, without the "singapore:<premise>_" prefix."""
    return [
        re.sub(r"^singapore:.*?_(?=[a-z])", "", m["statistic_id"]) for m, _ in calls
    ]


async def test_import_writes_every_series(recorder):
    coordinator = await _coordinator()
    await coordinator.async_import_billed_history()

    ids = _ids(recorder)
    assert "electricity" in ids
    assert "electricity_cost" in ids
    assert "last_bill" in ids
    for meta, stats in recorder:
        assert meta["source"] == "singapore"
        assert meta["statistic_id"].startswith("singapore:")
        assert meta["has_sum"] is True
        assert "unit_class" in meta  # the running "HA" knows unit_class
        assert stats
        assert all({"start", "state", "sum"} <= set(row) for row in stats)
    cost_meta = next(m for m, _ in recorder if m["statistic_id"].endswith("_cost"))
    assert cost_meta["unit_of_measurement"] == "SGD"
    assert coordinator._imported_price == 0.2929


async def test_import_is_incremental(recorder):
    coordinator = await _coordinator()
    await coordinator.async_import_billed_history()
    first = len(recorder)
    # Nothing new since the last import: no rows are re-sent.
    await coordinator.async_import_billed_history()
    assert len(recorder) == first


async def test_new_price_resends_only_the_cost_series(recorder):
    coordinator = await _coordinator()
    await coordinator.async_import_billed_history()
    recorder.clear()
    coordinator.tariff.data.electricity_price = 30.0
    await coordinator.async_tariff_updated()
    assert _ids(recorder) == ["electricity_cost"]
    assert coordinator._imported_price == 0.3


async def test_no_tariff_skips_the_cost_series(recorder):
    coordinator = await _coordinator(price=None)
    await coordinator.async_import_billed_history()
    assert "electricity_cost" not in _ids(recorder)
    assert "electricity" in _ids(recorder)


async def test_old_ha_gets_no_unit_class(recorder):
    recorder.models.StatisticMetaData = _OldMetaData
    coordinator = await _coordinator()
    await coordinator.async_import_billed_history()
    assert recorder
    assert all("unit_class" not in meta for meta, _stats in recorder)


async def test_a_new_premise_resets_the_watermarks(recorder):
    coordinator = await _coordinator()
    await coordinator.async_import_billed_history()
    first = len(recorder)
    coordinator._imported_premise = "another premise"
    await coordinator.async_import_billed_history()
    assert len(recorder) == 2 * first


async def test_import_errors_are_logged_not_raised(recorder, caplog):
    coordinator = await _coordinator()
    recorder.models.StatisticMetaData = None  # breaks the import
    await coordinator.async_import_billed_history()
    assert "failed to import SP account statistics" in caplog.text


async def test_import_without_data_does_nothing(recorder):
    coordinator = await _coordinator()
    coordinator.data = None
    await coordinator.async_import_billed_history()
    assert recorder == []


async def test_schedule_and_stop_the_background_import(recorder):
    coordinator = await _coordinator()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(usage=None):
        started.set()
        await release.wait()

    coordinator.async_import_billed_history = _slow
    coordinator.hass.async_create_task = asyncio.ensure_future
    coordinator._schedule_stats_import(coordinator.data)
    task = coordinator._stats_task
    await started.wait()
    # A second poll while the first import runs doesn't start another.
    coordinator._schedule_stats_import(coordinator.data)
    assert coordinator._stats_task is task

    await coordinator.async_stop_stats_import()
    assert task.cancelled()
    # Stopped: later polls don't schedule imports, and stopping again is a no-op.
    coordinator._schedule_stats_import(coordinator.data)
    assert coordinator._stats_task is None
    await coordinator.async_stop_stats_import()
