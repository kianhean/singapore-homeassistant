"""Geo location entities: free taxis near home, for the Map card.

One entity per free taxi within ``NEARBY_KM`` of the home location (nearest
first, at most ``MAX_TAXI_MARKERS``), with ``source: singapore``, so a Map card
with ``geo_location_sources: [singapore]`` plots them.

Taxis carry no id in LTA's feed, so the entities are slots: "Free taxi 1" is
always the nearest taxi, "Free taxi 2" the next, and so on. Each poll moves
the slots to the new positions, adds slots when more taxis are near and
removes the surplus when fewer are. Like Home Assistant's other feed-based
geo location entities they have no unique id, so they never enter the entity
registry (and need no ``ENTITY_SOURCES`` entry for stale cleanup).
"""

from __future__ import annotations

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SingaporeConfigEntry
from .const import DOMAIN
from .taxi_coordinator import TaxiAvailabilityCoordinator, TaxiPosition

# The Map card's geo_location_sources value.
GEO_SOURCE = DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the taxi map markers when taxi availability is on."""
    if (taxi := entry.runtime_data.taxi) is None:
        return
    entry.async_on_unload(async_setup_taxi_markers(taxi, async_add_entities))


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
