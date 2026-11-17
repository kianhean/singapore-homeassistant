"""SP account Energy statistics land in a real recorder.

Separate module because the recorder must be set up before ``hass`` (the
``mock_recorder_before_hass`` override applies to every test in a module).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.singapore.sp_account.client import HttpResponse
from custom_components.singapore.sp_account.const import JARVIS_AMI_PATH
from custom_components.singapore.sp_account.history import external_statistic_id
from custom_components.singapore.sp_account.models import SG_TZ
from tests.sp_account.conftest import FIXED_NOW

from .test_sp_account import _add_account, _setup


@pytest.fixture
async def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder test database before hass starts."""


def _month_of_ami() -> bytes:
    """31 days of half-hour slots up to the fixed clock (0.15 kWh at 03:00).

    The recorded AMI fixture holds four slots, too few for the insights that
    need full days (always-on power, yesterday, projected cost).
    """
    start = datetime(2026, 7, 3, tzinfo=SG_TZ)
    rows = []
    at = start
    while at < FIXED_NOW:
        amount = 0.15 if (at.hour, at.minute) == (3, 0) else 0.4
        rows.append({"date": at.isoformat(), "amount": None, "consumption": amount})
        at += timedelta(minutes=30)
    body = {"unit": "kWh", "history": [{"data": rows}]}
    return json.dumps(body).encode()


@pytest.fixture
def sp_month_of_ami(sp_transport):
    sp_transport.responses[JARVIS_AMI_PATH] = HttpResponse(
        status=200, body=_month_of_ami()
    )
    return sp_transport


async def test_energy_statistics_are_imported(
    recorder_mock, hass: HomeAssistant, mock_sources, sp_transport
) -> None:
    entry = await _setup(hass)
    await _add_account(hass, entry)  # cost priced from the scraped tariff
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    ids = await recorder_mock.async_add_executor_job(list_statistic_ids, hass)
    statistic_ids = {item["statistic_id"] for item in ids}
    usage = entry.runtime_data.sp_accounts[next(iter(entry.subentries))].data
    for key in (
        "electricity",
        "electricity_cost",
        "water",
        "last_bill",
        "electricity_monthly",
        "electricity_peer_average",
        "electricity_peer_efficient",
        "water_monthly",
        "water_peer_average",
        "water_peer_efficient",
    ):
        assert external_statistic_id(usage.premise_id, key) in statistic_ids

    peer_id = external_statistic_id(usage.premise_id, "electricity_peer_average")
    rows = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        {peer_id},
        "month",
        None,
        {"state"},
    )
    assert [row["state"] for row in rows[peer_id]] == [200.0, 200.0]


async def test_insight_sensors_and_calendar_in_real_ha(
    recorder_mock, hass: HomeAssistant, mock_sources, sp_month_of_ami
) -> None:
    entry = await _setup(hass)
    await _add_account(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)

    prefix = "sensor.1_example_road_singapore_"
    always_on = hass.states.get(f"{prefix}always_on_power")
    assert always_on.state == "300.0"  # 0.15 kWh in 30 min
    assert always_on.attributes["unit_of_measurement"] == "W"
    assert hass.states.get(f"{prefix}electricity_yesterday").state == "18.95"
    assert hass.states.get(f"{prefix}bill_status").state == "due"
    window = hass.states.get(f"{prefix}meter_reading_window")
    assert window.state == "upcoming"
    assert window.attributes["options"] == ["upcoming", "open", "closed"]
    assert hass.states.get(f"{prefix}electricity_vs_similar_homes").state == "-29"
    for key in (
        "electricity_cost_yesterday",
        "electricity_cost_today",
        "electricity_cost_this_month",
        "projected_electricity_cost",
    ):
        state = hass.states.get(f"{prefix}{key}")
        assert state is not None, key  # priced from the scraped tariff
        assert float(state.state) > 0
    calendar = hass.states.get(
        "calendar.1_example_road_singapore_bills_and_meter_readings"
    )
    assert calendar is not None


EXAMPLE = Path(__file__).parent.parent / "examples" / "sp_account_dashboard.yaml"
EXAMPLE_PREMISE_ID = "2001590888"


def _cards(node):
    """Every card dict in a dashboard config, however deeply nested."""
    if isinstance(node, dict):
        if "type" in node:
            yield node
        for value in node.values():
            yield from _cards(value)
    elif isinstance(node, list):
        for item in node:
            yield from _cards(item)


async def test_example_sp_dashboard_matches_a_real_install(
    recorder_mock, hass: HomeAssistant, mock_sources, sp_month_of_ami
) -> None:
    """examples/sp_account_dashboard.yaml only uses entities/statistics that exist.

    The fixture account's device is "1 Example Road, Singapore", the slug the
    example uses; its premise id stands in for the example's placeholder.
    """
    entry = await _setup(hass)
    await _add_account(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    usage = entry.runtime_data.sp_accounts[next(iter(entry.subentries))].data
    statistic_prefix = external_statistic_id(usage.premise_id, "x")[:-1]

    config = yaml.safe_load(EXAMPLE.read_text())
    cards = list(_cards(config))
    entities: set[str] = set()
    statistics: dict[str, set[tuple[str, str]]] = {}
    for card in cards:
        refs = [card.get("entity")] + [
            ref.get("entity") if isinstance(ref, dict) else ref
            for ref in card.get("entities", [])
        ]
        for ref in filter(None, refs):
            if ref.startswith("singapore:"):
                ref = ref.replace(f"singapore:{EXAMPLE_PREMISE_ID}_", statistic_prefix)
                if card["type"] == "statistic":
                    period, types = card["period"]["calendar"]["period"], {"change"}
                else:
                    period, types = card["period"], set(card["stat_types"])
                for stat_type in types:
                    statistics.setdefault(ref, set()).add((period, stat_type))
            else:
                entities.add(ref)

    missing = sorted(e for e in entities if hass.states.get(e) is None)
    assert not missing, f"example uses entities a real install lacks: {missing}"
    for card in cards:
        for attribute in card.get("state_content", []):
            if attribute != "state":
                assert attribute in hass.states.get(card["entity"]).attributes
        if card.get("type") == "attribute":
            assert card["attribute"] in hass.states.get(card["entity"]).attributes

    start = datetime(2024, 1, 1, tzinfo=UTC)
    for statistic_id, queries in statistics.items():
        for period, stat_type in queries:
            rows = await recorder_mock.async_add_executor_job(
                statistics_during_period,
                hass,
                start,
                None,
                {statistic_id},
                period,
                None,
                {stat_type},
            )
            values = [row.get(stat_type) for row in rows.get(statistic_id, [])]
            assert any(v is not None for v in values), (statistic_id, period)
