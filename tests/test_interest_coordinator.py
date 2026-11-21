"""Tests for SingStat M700071 (MAS) interest rate coordinator and sensors."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.interest_coordinator import (
    INTEREST_SERIES_KEYS,
    InterestData,
    InterestRate,
    InterestRateCoordinator,
    parse_singstat,
)
from custom_components.singapore.sensor import SingaporeInterestRateSensor

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "singstat_m700071.json").read_text()
)


def test_parse_singstat_fixture():
    data = parse_singstat(FIXTURE)
    assert data.data_last_updated == "13/11/2026"
    assert set(data.rates) == set(INTEREST_SERIES_KEYS)
    assert data.rates["sora"].value == pytest.approx(1.5526)
    assert data.rates["sora"].period == "2026 Oct"
    assert data.rates["sora_3m"].value == pytest.approx(1.297)
    assert data.rates["tbill_1y"].value == pytest.approx(1.9)
    assert data.rates["sgs_10y"].value == pytest.approx(2.24)


def test_parse_singstat_rejects_bad_payload():
    with pytest.raises(UpdateFailed):
        parse_singstat({})
    with pytest.raises(UpdateFailed):
        parse_singstat({"Data": {"row": [{"rowText": "Unknown", "columns": []}]}})


@pytest.mark.asyncio
async def test_interest_coordinator_fetch_success_and_errors():
    coord = InterestRateCoordinator(MagicMock())

    resp = MagicMock(status=200)
    resp.json = AsyncMock(return_value=FIXTURE)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm

    with patch(
        "custom_components.singapore.interest_coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coord._async_update_data()
    assert data.rates["sora"].value == pytest.approx(1.5526)
    assert coord.last_updated is not None

    resp.status = 500
    with (
        patch(
            "custom_components.singapore.interest_coordinator.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(UpdateFailed, match="HTTP 500"),
    ):
        await coord._async_update_data()

    session.get.side_effect = aiohttp.ClientError("timeout")
    with (
        patch(
            "custom_components.singapore.interest_coordinator.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(UpdateFailed, match="timeout"),
    ):
        await coord._async_update_data()


def test_interest_sensor_values_and_attributes():
    coord = MagicMock(spec=InterestRateCoordinator)
    coord.data = InterestData(
        rates={
            "sora_3m": InterestRate(
                value=1.297,
                period="2026 Oct",
                series="Compounded Singapore Overnight Rate Average (SORA) - 3 Month",
            )
        },
        data_last_updated="13/11/2026",
    )
    coord.last_updated = None

    sensor = SingaporeInterestRateSensor(coord, "e1", "sora_3m")
    assert sensor.unique_id == "e1_interest_sora_3m"
    assert sensor._attr_translation_key == "interest_sora_3m"
    assert sensor.native_value == pytest.approx(1.297)
    attrs = sensor.extra_state_attributes
    assert attrs["period"] == "2026 Oct"
    assert attrs["data_last_updated"] == "13/11/2026"
