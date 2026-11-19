"""Shared entity base, source/device mapping, and stale device cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.singapore import (
    SingaporeData,
    async_remove_config_entry_device,
    async_remove_stale,
)
from custom_components.singapore.calendar import (
    async_setup_entry as calendar_setup_entry,
)
from custom_components.singapore.camera import async_setup_entry as camera_setup_entry
from custom_components.singapore.capitaland_coordinator import (
    CapitaLandData,
    CapitaLandSite,
)
from custom_components.singapore.const import (
    CONF_DISABLED_SOURCES,
    DOMAIN,
    SOURCES,
    enabled_sources,
)
from custom_components.singapore.entity import (
    DEVICES,
    ENTITY_SOURCES,
    device_identifier,
    device_info,
    entity_source,
)
from custom_components.singapore.event import async_setup_entry as event_setup_entry
from custom_components.singapore.sensor import async_setup_entry as sensor_setup_entry
from custom_components.singapore.weather import (
    async_setup_entry as weather_setup_entry,
)

_PLATFORMS = {
    "sensor": sensor_setup_entry,
    "weather": weather_setup_entry,
    "calendar": calendar_setup_entry,
    "camera": camera_setup_entry,
    "event": event_setup_entry,
}


def _entry(disabled: list[str] | None = None) -> ConfigEntry:
    entry = ConfigEntry("entry1")
    entry.options = {CONF_DISABLED_SOURCES: disabled} if disabled else {}
    entry.subentries = {}
    return entry


def _runtime_data(enabled: set[str]) -> SingaporeData:
    """One distinct mock coordinator per enabled source."""
    coordinators = {}
    for source in SOURCES:
        if source not in enabled:
            coordinators[source] = None
            continue
        coordinator = MagicMock(name=source)
        coordinator.source = source
        if source == "weather":
            coordinator.data.areas = {"Bedok": "Fair", "Changi": "Cloudy"}
        if source == "carpark":
            coordinator.data.tracked = ["BJ55"]
        if source == "capitaland":
            coordinator.data = CapitaLandData(
                {"BM": CapitaLandSite("BM", "Bedok Mall", "Retail", 39, 264, False)}
            )
        coordinators[source] = coordinator
    return SingaporeData(**coordinators, sp_accounts={}, options={})


async def _all_entities(entry: ConfigEntry) -> list[tuple[str, object]]:
    """Every (platform, entity) the platforms create for this entry."""
    created: list[tuple[str, object]] = []
    for platform, setup in _PLATFORMS.items():

        def _add(entities, *_args, _platform=platform, **_kwargs):
            created.extend((_platform, entity) for entity in entities)

        await setup(MagicMock(), entry, _add)
    return created


async def test_every_entity_maps_to_its_coordinators_source():
    entry = _entry()
    entry.runtime_data = _runtime_data(set(SOURCES))
    entities = await _all_entities(entry)
    assert entities
    seen_sources = set()
    for platform, entity in entities:
        source = entity.coordinator.source
        key = getattr(entity, "_attr_translation_key", None)
        assert entity_source(platform, key) == source, (platform, key)
        # The entity sits on a device that lists its source, so turning the
        # source off can remove the device once its other sources are off too.
        assert source in DEVICES[entity._device].sources
        assert entity.device_info == device_info("entry1", entity._device)
        assert entity.unique_id.startswith("entry1_")
        seen_sources.add(source)
    assert seen_sources == set(SOURCES)


async def test_every_listed_translation_key_is_used():
    entry = _entry()
    entry.runtime_data = _runtime_data(set(SOURCES))
    used = {
        getattr(entity, "_attr_translation_key", None)
        for _platform, entity in await _all_entities(entry)
    }
    assert set(ENTITY_SOURCES) <= used


def test_every_source_has_a_device():
    owned = [source for device in DEVICES.values() for source in device.sources]
    assert sorted(owned) == sorted(SOURCES)


def test_device_info_shape():
    info = device_info("entry1", "nea")
    assert info["identifiers"] == {("singapore", "entry1_nea")}
    assert info["name"] == "NEA"
    assert info["manufacturer"] == "Singapore"
    assert info["configuration_url"] == "https://www.nea.gov.sg"


def test_entity_source_unknown_keys():
    assert entity_source("sensor", None) is None
    assert entity_source("sensor", "sp_electricity") is None
    assert entity_source("weather", None) == "weather"


# -- stale cleanup -----------------------------------------------------------


def _reg_entry(
    entity_id: str, translation_key: str | None, subentry=None, unique_id=None
):
    return SimpleNamespace(
        entity_id=entity_id,
        domain=entity_id.split(".")[0],
        translation_key=translation_key,
        config_subentry_id=subentry,
        unique_id=unique_id,
    )


def _run_cleanup(enabled, entries, devices, options=None):
    entity_registry = MagicMock()
    device_registry = MagicMock()
    entry = _entry()
    if options:
        entry.options = options
    with (
        patch("custom_components.singapore.er.async_get", return_value=entity_registry),
        patch(
            "custom_components.singapore.er.async_entries_for_config_entry",
            return_value=entries,
        ),
        patch("custom_components.singapore.dr.async_get", return_value=device_registry),
        patch(
            "custom_components.singapore.dr.async_entries_for_config_entry",
            return_value=devices,
        ),
    ):
        async_remove_stale(MagicMock(), entry, enabled)
    removed = [c.args[0] for c in entity_registry.async_remove.call_args_list]
    removed_devices = [
        c.args[0] for c in device_registry.async_update_device.call_args_list
    ]
    for call in device_registry.async_update_device.call_args_list:
        assert call.kwargs == {"remove_config_entry_id": "entry1"}
    return removed, removed_devices


def _device(key: str):
    return SimpleNamespace(
        id=f"dev_{key}", identifiers={device_identifier("entry1", key)}
    )


def test_cleanup_removes_turned_off_sources_only():
    entries = [
        _reg_entry("sensor.air_quality_psi", "psi"),
        _reg_entry("sensor.nea_uv_index", "uv_index"),
        _reg_entry("sensor.nea_dengue_clusters", "dengue_clusters"),
        _reg_entry("camera.weather_rain_radar_70_km", "rain_radar"),
        _reg_entry("weather.weather_bedok", None),
        _reg_entry("sensor.weather_temperature", "temperature"),
        # SP account entities belong to a subentry: never touched here.
        _reg_entry("sensor.home_electricity", "sp_electricity", subentry="sub1"),
        # Unknown keys (a future entity, a user-made one) are left alone.
        _reg_entry("sensor.something_else", "mystery"),
    ]
    devices = [_device(key) for key in DEVICES]
    enabled = set(SOURCES) - {"psi", "uv", "radar"}

    removed, removed_devices = _run_cleanup(enabled, entries, devices)

    assert removed == [
        "sensor.air_quality_psi",
        "sensor.nea_uv_index",
        "camera.weather_rain_radar_70_km",
    ]
    # Air Quality has no source left; NEA (dengue) and Weather (weather) stay.
    assert removed_devices == ["dev_air_quality"]


def test_cleanup_removes_shared_device_when_all_its_sources_are_off():
    devices = [_device("nea"), _device("weather")]
    enabled = set(SOURCES) - {"dengue", "lightning", "uv", "wbgt", "weather", "radar"}
    _removed, removed_devices = _run_cleanup(enabled, [], devices)
    assert sorted(removed_devices) == ["dev_nea", "dev_weather"]


def test_cleanup_removes_carparks_dropped_from_the_options():
    entries = [
        _reg_entry(
            "sensor.hdb_carparks_bj55", "carpark", unique_id="entry1_carpark_bj55"
        ),
        _reg_entry(
            "sensor.hdb_carparks_acb", "carpark", unique_id="entry1_carpark_acb"
        ),
    ]
    removed, _ = _run_cleanup(set(SOURCES), entries, [], {"carparks": "BJ55"})
    assert removed == ["sensor.hdb_carparks_acb"]
    # "Nearest home" mode (no list): the set isn't known yet, nothing goes.
    removed, _ = _run_cleanup(set(SOURCES), entries, [])
    assert removed == []


def test_cleanup_with_everything_on_removes_nothing():
    entries = [_reg_entry("sensor.air_quality_psi", "psi")]
    removed, removed_devices = _run_cleanup(
        set(SOURCES), entries, [_device(k) for k in DEVICES]
    )
    assert removed == []
    assert removed_devices == []


def test_cleanup_leaves_other_devices_alone():
    # An SP premise device on the entry is never one of the service devices.
    sp_premise = SimpleNamespace(id="dev_sp", identifiers={(DOMAIN, "entry1_sp_P1")})
    removed, removed_devices = _run_cleanup(set(), [], [sp_premise])
    assert removed == []
    assert removed_devices == []


# -- manual device removal ---------------------------------------------------


@pytest.mark.parametrize(
    ("identifier", "disabled", "allowed"),
    [
        ("entry1_air_quality", [], False),
        ("entry1_air_quality", ["psi"], True),
        # NEA stays while any of dengue/lightning/UV/WBGT is on.
        ("entry1_nea", ["uv", "lightning", "dengue"], False),
        ("entry1_nea", ["uv", "lightning", "dengue", "wbgt"], True),
        ("entry1_sp_P1", [], False),
        ("entry1_sp_GONE", [], True),
        ("entry1_something_old", [], True),
    ],
)
async def test_remove_config_entry_device(identifier, disabled, allowed):
    entry = _entry(disabled)
    entry.runtime_data = _runtime_data(enabled_sources(entry))
    account = MagicMock()
    account.data.premise_id = "P1"
    no_data = MagicMock()
    no_data.data = None
    entry.runtime_data.sp_accounts = {"sub1": account, "sub2": no_data}
    device = SimpleNamespace(identifiers={(DOMAIN, identifier)})
    assert await async_remove_config_entry_device(MagicMock(), entry, device) is allowed
