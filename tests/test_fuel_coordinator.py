"""Tests for SingStat M213761 fuel price coordinator and sensors."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.fuel_coordinator import (
    FUEL_SERIES_KEYS,
    FuelData,
    FuelPrice,
    FuelPriceCoordinator,
    parse_fuel_prices,
)
from custom_components.singapore.sensor import SingaporeFuelPriceSensor

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "singstat_m213761_fuel.json").read_text()
)


def test_parse_fuel_prices_fixture():
    data = parse_fuel_prices(FIXTURE)
    assert data.data_last_updated == "23/10/2026"
    assert set(data.prices) == set(FUEL_SERIES_KEYS)
    assert data.prices["ron92"].value == pytest.approx(2.91)
    assert data.prices["ron95"].value == pytest.approx(2.92)
    assert data.prices["ron98"].value == pytest.approx(3.44)
    assert data.prices["diesel"].value == pytest.approx(3.38)
    assert data.prices["ron95"].period == "2026 Sep"


def test_parse_fuel_prices_rejects_bad_payload():
    with pytest.raises(UpdateFailed):
        parse_fuel_prices({})
    with pytest.raises(UpdateFailed):
        parse_fuel_prices({"Data": {"row": [{"rowText": "Bananas", "columns": []}]}})


@pytest.mark.asyncio
async def test_fuel_coordinator_fetch_success_and_errors():
    coord = FuelPriceCoordinator(MagicMock())

    resp = MagicMock(status=200)
    resp.json = AsyncMock(return_value=FIXTURE)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm

    with patch(
        "custom_components.singapore.fuel_coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coord._async_update_data()
    assert data.prices["ron95"].value == pytest.approx(2.92)
    assert coord.last_updated is not None

    resp.status = 502
    with (
        patch(
            "custom_components.singapore.fuel_coordinator.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(UpdateFailed, match="HTTP 502"),
    ):
        await coord._async_update_data()

    session.get.side_effect = aiohttp.ClientError("network down")
    with (
        patch(
            "custom_components.singapore.fuel_coordinator.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(UpdateFailed, match="network down"),
    ):
        await coord._async_update_data()


def test_fuel_sensor_values_and_attributes():
    coord = MagicMock(spec=FuelPriceCoordinator)
    coord.data = FuelData(
        prices={
            "ron95": FuelPrice(
                value=2.92,
                period="2026 Sep",
                series="Petrol, 95 Octane (Per Litre)",
            )
        },
        data_last_updated="23/10/2026",
    )
    coord.last_updated = None

    sensor = SingaporeFuelPriceSensor(coord, "e1", "ron95")
    assert sensor.unique_id == "e1_fuel_ron95"
    assert sensor._attr_translation_key == "fuel_ron95"
    assert sensor.native_value == pytest.approx(2.92)
    attrs = sensor.extra_state_attributes
    assert attrs["period"] == "2026 Sep"
    assert attrs["data_last_updated"] == "23/10/2026"
