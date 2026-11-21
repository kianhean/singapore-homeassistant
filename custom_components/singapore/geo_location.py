"""Geo location entities: free taxis near home and live LTA traffic incidents, for the Map card.

One entity per free taxi within ``NEARBY_KM`` of the home location (nearest
first, at most ``MAX_TAXI_MARKERS``), with ``source: singapore``, so a Map card
with ``geo_location_sources: [singapore]`` plots them.

One entity per active LTA traffic incident with coordinates (nearest first, at
most ``MAX_TRAFFIC_INCIDENT_MARKERS``), with ``source: singapore_traffic``, so
a Map card with ``geo_location_sources: [singapore_traffic]`` plots them.

Neither feed carries stable IDs, so the entities are slots ("Free taxi 1",
"Traffic incident 1", ...). Each poll moves the slots to the new positions,
adds slots when more appear and removes the surplus when fewer are active.
Like Home Assistant's other feed-based geo location entities they have no
unique id, so they never enter the entity registry (and need no
``ENTITY_SOURCES`` entry for stale cleanup).
"""

from __future__ import annotations

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SingaporeConfigEntry
from .const import DOMAIN
from .dengue_coordinator import _haversine_km
from .taxi_coordinator import TaxiAvailabilityCoordinator, TaxiPosition
from .traffic_incidents_coordinator import TrafficIncident, TrafficIncidentsCoordinator

# The Map card's geo_location_sources values.
GEO_SOURCE = DOMAIN
TRAFFIC_GEO_SOURCE = f"{DOMAIN}_traffic"
MAX_TRAFFIC_INCIDENT_MARKERS = 50

_INCIDENT_ICONS: dict[str, str] = {
    "accident": "mdi:car-emergency",
    "vehicle breakdown": "mdi:car-wrench",
    "heavy traffic": "mdi:traffic-cone",
    "roadwork": "mdi:road-variant",
    "road block": "mdi:road-variant",
    "obstacle": "mdi:alert-octagon-outline",
    "unattended vehicle": "mdi:car-off",
    "weather": "mdi:weather-pouring",
    "diversion": "mdi:directions-fork",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up taxi and traffic incident map markers when their sources are on."""
    if (taxi := entry.runtime_data.taxi) is not None:
        entry.async_on_unload(async_setup_taxi_markers(taxi, async_add_entities))
    if (incidents := entry.runtime_data.traffic_incidents) is not None:
        entry.async_on_unload(
            async_setup_traffic_incident_markers(hass, incidents, async_add_entities)
        )


@callback
def async_setup_taxi_markers(
    coordinator: TaxiAvailabilityCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
):
    """Keep one marker per nearby taxi; return the unsub."""
    markers: list[SingaporeTaxiMarker] = []

    @callback
    def _sync() -> None:
        data = coordinator.data
        wanted = 0 if data is None else len(data.nearby_taxis)
        if wanted > len(markers):
            new = [
                SingaporeTaxiMarker(coordinator, slot)
                for slot in range(len(markers), wanted)
            ]
            markers.extend(new)
            async_add_entities(new)
        # A marker not added to Home Assistant yet is kept (it shows no
        # position) and removed on a later poll instead.
        while len(markers) > wanted and markers[-1].hass is not None:
            marker = markers.pop()
            marker.hass.async_create_task(marker.async_remove())

    _sync()
    return coordinator.async_add_listener(_sync)


class SingaporeTaxiMarker(
    CoordinatorEntity[TaxiAvailabilityCoordinator], GeolocationEvent
):
    """The ``slot``-th nearest free taxi (0 = nearest)."""

    _attr_has_entity_name = True
    _attr_translation_key = "taxi"
    _attr_attribution = "Data provided by LTA via data.gov.sg"
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator: TaxiAvailabilityCoordinator, slot: int) -> None:
        super().__init__(coordinator)
        self._slot = slot
        self._attr_translation_placeholders = {"number": str(slot + 1)}

    def _taxi(self) -> TaxiPosition | None:
        data = self.coordinator.data
        if data is None or self._slot >= len(data.nearby_taxis):
            return None
        return data.nearby_taxis[self._slot]

    @property
    def source(self) -> str:
        return GEO_SOURCE

    @property
    def distance(self) -> float | None:
        taxi = self._taxi()
        return None if taxi is None else taxi.distance_km

    @property
    def latitude(self) -> float | None:
        taxi = self._taxi()
        return None if taxi is None else taxi.latitude

    @property
    def longitude(self) -> float | None:
        taxi = self._taxi()
        return None if taxi is None else taxi.longitude


def _located_incidents(
    hass: HomeAssistant, coordinator: TrafficIncidentsCoordinator
) -> list[tuple[TrafficIncident, float | None]]:
    """Active incidents with coordinates, sorted nearest to home first."""
    data = coordinator.data
    if data is None:
        return []
    home_lat = getattr(hass.config, "latitude", None)
    home_lon = getattr(hass.config, "longitude", None)
    has_home = isinstance(home_lat, (int, float)) and isinstance(home_lon, (int, float))
    items: list[tuple[TrafficIncident, float | None]] = []
    for inc in data.incidents:
        if inc.latitude is None or inc.longitude is None:
            continue
        dist = (
            round(_haversine_km(home_lat, home_lon, inc.latitude, inc.longitude), 2)
            if has_home
            else None
        )
        items.append((inc, dist))
    if has_home:
        items.sort(key=lambda pair: pair[1] if pair[1] is not None else float("inf"))
    return items[:MAX_TRAFFIC_INCIDENT_MARKERS]


@callback
def async_setup_traffic_incident_markers(
    hass: HomeAssistant,
    coordinator: TrafficIncidentsCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
):
    """Keep one map marker per active LTA traffic incident; return the unsub."""
    markers: list[SingaporeTrafficIncidentMarker] = []

    @callback
    def _sync() -> None:
        wanted = len(_located_incidents(hass, coordinator))
        if wanted > len(markers):
            new = [
                SingaporeTrafficIncidentMarker(hass, coordinator, slot)
                for slot in range(len(markers), wanted)
            ]
            markers.extend(new)
            async_add_entities(new)
        while len(markers) > wanted and markers[-1].hass is not None:
            marker = markers.pop()
            marker.hass.async_create_task(marker.async_remove())

    _sync()
    return coordinator.async_add_listener(_sync)


class SingaporeTrafficIncidentMarker(
    CoordinatorEntity[TrafficIncidentsCoordinator], GeolocationEvent
):
    """The ``slot``-th nearest active LTA traffic incident (0 = nearest)."""

    _attr_has_entity_name = True
    _attr_translation_key = "traffic_incident"
    _attr_attribution = "Data provided by LTA via mytransport.sg"
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TrafficIncidentsCoordinator,
        slot: int,
    ) -> None:
        super().__init__(coordinator)
        self._hass_ref = hass
        self._slot = slot
        self._attr_translation_placeholders = {"number": str(slot + 1)}

    def _entry(self) -> tuple[TrafficIncident, float | None] | None:
        items = _located_incidents(self._hass_ref, self.coordinator)
        if self._slot >= len(items):
            return None
        return items[self._slot]

    @property
    def source(self) -> str:
        return TRAFFIC_GEO_SOURCE

    @property
    def icon(self) -> str | None:
        entry = self._entry()
        if entry is None:
            return "mdi:car-emergency"
        return _INCIDENT_ICONS.get(entry[0].type.lower(), "mdi:alert-circle-outline")

    @property
    def distance(self) -> float | None:
        entry = self._entry()
        return None if entry is None else entry[1]

    @property
    def latitude(self) -> float | None:
        entry = self._entry()
        return None if entry is None else entry[0].latitude

    @property
    def longitude(self) -> float | None:
        entry = self._entry()
        return None if entry is None else entry[0].longitude

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        entry = self._entry()
        if entry is None:
            return {}
        return {"type": entry[0].type, "message": entry[0].message}
