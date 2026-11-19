"""End-to-end setup tests against a real Home Assistant instance."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.singapore.const import DOMAIN


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Singapore", data={"name": "Singapore"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities_and_unloads(
    hass: HomeAssistant, mock_sources
) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get("sensor.energy_electricity_tariff")
    assert state is not None
    assert float(state.state) == 29.29
    assert state.attributes["attribution"] == "Data provided by SP Group"

    coe = hass.states.get("sensor.coe_category_a")
    assert coe is not None and coe.state == "95501"

    line = hass.states.get("sensor.mrt_lrt_circle_line_status")
    assert line is not None
    assert line.state == "disruption"
    assert line.attributes["options"] == ["normal", "planned", "disruption"]

    bearing = hass.states.get("sensor.weather_wind_bearing")
    assert bearing is not None
    assert bearing.attributes["state_class"] == "measurement_angle"

    weather = hass.states.get("weather.weather_bedok")
    assert weather is not None
    assert weather.state == "lightning-rainy"

    assert hass.states.get("calendar.public_holidays") is not None

    # MOE school calendars: one per kind, on the School device.
    for entity_id in (
        "calendar.school_holidays",
        "calendar.school_terms",
        "calendar.school_exams",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["source"] == "MOE"
    assert hass.states.get("calendar.school_exams").attributes["events"] == 1

    psi = hass.states.get("sensor.air_quality_psi")
    assert psi is not None
    assert float(psi.state) == 52
    assert psi.attributes["device_class"] == "aqi"
    assert psi.attributes["band"] == "moderate"

    # Regional PSI sensors carry a location so they render on a Map card.
    east = hass.states.get("sensor.air_quality_psi_east")
    assert east is not None
    assert float(east.state) == 52
    assert east.attributes["latitude"] == 1.35735
    assert east.attributes["longitude"] == 103.94
    assert east.attributes["pm25_twenty_four_hourly"] == 14

    pm25 = hass.states.get("sensor.air_quality_pm2_5")
    assert pm25 is not None
    assert float(pm25.state) == 14
    assert pm25.attributes["device_class"] == "pm25"
    # Compare against HA's own constant: newer HA spells it with Greek mu (μ).
    assert (
        pm25.attributes["unit_of_measurement"]
        == CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
    )

    co = hass.states.get("sensor.air_quality_carbon_monoxide")
    assert co is not None
    assert float(co.state) == 0.6
    assert co.attributes["unit_of_measurement"] == "mg/m³"

    flood = hass.states.get("sensor.pub_flood_alerts")
    assert flood is not None
    assert flood.state == "1"
    assert flood.attributes["locations"] == ["Flash flood at Bt Timah Rd"]
    assert flood.attributes["attribution"] == "Data provided by PUB via data.gov.sg"

    causeway = hass.states.get("camera.traffic_cameras_woodlands_causeway")
    assert causeway is not None
    assert causeway.state == "idle"
    assert causeway.attributes["checkpoint"] == "Woodlands"
    missing = hass.states.get("camera.traffic_cameras_woodlands_checkpoint")
    assert missing is not None and missing.state == "unavailable"
    assert hass.states.get("camera.traffic_cameras_tuas_checkpoint").state == "idle"
    image = await async_get_image(hass, "camera.traffic_cameras_woodlands_causeway")
    assert image.content == b"jpeg-2701"

    radar = hass.states.get("camera.weather_rain_radar_70_km")
    assert radar is not None
    assert radar.state == "idle"
    assert radar.attributes["range"] == "70 km"
    assert radar.attributes["north"] == 1.97854
    assert hass.states.get("camera.weather_rain_radar_240_km").state == "idle"
    # No NEA map for 480 km, so that camera is registered but disabled by default.
    assert hass.states.get("camera.weather_rain_radar_480_km") is None
    radar_480 = er.async_get(hass).async_get("camera.weather_rain_radar_480_km")
    assert radar_480 is not None
    assert radar_480.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    radar_image = await async_get_image(hass, "camera.weather_rain_radar_70_km")
    # Drawn on NEA's 853 x 479 Singapore base map.
    assert radar_image.content_type == "image/jpeg"
    assert radar_image.content[:3] == b"\xff\xd8\xff"

    # Animated loops for the two mapped ranges only (frames from the same API call).
    animation = hass.states.get("camera.weather_rain_radar_70_km_animation")
    assert animation is not None
    assert animation.state == "idle"
    assert animation.attributes["frame_count"] == 1
    assert animation.attributes["first_frame_timestamp"] == "2026-11-13T22:25:00+08:00"
    assert hass.states.get("camera.weather_rain_radar_240_km_animation").state == "idle"
    assert (
        er.async_get(hass).async_get_entity_id(
            "camera", "singapore", f"{entry.entry_id}_rain_radar_animation_480km"
        )
        is None
    )
    gif = await async_get_image(hass, "camera.weather_rain_radar_70_km_animation")
    assert gif.content_type == "image/gif"
    assert gif.content[:4] == b"GIF8"

    dengue = hass.states.get("sensor.nea_dengue_clusters")
    assert dengue is not None
    assert dengue.state == "2"
    assert dengue.attributes["total_cases"] == 41

    nearest = hass.states.get("sensor.nea_nearest_dengue_cluster")
    assert nearest is not None
    assert float(nearest.state) == 0.0
    assert nearest.attributes["locality"] == "Lentor Gdns"
    assert nearest.attributes["unit_of_measurement"] == "km"

    strikes = hass.states.get("sensor.nea_lightning_strikes")
    assert strikes is not None
    assert strikes.state == "2"
    assert strikes.attributes["cloud_to_ground"] == 1

    lightning = hass.states.get("sensor.nea_nearest_lightning")
    assert lightning is not None
    assert 1.0 < float(lightning.state) < 1.2
    assert lightning.attributes["type"] == "cloud_to_ground"
    assert lightning.attributes["strikes_within_10_km"] == 1

    uv = hass.states.get("sensor.nea_uv_index")
    assert uv is not None
    assert uv.state == "9"
    assert uv.attributes["unit_of_measurement"] == "UV index"
    assert uv.attributes["state_class"] == "measurement"
    assert uv.attributes["peak_hour"] == "2026-11-13T13:00:00+08:00"
    exposure = hass.states.get("sensor.nea_uv_exposure")
    assert exposure is not None
    assert exposure.state == "very_high"
    assert exposure.attributes["options"] == [
        "low",
        "moderate",
        "high",
        "very_high",
        "extreme",
    ]

    pm25_hour = hass.states.get("sensor.air_quality_pm2_5_1_hour")
    assert pm25_hour is not None
    assert pm25_hour.state == "61.0"
    assert pm25_hour.attributes["band"] == "elevated"
    assert pm25_hour.attributes["device_class"] == "pm25"
    # The regional PSI sensors carry the hourly reading too.
    assert (
        hass.states.get("sensor.air_quality_psi_east").attributes["pm25_one_hourly"]
        == 61.0
    )

    wbgt = hass.states.get("sensor.nea_heat_stress_wbgt")
    assert wbgt is not None
    assert wbgt.state == "31.2"
    assert wbgt.attributes["station"] == "Harness Home"
    assert wbgt.attributes["highest_wbgt"] == 33.4
    assert wbgt.attributes["unit_of_measurement"] == "°C"
    stress = hass.states.get("sensor.nea_heat_stress")
    assert stress is not None
    assert stress.state == "moderate"
    assert stress.attributes["options"] == ["low", "moderate", "high"]

    hawker = hass.states.get("calendar.hawker_centres_closures")
    assert hawker is not None
    assert hawker.attributes["message"] == "Adam Road Food Centre closed (cleaning)"
    assert hawker.attributes["location"] == "2, Adam Road, Singapore 289876"

    # Carparks and taxis are opt-in: off on a fresh install.
    assert entry.runtime_data.carpark is None
    assert entry.runtime_data.taxi is None
    assert entry.runtime_data.capitaland is None
    assert hass.states.get("sensor.taxis_available") is None

    # Every entity must use a stable, entry-scoped unique ID.
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert entities
    assert all(e.unique_id.startswith(entry.entry_id) for e in entities)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_hourly_forecast_uses_area_region(
    hass: HomeAssistant, mock_sources
) -> None:
    await _setup(hass)

    bedok_state = hass.states.get("weather.weather_bedok")
    assert bedok_state.attributes["forecast_region"] == "east"

    response = await hass.services.async_call(
        "weather",
        "get_forecasts",
        {
            "entity_id": ["weather.weather_bedok", "weather.weather_woodlands"],
            "type": "hourly",
        },
        blocking=True,
        return_response=True,
    )

    bedok = response["weather.weather_bedok"]["forecast"]
    assert [f["condition"] for f in bedok] == ["lightning-rainy", "sunny"]
    assert bedok[0]["datetime"] == "2099-01-01T18:00:00+08:00"
    woodlands = response["weather.weather_woodlands"]["forecast"]
    assert [f["condition"] for f in woodlands] == ["cloudy", "cloudy"]


async def test_setup_retries_when_source_unavailable(
    hass: HomeAssistant, mock_sources
) -> None:
    from custom_components.singapore.coordinator import TARIFF_URL

    mock_sources.clear_requests()
    mock_sources._mocks = [m for m in mock_sources._mocks if str(m.url) != TARIFF_URL]
    mock_sources.get(TARIFF_URL, status=503)

    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Singapore"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_config_flow_creates_entry_and_is_single_instance(
    hass: HomeAssistant, mock_sources
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Singapore"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_diagnostics(hass: HomeAssistant, hass_client, mock_sources) -> None:
    entry = await _setup(hass)

    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diag["coordinators"]["tariff"]["data"]["electricity_price"] == 29.29
    assert diag["coordinators"]["train"]["data"]["status"] == "disruption"
    psi = diag["coordinators"]["psi"]["data"]
    assert psi["national"]["psi_twenty_four_hourly"] == 52
    assert diag["coordinators"]["flood"]["data"]["alerts"][0]["severity"] == "Minor"
    assert diag["coordinators"]["dengue"]["last_update_success"] is True


async def test_opt_in_carparks_and_taxis(
    hass: HomeAssistant, hass_client, mock_sources
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Singapore",
        data={"name": "Singapore"},
        options={"enabled_sources": ["carpark", "taxi"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    # Nearest home (no carparks chosen): the only one in the canned feed.
    carpark = hass.states.get("sensor.hdb_carparks_acb")
    assert carpark is not None
    assert carpark.state == "103"
    assert carpark.attributes["car_lots_total"] == 105
    assert carpark.attributes["address"].startswith("BLK 270/271")
    assert abs(carpark.attributes["latitude"] - 1.30106) < 1e-3
    assert carpark.attributes["unit_of_measurement"] == "lots"

    total = hass.states.get("sensor.taxis_available")
    assert total is not None and total.state == "2"
    nearby = hass.states.get("sensor.taxis_nearby")
    assert nearby is not None and nearby.state == "1"
    assert nearby.attributes["within_500_m"] == 1

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    coordinators = diagnostics["coordinators"]
    assert coordinators["carpark"]["data"]["tracked"] == ["ACB"]
    assert coordinators["taxi"]["data"]["total"] == 2


async def test_opt_in_capitaland_carparks(
    hass: HomeAssistant, hass_client, mock_sources
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Singapore",
        data={"name": "Singapore"},
        options={"enabled_sources": ["capitaland"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    bedok = hass.states.get("sensor.capitaland_carparks_bedok_mall")
    assert bedok is not None
    assert bedok.state == "39"
    assert bedok.attributes["total_lots"] == 264
    assert bedok.attributes["site_code"] == "BM"
    assert bedok.attributes["unit_of_measurement"] == "lots"
    assert hass.states.get("sensor.capitaland_carparks_plaza_singapura") is not None
    # Offices aren't malls.
    assert hass.states.get("sensor.capitaland_carparks_capital_tower") is None

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    sites = diagnostics["coordinators"]["capitaland"]["data"]["sites"]
    assert sites["BM"]["available"] == 39


async def test_example_dashboard_matches_a_real_install(
    hass: HomeAssistant, mock_sources
) -> None:
    """Every entity (and attribute) examples/dashboard.yaml uses exists.

    With the opt-in carparks and taxis on and the example's carpark numbers
    chosen, as the dashboard's header comment tells users to set up.
    """
    from pathlib import Path

    import yaml

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Singapore",
        data={"name": "Singapore"},
        options={"enabled_sources": ["carpark", "taxi"], "carparks": "B8B, B8, B6"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    def _cards(node):
        if isinstance(node, dict):
            if "type" in node:
                yield node
            for value in node.values():
                yield from _cards(value)
        elif isinstance(node, list):
            for item in node:
                yield from _cards(item)

    example = Path(__file__).parents[1] / "examples" / "dashboard.yaml"
    config = yaml.safe_load(example.read_text())
    checked = 0
    for card in _cards(config):
        entity_id = card.get("entity")
        if not isinstance(entity_id, str):
            continue
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        # Attribute rows and tile state_content must name real attributes.
        if (attribute := card.get("attribute")) is not None:
            assert attribute in state.attributes, (entity_id, attribute)
        if isinstance(content := card.get("state_content"), str) and content != "state":
            assert content in state.attributes, (entity_id, content)
        checked += 1
    assert checked > 30
