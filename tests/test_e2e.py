"""End-to-end tests that hit live external APIs used by the integration.

Run with:
    pytest tests/test_e2e.py -v -m e2e

Skipped by default in CI (no ``-m e2e`` flag).  These tests are the canary
for upstream payload/markup changes across every external source.
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

_SP_URL = "https://www.spgroup.com.sg/our-services/utilities/tariff-information"
_COE_URL = (
    "https://data.gov.sg/api/action/datastore_search"
    "?resource_id=d_69b3380ad7e51aff3a7dcc84eba52b8a"
    "&limit=10"
    "&sort=month%20desc%2Cbidding_no%20desc"
)
_WEATHER_URL = "https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast"
_FOUR_DAY_URL = "https://api-open.data.gov.sg/v2/real-time/api/four-day-outlook"
_WEATHER_READING_URL = "https://api.data.gov.sg/v1/environment/air-temperature"
_HOLIDAY_URL = "https://www.mom.gov.sg/employment-practices/public-holidays"
_TRAIN_URL = "https://www.mytransport.sg/trainstatus#"

# Sanity-check bounds: tariffs that fall outside these ranges almost certainly
# indicate a parse error rather than a real rate.
_ELECTRICITY_MIN, _ELECTRICITY_MAX = 10.0, 60.0  # ¢/kWh
_GAS_MIN, _GAS_MAX = 5.0, 50.0  # ¢/kWh
_WATER_MIN, _WATER_MAX = 1.0, 10.0  # SGD/m³
_NETWORK_MIN, _NETWORK_MAX = 1.0, 20.0  # ¢/kWh


def _fetch_url_html(url: str) -> str:
    import niquests

    try:
        with niquests.Session() as session:
            response = session.get(url, headers=_HEADERS, timeout=30)
            response.raise_for_status()
            return response.text
    except Exception as err:
        pytest.skip(
            f"Skipping e2e due to external network/proxy error for {url}: {err}"
        )


def _fetch_json(url: str) -> dict:
    import niquests

    try:
        with niquests.Session() as session:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
    except Exception as err:
        pytest.skip(
            f"Skipping e2e due to external network/proxy error for {url}: {err}"
        )


# ---------------------------------------------------------------------------
# Live fetch → parse (all external APIs)
# ---------------------------------------------------------------------------


def test_e2e_fetch_and_parse():
    """Fetch the live page and assert the parser returns plausible values."""
    from custom_components.singapore.coordinator import _parse_tariff

    html = _fetch_url_html(_SP_URL)
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
    assert data.quarter in (
        "Q1",
        "Q2",
        "Q3",
        "Q4",
    ), f"quarter={data.quarter!r} not recognised — date parsing may be broken"
    assert data.year >= 2024, f"year={data.year} looks wrong"


def test_e2e_coe_api_fetch_and_parse():
    """Fetch live COE API and assert parser returns a recent exercise."""
    from custom_components.singapore.coe_coordinator import _parse_coe

    payload = _fetch_json(_COE_URL)
    data = _parse_coe(payload)

    assert data.month.count("-") == 1, f"month={data.month!r} has unexpected format"
    assert data.bidding_no in (1, 2), f"bidding_no={data.bidding_no} is invalid"
    assert data.premiums, "Expected at least one COE premium"
    assert all(v > 0 for v in data.premiums.values()), (
        f"Non-positive COE premium found: {data.premiums}"
    )


def test_e2e_weather_forecast_api_fetch_and_parse():
    """Fetch live 2-hour forecast endpoint and assert at least one area forecast."""
    from custom_components.singapore.weather_coordinator import _parse_weather

    payload = _fetch_json(_WEATHER_URL)
    data = _parse_weather(payload)

    assert data.areas, "No forecast areas parsed from data.gov.sg weather payload"
    first_area = next(iter(data.areas.values()))
    assert first_area.area, "Parsed area name is empty"
    assert first_area.condition_text, "Parsed condition text is empty"


def test_e2e_four_day_forecast_api_fetch_and_parse():
    """Fetch live 4-day outlook endpoint and assert plausible daily forecasts."""
    from custom_components.singapore.weather_coordinator import _parse_four_day

    payload = _fetch_json(_FOUR_DAY_URL)
    entries = _parse_four_day(payload)

    assert entries, "No four-day forecast entries parsed from data.gov.sg payload"
    assert len(entries) <= 5, f"Unexpectedly many entries: {len(entries)}"

    for entry in entries:
        assert entry.condition_text, f"Entry has empty condition_text: {entry}"
        assert entry.date.tzinfo is not None, "date must be timezone-aware"
        if entry.temp_high is not None and entry.temp_low is not None:
            assert 15.0 < entry.temp_low < entry.temp_high < 45.0, (
                f"Temperature range looks wrong: {entry.temp_low}–{entry.temp_high}°C"
            )
        if entry.humidity_high is not None and entry.humidity_low is not None:
            assert 0 <= entry.humidity_low <= entry.humidity_high <= 100, (
                f"Humidity range out of bounds: {entry.humidity_low}–{entry.humidity_high}%"
            )
        if entry.wind_speed_high is not None:
            assert 0 <= entry.wind_speed_high <= 150, (
                f"Wind speed looks wrong: {entry.wind_speed_high} km/h"
            )


def test_e2e_weather_reading_api_shape():
    """Fetch one live reading endpoint and verify expected rows exist."""
    from custom_components.singapore.weather_coordinator import _extract_readings_rows

    payload = _fetch_json(_WEATHER_READING_URL)
    rows = _extract_readings_rows(payload)

    assert rows, "No reading rows parsed from live air-temperature payload"
    assert any("value" in row for row in rows), "No 'value' key found in readings rows"


def test_e2e_holiday_page_fetch_and_parse():
    """Fetch live MOM public holiday page and assert holidays parse."""
    from custom_components.singapore.holiday_coordinator import _parse_public_holidays

    html = _fetch_url_html(_HOLIDAY_URL)
    holidays = _parse_public_holidays(html)

    assert holidays, "No holidays parsed from MOM public holidays page"
    assert all(h.name for h in holidays), "Found holiday with empty name"


def test_e2e_train_status_page_fetch_and_parse():
    """Fetch live train status page and assert parser emits overall + per-line status."""
    from custom_components.singapore.train_coordinator import (
        TRAIN_LINES,
        _parse_train_status,
    )

    html = _fetch_url_html(_TRAIN_URL)
    data = _parse_train_status(html)

    assert data.status in {"normal", "planned", "disruption"}, (
        f"Unexpected network status: {data.status}"
    )
    assert data.line_statuses, "No per-line statuses parsed"
    assert set(data.line_statuses) == set(TRAIN_LINES), "Per-line status keys mismatch"


def test_e2e_raw_html_debug(capsys):
    """Fetch and print diagnostic info to aid parser debugging.

    Always passes. Run with ``pytest tests/test_e2e.py -v -s -m e2e`` to see output.
    """
    import json
    import re

    from bs4 import BeautifulSoup

    html = _fetch_url_html(_SP_URL)
    soup = BeautifulSoup(html, "html.parser")

    with capsys.disabled():
        print(f"\n=== SP Group page ({len(html)} bytes) ===")

        # __NEXT_DATA__ — the motherlode for Next.js SSR pages
        next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if next_data_tag and next_data_tag.string:
            try:
                nd = json.loads(next_data_tag.string)
                print("\n--- __NEXT_DATA__ (pretty, first 3000 chars) ---")
                print(json.dumps(nd, indent=2)[:3000])
            except json.JSONDecodeError:
                print("\n--- __NEXT_DATA__ raw (first 3000 chars) ---")
                print(next_data_tag.string[:3000])
        else:
            print("\n[no __NEXT_DATA__ script tag found]")

        # All floats found anywhere in page text, with surrounding context
        page_text = soup.get_text(" ", strip=True)
        floats = re.findall(r"\b\d{1,3}\.\d{1,2}\b", page_text)
        print(f"\n--- floats in page text: {floats[:40]} ---")
        for f in floats[:20]:
            idx = page_text.find(f)
            if idx >= 0:
                ctx = page_text[max(0, idx - 120) : idx + 120]
                print(f"  {f!r:>8}  context: ...{ctx}...")

        # Inline <script> tags that contain tariff-like numbers
        for i, script in enumerate(soup.find_all("script")):
            src = script.string or ""
            if re.search(r"\b(2[0-9]\.\d{2}|3[0-9]\.\d{2})\b", src):
                print(f"\n--- inline script {i} snippet (first 500 chars) ---")
                print(src[:500])

        print("\n=== end of debug ===\n")
