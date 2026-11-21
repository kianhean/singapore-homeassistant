"""Tests for ECB FX rate coordinator and sensors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.fx_coordinator import (
    FxCoordinator,
    FxData,
    fx_quote_units,
    parse_ecb,
)
from custom_components.singapore.sensor import SingaporeFxRateSensor

FIXTURE = (Path(__file__).parent / "fixtures" / "ecb_eurofxref_daily.xml").read_text()


def test_parse_ecb_fixture():
    data = parse_ecb(FIXTURE)
    assert data.date == "2026-11-20"
    assert "SGD" not in data.rates
    # EUR/SGD = SGD/EUR = 1.4160
    assert data.rates["EUR"] == pytest.approx(1.416, rel=1e-6)
    # USD/SGD = 1.4160 / 1.0990
    assert data.rates["USD"] == pytest.approx(1.4160 / 1.0990, rel=1e-5)
    # 100 JPY/SGD = 1.4160 / 174.35 * 100
    assert data.rates["JPY"] == pytest.approx(1.4160 / 174.35 * 100, rel=1e-5)
    assert data.rates["MYR"] == pytest.approx(1.4160 / 4.5196, rel=1e-5)


def test_parse_ecb_rejects_bad_xml_or_missing_sgd():
    with pytest.raises(UpdateFailed):
        parse_ecb("<not-xml")
    with pytest.raises(UpdateFailed):
        parse_ecb(
            "<Envelope><Cube><Cube time='2026-11-20'>"
            "<Cube currency='USD' rate='1.1'/></Cube></Cube></Envelope>"
        )


def test_fx_quote_units():
    assert fx_quote_units("USD") == 1
    assert fx_quote_units("JPY") == 100
    assert fx_quote_units("KRW") == 100


@pytest.mark.asyncio
async def test_fx_coordinator_fetch_success_and_errors():
    coord = FxCoordinator(MagicMock())

    resp = MagicMock(status=200)
    resp.text = AsyncMock(return_value=FIXTURE)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm

    with patch(
        "custom_components.singapore.fx_coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coord._async_update_data()
    assert data.date == "2026-11-20"
    assert coord.last_updated is not None

    resp.status = 503
    with (
        patch(
            "custom_components.singapore.fx_coordinator.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(UpdateFailed, match="HTTP 503"),
    ):
        await coord._async_update_data()

    session.get.side_effect = aiohttp.ClientError("offline")
    with (
        patch(
            "custom_components.singapore.fx_coordinator.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(UpdateFailed, match="offline"),
    ):
        await coord._async_update_data()


def test_fx_sensor_values_and_attributes():
    coord = MagicMock(spec=FxCoordinator)
    coord.data = FxData(date="2026-11-20", rates={"USD": 1.288444, "JPY": 0.812159})
    coord.last_updated = None

    usd = SingaporeFxRateSensor(coord, "e1", "USD")
    assert usd.unique_id == "e1_fx_usd"
    assert usd._attr_translation_placeholders == {"pair": "USD/SGD"}
    assert usd.native_value == pytest.approx(1.288444)
    assert usd.extra_state_attributes["quote_units"] == 1
    assert usd.extra_state_attributes["rate_date"] == "2026-11-20"

    jpy = SingaporeFxRateSensor(coord, "e1", "JPY")
    assert jpy._attr_translation_placeholders == {"pair": "100 JPY/SGD"}
    assert jpy.native_value == pytest.approx(0.812159)
    assert jpy.extra_state_attributes["quote_units"] == 100
