"""End-to-end tests that hit the live SP Group tariff page.

Run with:
    pytest tests/test_e2e.py -v -m e2e

Skipped by default in CI (no ``-m e2e`` flag).  These tests are the canary
for website structure changes: if _parse_tariff raises or returns zeroes,
the scraper needs updating before users see "Failed setup" errors.
"""

from __future__ import annotations

import pytest

# Skip entire module unless -m e2e is explicitly passed
pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

_URL = "https://www.spgroup.com.sg/our-services/utilities/tariff-information"

# Sanity-check bounds: tariffs that fall outside these ranges almost certainly
# indicate a parse error rather than a real rate.
_ELECTRICITY_MIN, _ELECTRICITY_MAX = 10.0, 60.0  # ¢/kWh
_GAS_MIN, _GAS_MAX = 5.0, 50.0  # ¢/kWh
_WATER_MIN, _WATER_MAX = 1.0, 10.0  # SGD/m³
_NETWORK_MIN, _NETWORK_MAX = 1.0, 20.0  # ¢/kWh


def _fetch_html() -> str:
    """Fetch the tariff page synchronously using requests."""
    import requests  # imported lazily so unit tests don't need it

    resp = requests.get(_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Live fetch → parse
# ---------------------------------------------------------------------------


def test_e2e_fetch_and_parse():
    """Fetch the live page and assert the parser returns plausible values."""
    from custom_components.singapore.coordinator import _parse_tariff

    html = _fetch_html()
    assert len(html) > 1000, (
        f"Page suspiciously short ({len(html)} bytes) — likely a bot-block page"
    )

    data = _parse_tariff(html)

    assert _ELECTRICITY_MIN < data.electricity_price < _ELECTRICITY_MAX, (
        f"electricity_price={data.electricity_price} out of expected range "
        f"[{_ELECTRICITY_MIN}, {_ELECTRICITY_MAX}] ¢/kWh"
    )
    assert _NETWORK_MIN < data.network_cost < _NETWORK_MAX, (
        f"network_cost={data.network_cost} out of expected range "
        f"[{_NETWORK_MIN}, {_NETWORK_MAX}] ¢/kWh"
    )
    assert _GAS_MIN < data.gas_price < _GAS_MAX, (
        f"gas_price={data.gas_price} out of expected range "
        f"[{_GAS_MIN}, {_GAS_MAX}] ¢/kWh"
    )
    assert _WATER_MIN < data.water_price < _WATER_MAX, (
        f"water_price={data.water_price} out of expected range "
        f"[{_WATER_MIN}, {_WATER_MAX}] SGD/m³"
    )
    assert data.solar_export_price > 0, "solar_export_price must be positive"
    assert data.quarter in ("Q1", "Q2", "Q3", "Q4"), (
        f"quarter={data.quarter!r} not recognised — date parsing may be broken"
    )
    assert data.year >= 2024, f"year={data.year} looks wrong"


def test_e2e_raw_html_debug(capsys):
    """Fetch and print the first 3 000 chars of HTML to aid parser debugging.

    This test always passes; its job is to give you a quick snapshot of what
    the page looks like when running ``pytest tests/test_e2e.py -v -s -m e2e``.
    """
    html = _fetch_html()
    with capsys.disabled():
        print(f"\n=== SP Group page ({len(html)} bytes) — first 3000 chars ===")
        print(html[:3000])
        print("=== end of snippet ===\n")
