"""NEA beach water quality: parsing, grouping, coordinator, sensors."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.beach_coordinator import (
    BAND_OPTIONS,
    BEACHES,
    BeachWaterQualityCoordinator,
    beach_of,
    parse_beaches,
)
from custom_components.singapore.sensor import (
    SingaporeBeachSensor,
    async_setup_beach_sensors,
)

# A real answer (fetched 2026-11-22): three weeks, 19 areas, all band 1.
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "nea_beach_water_quality.json").read_text()
)
_SESSION = "custom_components.singapore.beach_coordinator.async_get_clientsession"


def _with_bands(bands: dict[str, str], week: int = 0) -> dict:
    payload = copy.deepcopy(FIXTURE)
    for area in payload["data"][week]["Areas"]:
        if area["Area"] in bands:
            area["Banding"] = bands[area["Area"]]
    return payload


def test_every_area_belongs_to_a_beach():
    areas = [a["Area"] for a in FIXTURE["data"][0]["Areas"]]
    assert all(beach_of(area) for area in areas), areas
    assert beach_of("Siloso Beach") == "sentosa"
    assert beach_of("Somewhere New") is None


def test_parse_latest_week():
    data = parse_beaches(FIXTURE)
    assert data.week_start == date(2026, 11, 9)
    assert data.week_end == date(2026, 11, 15)
    assert list(data.beaches) == list(BEACHES)
    sentosa = data.beaches["sentosa"]
    assert sentosa.name == "Sentosa"
    assert [a.area for a in sentosa.areas] == [
        "Palawan Beach",
        "Siloso Beach",
        "Tanjong Beach",
    ]
    assert sentosa.band == "normal"
    assert sentosa.previous_bands == ("normal", "normal")
    assert len(data.beaches["east_coast"].areas) == 7
    # "&ndash;" in NEA's descriptions is decoded.
    assert "–" in data.beaches["changi"].areas[0].description
    assert data.beaches["changi"].latitude == pytest.approx(1.385, abs=0.02)
    assert data.advisories["normal"][0] == "Safe to Enter the Water"
    # Footnote marks (<sup>2</sup>) are dropped from the advice.
    assert "<" not in data.advisories["elevated"][1]
    assert data.advisories["elevated"][0] == (
        "Caution Advised For Primary Contact Activities"
    )


def test_a_beach_reports_its_worst_area():
    data = parse_beaches(
        _with_bands(
            {
                "East Coast Beach 3": "2",
                "East Coast Beach 5": "3",
                "Changi Beach 1": "Grey",
            }
        )
    )
    assert data.beaches["east_coast"].band == "high"
    # No result (Grey) for one area: the other one still counts.
    assert data.beaches["changi"].band == "normal"
    assert data.beaches["changi"].areas[0].band is None
    advisory = parse_beaches(_with_bands({"Punggol Beach": "Advisory"}))
    assert advisory.beaches["punggol"].band == "advisory"
    grey = parse_beaches(_with_bands({"Punggol Beach": "Grey"}))
    assert grey.beaches["punggol"].band is None
    assert BAND_OPTIONS.index("advisory") > BAND_OPTIONS.index("high")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"data": []},
        {"data": [{"Areas": []}]},
        {"data": [{"Areas": [{"Area": "Mars"}]}]},
    ],
)
def test_parse_bad_payload_raises(payload):
    with pytest.raises(UpdateFailed):
        parse_beaches(payload)


def test_parse_tolerates_odd_rows():
    data = parse_beaches(
        {
            "data": [
                {
                    "Week": "nonsense",
                    "Areas": ["x", {"Area": "Punggol Beach", "Banding": "1"}],
                }
            ],
            "areas": ["x", {"Area": "Punggol Beach", "Points": [{"Lat": "x"}]}],
            "banding": None,
        }
    )
    assert data.week_start is None
    assert data.beaches["punggol"].latitude is None
    assert data.advisories == {}


def _session(payload=None, *, status=200, json_error=False):
    response = MagicMock(status=status)
    response.json = AsyncMock(
        side_effect=ValueError("html") if json_error else None, return_value=payload
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = cm
    return session


async def test_coordinator_uses_a_5_minute_timestamp():
    session = _session(FIXTURE)
    coordinator = BeachWaterQualityCoordinator(MagicMock())
    with (
        patch(_SESSION, return_value=session),
        patch(
            "custom_components.singapore.beach_coordinator.time.time",
            return_value=1795312399.5,
        ),
    ):
        data = await coordinator._async_update_data()
    assert session.get.call_args.args[0].endswith("/GetNeaData/1795312200")
    assert "changi" in data.beaches


async def test_coordinator_errors_and_fallback():
    coordinator = BeachWaterQualityCoordinator(MagicMock())
    with patch(_SESSION, return_value=_session(status=500)):
        with pytest.raises(UpdateFailed, match="HTTP 500"):
            await coordinator._async_update_data()
    with patch(_SESSION, return_value=_session(json_error=True)):
        with pytest.raises(UpdateFailed, match="not JSON"):
            await coordinator._async_update_data()
    failing = MagicMock()
    failing.get.side_effect = aiohttp.ClientError("down")
    with patch(_SESSION, return_value=failing):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    coordinator.data = previous = parse_beaches(FIXTURE)
    with patch(_SESSION, return_value=_session(status=503)):
        assert await coordinator._async_update_data() is previous
    with patch(_SESSION, return_value=failing):
        assert await coordinator._async_update_data() is previous


def test_beach_sensor():
    coordinator = BeachWaterQualityCoordinator(MagicMock())
    coordinator.data = parse_beaches(_with_bands({"East Coast Beach 2": "2"}))
    sensor = SingaporeBeachSensor(coordinator, "e1", "east_coast", "East Coast")
    assert sensor.unique_id == "e1_beach_east_coast"
    assert sensor._attr_translation_key == "beach_water_quality"
    assert sensor._attr_translation_placeholders == {"beach": "East Coast"}
    assert sensor._attr_options == BAND_OPTIONS
    assert sensor.native_value == "elevated"
    attrs = sensor.extra_state_attributes
    assert attrs["week_start"] == "2026-11-09"
    assert attrs["week_end"] == "2026-11-15"
    assert attrs["advisory"] == "Caution Advised For Primary Contact Activities"
    assert attrs["areas"][1] == {
        "area": "East Coast Beach 2",
        "description": "Carpark B2 – Siglap Canal (East Coast Park Area C)",
        "band": "elevated",
    }
    assert attrs["previous_weeks"] == ["normal", "normal"]
    assert "latitude" in attrs and "longitude" in attrs

    gone = SingaporeBeachSensor(coordinator, "e1", "lazarus", "Lazarus")
    assert gone.native_value is None
    assert gone.extra_state_attributes == {}


def test_beach_sensors_added_once():
    coordinator = BeachWaterQualityCoordinator(MagicMock())
    added: list = []
    unsub = async_setup_beach_sensors(coordinator, "e1", added.extend)
    coordinator.data = parse_beaches(FIXTURE)
    coordinator.async_update_listeners()
    coordinator.async_update_listeners()
    assert [s.unique_id for s in added] == [f"e1_beach_{key}" for key in BEACHES]
    unsub()
