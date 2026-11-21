"""Shared entity base: the integration's devices and which source owns what.

Every public data source's entities sit on one of the service devices in
``DEVICES``. ``ENTITY_SOURCES`` maps each entity's translation key to the data
source it comes from, so ``__init__.py`` can remove the entities and devices
of a source that was turned off in the options (``async_remove_stale``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    DOMAIN,
    SCHOOL_EVENT_KINDS,
    SOURCE_CAPITALAND,
    SOURCE_CARPARK,
    SOURCE_COE,
    SOURCE_DENGUE,
    SOURCE_EV_CHARGERS,
    SOURCE_FLOOD,
    SOURCE_HAWKER,
    SOURCE_HOLIDAY,
    SOURCE_LIGHTNING,
    SOURCE_PSI,
    SOURCE_RADAR,
    SOURCE_SCHOOL,
    SOURCE_TARIFF,
    SOURCE_TAXI,
    SOURCE_TIDES,
    SOURCE_TRAFFIC,
    SOURCE_TRAFFIC_INCIDENTS,
    SOURCE_TRAIN,
    SOURCE_TRAIN_CROWD,
    SOURCE_UV,
    SOURCE_WARNINGS,
    SOURCE_WBGT,
    SOURCE_WEATHER,
)

_CoordinatorT = TypeVar("_CoordinatorT", bound=DataUpdateCoordinator)

DEVICE_ENERGY = "energy"
DEVICE_COE = "coe"
DEVICE_WEATHER = "weather"
DEVICE_HOLIDAY = "holiday"
DEVICE_SCHOOL = "school"
DEVICE_TRAIN = "train"
DEVICE_AIR_QUALITY = "air_quality"
DEVICE_FLOOD = "flood"
DEVICE_TRAFFIC = "traffic_cameras"
DEVICE_NEA = "nea"
DEVICE_HAWKER = "hawker"
DEVICE_TRAFFIC_INCIDENTS = "traffic_incidents"
DEVICE_CARPARK = "carparks"
DEVICE_TAXI = "taxis"
DEVICE_CAPITALAND = "capitaland_carparks"
DEVICE_EV_CHARGERS = "ev_chargers"


@dataclass(frozen=True, slots=True)
class SingaporeDevice:
    """A service device; its identifier is ``{entry_id}_{key}``."""

    name: str
    model: str
    # The data sources whose entities sit on this device. The device is
    # removed once every one of them is turned off.
    sources: tuple[str, ...]
    configuration_url: str | None = None


DEVICES: dict[str, SingaporeDevice] = {
    DEVICE_ENERGY: SingaporeDevice(
        "Energy",
        "SP Group Tariffs",
        (SOURCE_TARIFF,),
        "https://www.spgroup.com.sg/our-services/utilities/tariff-information",
    ),
    DEVICE_COE: SingaporeDevice(
        "COE",
        "LTA COE Results",
        (SOURCE_COE,),
        "https://data.gov.sg/datasets/d_69b3380ad7e51aff3a7dcc84eba52b8a/view",
    ),
    DEVICE_WEATHER: SingaporeDevice(
        "Weather",
        "NEA Weather",
        (SOURCE_WEATHER, SOURCE_RADAR),
        "https://www.nea.gov.sg",
    ),
    DEVICE_HOLIDAY: SingaporeDevice(
        "Public Holidays",
        "MOM Public Holidays",
        (SOURCE_HOLIDAY,),
        "https://www.mom.gov.sg/employment-practices/public-holidays",
    ),
    DEVICE_SCHOOL: SingaporeDevice(
        "School",
        "MOE School Calendar",
        (SOURCE_SCHOOL,),
        "https://www.moe.gov.sg/calendar",
    ),
    DEVICE_TRAIN: SingaporeDevice(
        "MRT/LRT",
        "MyTransport Train Status",
        (SOURCE_TRAIN, SOURCE_TRAIN_CROWD),
        "https://www.mytransport.sg/trainstatus#",
    ),
    DEVICE_AIR_QUALITY: SingaporeDevice(
        "Air Quality",
        "NEA PSI",
        (SOURCE_PSI,),
        "https://data.gov.sg/datasets/d_fe37906a0182569d891506e815e819b7/view",
    ),
    DEVICE_FLOOD: SingaporeDevice(
        "PUB",
        "PUB Flood Alerts",
        (SOURCE_FLOOD,),
        "https://data.gov.sg/datasets/d_f1404e08587ce555b9ea3f565e2eb9a3/view",
    ),
    DEVICE_TRAFFIC: SingaporeDevice(
        "Traffic Cameras",
        "LTA Traffic Images",
        (SOURCE_TRAFFIC,),
        "https://data.gov.sg/datasets/d_6cdb6b405b25aaaacbaf7689bcc6fae0/view",
    ),
    DEVICE_NEA: SingaporeDevice(
        "NEA",
        "NEA via data.gov.sg",
        (
            SOURCE_DENGUE,
            SOURCE_LIGHTNING,
            SOURCE_UV,
            SOURCE_WBGT,
            SOURCE_WARNINGS,
            SOURCE_TIDES,
        ),
        "https://www.nea.gov.sg",
    ),
    DEVICE_HAWKER: SingaporeDevice(
        "Hawker Centres",
        "NEA Hawker Centres",
        (SOURCE_HAWKER,),
        "https://data.gov.sg/datasets/d_bda4baa634dd1cc7a6c7cad5f19e2d68/view",
    ),
    DEVICE_TRAFFIC_INCIDENTS: SingaporeDevice(
        "Traffic",
        "LTA Traffic Incidents",
        (SOURCE_TRAFFIC_INCIDENTS,),
        "https://www.mytransport.sg",
    ),
    DEVICE_CARPARK: SingaporeDevice(
        "HDB Carparks",
        "HDB Carpark Availability",
        (SOURCE_CARPARK,),
        "https://data.gov.sg/datasets/d_ca933a644e55d34fe21f28b8052fac63/view",
    ),
    DEVICE_TAXI: SingaporeDevice(
        "Taxis",
        "LTA Taxi Availability",
        (SOURCE_TAXI,),
        "https://data.gov.sg/datasets/d_e25662f1a062dd046453926aa284ba64/view",
    ),
    DEVICE_CAPITALAND: SingaporeDevice(
        "CapitaLand Carparks",
        "CapitaLand JustPark Lot Availability",
        (SOURCE_CAPITALAND,),
        "https://justpark.capitaland.com/Lot-Availability",
    ),
    DEVICE_EV_CHARGERS: SingaporeDevice(
        "EV Chargers",
        "LTA DataMall EV Charging Points",
        (SOURCE_EV_CHARGERS,),
        "https://datamall.lta.gov.sg",
    ),
}

# Translation key -> data source, for every public-source entity. The weather
# entities (one per forecast area) are named after the area instead of a
# translation key; they are matched by platform in ``entity_source``.
# tests/test_entity.py checks every entity the platforms create is listed.
ENTITY_SOURCES: dict[str, str] = {
    **dict.fromkeys(
        ("electricity_tariff", "solar_export_price", "gas_tariff", "water_tariff"),
        SOURCE_TARIFF,
    ),
    **dict.fromkeys(("coe_category", "coe_results"), SOURCE_COE),
    **dict.fromkeys(
        ("temperature", "humidity", "wind_speed", "wind_bearing", "rainfall"),
        SOURCE_WEATHER,
    ),
    **dict.fromkeys(("public_holidays", "public_holiday_today"), SOURCE_HOLIDAY),
    **{f"school_{kind}": SOURCE_SCHOOL for kind in SCHOOL_EVENT_KINDS},
    "school_holiday_today": SOURCE_SCHOOL,
    **dict.fromkeys(
        ("train_status", "train_line_status", "train_service_alert"), SOURCE_TRAIN
    ),
    **dict.fromkeys(
        (
            "psi",
            "psi_region",
            "pm25",
            "pm10",
            "ozone",
            "nitrogen_dioxide",
            "sulphur_dioxide",
            "carbon_monoxide",
            "pm25_one_hour",
        ),
        SOURCE_PSI,
    ),
    **dict.fromkeys(("flood_alerts", "flood_alert_nearby"), SOURCE_FLOOD),
    "checkpoint_camera": SOURCE_TRAFFIC,
    **dict.fromkeys(
        ("dengue_clusters", "nearest_dengue_cluster", "in_dengue_cluster"),
        SOURCE_DENGUE,
    ),
    **dict.fromkeys(
        ("lightning_strikes", "nearest_lightning", "lightning_nearby"), SOURCE_LIGHTNING
    ),
    **dict.fromkeys(("uv_index", "uv_exposure"), SOURCE_UV),
    **dict.fromkeys(("rain_radar", "rain_radar_animation"), SOURCE_RADAR),
    **dict.fromkeys(("wbgt", "heat_stress"), SOURCE_WBGT),
    **dict.fromkeys(
        ("weather_advisories", "latest_earthquake", "heavy_rain_warning"),
        SOURCE_WARNINGS,
    ),
    **dict.fromkeys(("next_tide", "next_tide_level"), SOURCE_TIDES),
    "hawker_closures": SOURCE_HAWKER,
    **dict.fromkeys(
        (
            "traffic_incidents",
            "nearest_traffic_incident",
            "traffic_incident_nearby",
        ),
        SOURCE_TRAFFIC_INCIDENTS,
    ),
    "carpark": SOURCE_CARPARK,
    **dict.fromkeys(("taxis_available", "taxis_nearby"), SOURCE_TAXI),
    "capitaland_carpark": SOURCE_CAPITALAND,
    "train_crowd": SOURCE_TRAIN_CROWD,
    **dict.fromkeys(
        ("ev_chargers_available", "nearest_ev_charger"),
        SOURCE_EV_CHARGERS,
    ),
}


def school_translation_key(kind: str) -> str:
    """The translation key of a MOE school calendar (one per event kind)."""
    return f"school_{kind}"


def entity_source(platform: str, translation_key: str | None) -> str | None:
    """The data source an entity belongs to (None: not a public source's)."""
    if platform == "weather":
        return SOURCE_WEATHER
    if translation_key is None:
        return None
    return ENTITY_SOURCES.get(translation_key)


def device_identifier(entry_id: str, device: str) -> tuple[str, str]:
    """A service device's registry identifier."""
    return (DOMAIN, f"{entry_id}_{device}")


def device_info(entry_id: str, device: str) -> DeviceInfo:
    """DeviceInfo for one of the service devices in ``DEVICES``."""
    spec = DEVICES[device]
    info = DeviceInfo(
        identifiers={device_identifier(entry_id, device)},
        name=spec.name,
        manufacturer="Singapore",
        model=spec.model,
        entry_type=DeviceEntryType.SERVICE,
    )
    if spec.configuration_url is not None:
        info["configuration_url"] = spec.configuration_url
    return info


class SingaporeEntity(CoordinatorEntity[_CoordinatorT]):
    """A public data source's entity: named by translation key, on its device.

    Subclasses set ``_device`` (a ``DEVICES`` key) and pass the unique-id
    suffix (``{entry_id}_{suffix}``) here, or set ``_attr_unique_id`` later.
    """

    _attr_has_entity_name = True
    _device: str

    def __init__(
        self,
        coordinator: _CoordinatorT,
        entry_id: str,
        unique_id_suffix: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        if unique_id_suffix is not None:
            self._attr_unique_id = f"{entry_id}_{unique_id_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return device_info(self._entry_id, self._device)
