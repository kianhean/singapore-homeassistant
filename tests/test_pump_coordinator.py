"""Pump prices from Caltex, SPC and Shell: parsers, coordinator, sensors."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.entity import PUMP_BRAND_NAMES, PUMP_GRADES
from custom_components.singapore.pump_coordinator import (
    BRANDS,
    CALTEX_URL,
    GRADES,
    SGT,
    SHELL_MODEL_URL,
    SPC_URL,
    BrandPrices,
    PumpPrice,
    PumpPriceCoordinator,
    PumpPriceData,
    grade_of,
    parse_caltex,
    parse_shell_sheet,
    parse_spc,
    shell_sheet_url,
)
from custom_components.singapore.sensor import (
    SingaporePumpPriceSensor,
    async_setup_pump_price_sensors,
)

# Real pages, trimmed to the price sections (fetched 2026-11-22). The Shell
# sheet keeps only its workbook parts (no author metadata).
FIXTURES = Path(__file__).parent / "fixtures"
CALTEX_HTML = (FIXTURES / "caltex_home.html").read_text()
SPC_HTML = (FIXTURES / "spc_pump_price.html").read_text()
SHELL_MODEL = (FIXTURES / "shell_price_board.model.json").read_text()
SHELL_XLSX = (FIXTURES / "shell_fuel_prices.xlsx").read_bytes()
_SESSION = "custom_components.singapore.pump_coordinator.async_get_clientsession"


def test_grade_lists_match():
    assert tuple(PUMP_GRADES) == GRADES
    assert tuple(PUMP_BRAND_NAMES) == BRANDS


@pytest.mark.parametrize(
    ("name", "grade"),
    [
        ("Regular 92 with Techron®", "92"),
        ("Premium 95 with Techron®", "95"),
        ("levo 98", "98"),
        ("Shell V-Power", "premium"),
        ("Shell V-Power Diesel", "premium_diesel"),
        ("Caltex Diesel with Techron® D", "diesel"),
        ("Shell FuelSave 95", "95"),
        ("Lubricant 1095", None),
        ("Car wash", None),
    ],
)
def test_grade_of(name, grade):
    assert grade_of(name) == grade


def test_parse_caltex():
    data = parse_caltex(CALTEX_HTML)
    assert data.updated == datetime(2026, 11, 4, 13, 0, tzinfo=SGT)
    assert data.prices["95"] == PumpPrice("95", "Premium 95 with Techron®", 3.6, 3.42)
    assert set(data.prices) == {"92", "95", "98", "diesel"}
    assert data.prices["diesel"].price == 4.04


def test_parse_caltex_errors():
    with pytest.raises(UpdateFailed, match="no fuel price list"):
        parse_caltex("<html></html>")
    with pytest.raises(UpdateFailed, match="not JSON"):
        parse_caltex('<fuelprice-content list="[nope"></fuelprice-content>')
    data = parse_caltex(
        '<fuelprice-content list="[{&#34;fuelName&#34;:&#34;Car wash&#34;}, 1]">'
    )
    assert data.prices == {} and data.updated is None


def test_parse_spc():
    data = parse_spc(SPC_HTML)
    assert data.updated == datetime(2026, 10, 16, 11, 30, tzinfo=SGT)
    assert {g: p.price for g, p in data.prices.items()} == {
        "98": 4.08,
        "95": 3.56,
        "92": 3.54,
        "diesel": 4.0,
    }
    assert data.prices["95"].product == "SPC LEVO 95"
    assert data.prices["diesel"].product == "SPC Diesel"
    assert parse_spc("<html></html>").prices == {}


def test_shell_sheet_url_and_parse():
    url = shell_sheet_url(SHELL_MODEL)
    assert url.startswith("https://www.shell.com.sg/")
    assert url.endswith("/fuel-price-update-singapore.xlsx")
    assert shell_sheet_url("{}") is None

    data = parse_shell_sheet(SHELL_XLSX)
    assert data.updated == datetime(2026, 11, 18, 8, 22, 13, tzinfo=UTC)
    assert data.prices["premium"] == PumpPrice("premium", "Shell V-Power", 4.34, 3.17)
    assert {g: p.price for g, p in data.prices.items()} == {
        "95": 3.6,
        "98": 4.12,
        "premium": 4.34,
        "diesel": 4.04,
    }


def _xlsx(sheet: str, shared: list[str] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet {ns}><sheetData>{sheet}</sheetData></worksheet>",
        )
        if shared is not None:
            items = "".join(f"<si><t>{s}</t></si>" for s in shared)
            archive.writestr("xl/sharedStrings.xml", f"<sst {ns}>{items}</sst>")
    return out.getvalue()


def test_parse_shell_sheet_variants():
    # Inline strings, a nett price only in column C, no core.xml.
    data = parse_shell_sheet(
        _xlsx(
            '<row><c r="A2" t="inlineStr"><is><t>Shell FuelSave 95</t></is></c>'
            '<c r="B2"><v>3.55</v></c><c r="C2"><v>3.10</v></c></row>'
            '<row><c r="A3" t="s"><v>9</v></c><c r="B3"><v>3</v></c></row>'
            '<row><c r="A4" t="s"><v>0</v></c><c r="B4"><v>999</v></c></row>',
            ["Shell FuelSave Diesel"],
        )
    )
    assert data.prices == {"95": PumpPrice("95", "Shell FuelSave 95", 3.55, 3.1)}
    assert data.updated is None
    with pytest.raises(UpdateFailed, match="not a spreadsheet"):
        parse_shell_sheet(b"not a zip")
    assert parse_shell_sheet(_xlsx("")).prices == {}
    empty = io.BytesIO()
    zipfile.ZipFile(empty, "w").close()
    assert parse_shell_sheet(empty.getvalue()).prices == {}


def _response(*, text=None, content=None, status=200):
    response = MagicMock(status=status)
    response.text = AsyncMock(return_value=text)
    response.read = AsyncMock(return_value=content)
    if status >= 400:
        response.raise_for_status.side_effect = aiohttp.ClientResponseError(
            MagicMock(), (), status=status
        )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _session(routes: dict):
    session = MagicMock()

    def _get(url, **_kwargs):
        route = routes.get(url)
        if route is None and url.endswith(".xlsx"):
            route = routes.get("xlsx")
        if isinstance(route, Exception):
            raise route
        return route

    session.get.side_effect = _get
    return session


def _hass():
    hass = MagicMock()

    async def _executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = _executor
    return hass


def _routes(**overrides):
    routes = {
        CALTEX_URL: _response(text=CALTEX_HTML),
        SPC_URL: _response(text=SPC_HTML),
        SHELL_MODEL_URL: _response(text=SHELL_MODEL),
        "xlsx": _response(content=SHELL_XLSX),
    }
    routes.update(overrides)
    return routes


async def test_coordinator_fetches_every_brand():
    coordinator = PumpPriceCoordinator(_hass())
    with patch(_SESSION, return_value=_session(_routes())):
        data = await coordinator._async_update_data()
    assert set(data.brands) == {"caltex", "spc", "shell"}
    assert data.brands["shell"].prices["95"].price == 3.6


async def test_coordinator_keeps_a_brand_through_network_errors_only():
    coordinator = PumpPriceCoordinator(_hass())
    with patch(_SESSION, return_value=_session(_routes())):
        coordinator.data = await coordinator._async_update_data()

    # SPC unreachable: its last prices stay. Caltex changed its page: dropped.
    routes = _routes(
        **{
            SPC_URL: aiohttp.ClientError("down"),
            CALTEX_URL: _response(text="<html>redesigned</html>"),
        }
    )
    with patch(_SESSION, return_value=_session(routes)):
        data = await coordinator._async_update_data()
    assert data.brands["spc"] is coordinator.data.brands["spc"]
    assert "caltex" not in data.brands
    assert "shell" in data.brands


async def test_coordinator_fails_when_no_brand_loads():
    coordinator = PumpPriceCoordinator(_hass())
    routes = {
        CALTEX_URL: _response(status=503),
        SPC_URL: _response(text="<html></html>"),
        SHELL_MODEL_URL: _response(text="{}"),
    }
    with patch(_SESSION, return_value=_session(routes)):
        with pytest.raises(UpdateFailed) as err:
            await coordinator._async_update_data()
    assert "spc: no prices" in str(err.value)
    assert "no price sheet link" in str(err.value)


def _data():
    return PumpPriceData(
        {
            "caltex": parse_caltex(CALTEX_HTML),
            "spc": parse_spc(SPC_HTML),
        }
    )


def test_pump_price_sensor():
    coordinator = PumpPriceCoordinator(_hass())
    coordinator.data = _data()
    sensor = SingaporePumpPriceSensor(coordinator, "e1", "caltex", "95")
    assert sensor.unique_id == "e1_pump_caltex_95"
    assert sensor._attr_translation_key == "pump_95"
    assert sensor._device == "pump_caltex"
    assert sensor.native_unit_of_measurement == "SGD/L"
    assert sensor.native_value == 3.6
    assert sensor.extra_state_attributes == {
        "product": "Premium 95 with Techron®",
        "discounted_price": 3.42,
        "updated": "2026-11-04T13:00:00+08:00",
    }
    spc = SingaporePumpPriceSensor(coordinator, "e1", "spc", "92")
    assert spc.extra_state_attributes == {
        "product": "SPC LEVO 92",
        "updated": "2026-10-16T11:30:00+08:00",
    }
    gone = SingaporePumpPriceSensor(coordinator, "e1", "shell", "95")
    assert gone.native_value is None
    assert gone.extra_state_attributes == {}


def test_pump_sensors_added_as_prices_appear():
    coordinator = PumpPriceCoordinator(_hass())
    added: list = []
    unsub = async_setup_pump_price_sensors(coordinator, "e1", added.extend)
    assert added == []
    coordinator.data = PumpPriceData({"spc": parse_spc(SPC_HTML)})
    coordinator.async_update_listeners()
    coordinator.data = PumpPriceData(
        {
            "spc": parse_spc(SPC_HTML),
            "shell": BrandPrices({"premium": PumpPrice("premium", "V", 4.3)}),
        }
    )
    coordinator.async_update_listeners()
    coordinator.async_update_listeners()
    assert [s.unique_id for s in added] == [
        "e1_pump_spc_92",
        "e1_pump_spc_95",
        "e1_pump_spc_98",
        "e1_pump_spc_diesel",
        "e1_pump_shell_premium",
    ]
    unsub()
