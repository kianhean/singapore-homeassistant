"""Event entities: a new COE bidding result, and MRT/LRT service alerts.

An event entity's state is the time of its last event, so automations can
trigger on "it happened" without comparing sensor states themselves.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SingaporeConfigEntry
from .coe_coordinator import CoeCoordinator, CoeData
from .entity import DEVICE_COE, DEVICE_TRAIN, SingaporeEntity
from .train_coordinator import TrainStatusCoordinator

# Coordinator-driven: entities never poll on their own.
PARALLEL_UPDATES = 0

EVENT_NEW_RESULTS = "new_results"
EVENT_DISRUPTION = "disruption"
EVENT_PLANNED = "planned"
EVENT_RESOLVED = "resolved"

ATTR_EXERCISE = "exercise"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event entities for the enabled sources."""
    data = entry.runtime_data
    entities: list[EventEntity] = []
    if data.coe is not None:
        entities.append(SingaporeCoeResultsEvent(data.coe, entry.entry_id))
    if data.train is not None:
        entities.append(SingaporeTrainServiceAlertEvent(data.train, entry.entry_id))
    async_add_entities(entities)


def coe_exercise(data: CoeData) -> str:
    """A bidding exercise's label, e.g. "2026-03 #1"."""
    return f"{data.month} #{data.bidding_no}"


class SingaporeCoeResultsEvent(SingaporeEntity[CoeCoordinator], EventEntity):
    """Fires once per bidding exercise, when LTA publishes its results.

    The last exercise seen is restored from the entity's last event across
    restarts, so results published while Home Assistant was down still fire
    when they are first fetched. A brand-new install only records the current
    exercise and fires from the next one.
    """

    _device = DEVICE_COE
    _attr_translation_key = "coe_results"
    _attr_attribution = "Data provided by LTA via data.gov.sg"
    _attr_event_types = [EVENT_NEW_RESULTS]

    def __init__(self, coordinator: CoeCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "coe_results_event")
        self._last_exercise: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # EventEntity restored the last event before this runs.
        self._last_exercise = (self.state_attributes or {}).get(ATTR_EXERCISE)
        if self.coordinator.data is not None:
            self._process(self.coordinator.data, write=False)

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is not None:
            self._process(self.coordinator.data, write=True)

    def _process(self, data: CoeData, *, write: bool) -> None:
        exercise = coe_exercise(data)
        if exercise == self._last_exercise:
            return
        first_seen = self._last_exercise is None
        self._last_exercise = exercise
        if first_seen:
            return
        self._trigger_event(EVENT_NEW_RESULTS, coe_event_attributes(data))
        if write:
            self.async_write_ha_state()


def coe_event_attributes(data: CoeData) -> dict[str, Any]:
    """The event's attributes: the exercise and every category's premium."""
    return {
        ATTR_EXERCISE: coe_exercise(data),
        "month": data.month,
        "bidding_no": data.bidding_no,
        **{
            f"category_{cat.lower()}": premium
            for cat, premium in sorted(data.premiums.items())
        },
    }


def train_transitions(
    previous: dict[str, str], current: dict[str, str]
) -> list[tuple[str, str]]:
    """(event type, line) for each line whose status changed in a way worth an event.

    A line going into disruption fires ``disruption``; a planned notice on a
    normal line fires ``planned``; a disrupted line recovering fires
    ``resolved``. A planned notice ending is not an event (they lapse daily).
    """
    events: list[tuple[str, str]] = []
    for line, status in current.items():
        before = previous.get(line)
        if before is None or before == status:
            continue
        if status == "disruption":
            events.append((EVENT_DISRUPTION, line))
        elif before == "disruption":
            events.append((EVENT_RESOLVED, line))
        elif status == "planned" and before == "normal":
            events.append((EVENT_PLANNED, line))
    return events


class SingaporeTrainServiceAlertEvent(
    SingaporeEntity[TrainStatusCoordinator], EventEntity
):
    """Fires when an MRT/LRT line is disrupted, has a planned notice, or recovers.

    Compares each poll with the previous one; the first poll after startup
    only sets the baseline (a line already disrupted then has its sensor at
    ``disruption`` but fires no event).
    """

    _device = DEVICE_TRAIN
    _attr_translation_key = "train_service_alert"
    _attr_attribution = "Data provided by LTA via mytransport.sg"
    _attr_event_types = [EVENT_DISRUPTION, EVENT_PLANNED, EVENT_RESOLVED]

    def __init__(self, coordinator: TrainStatusCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "train_service_alert")
        self._previous: dict[str, str] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.data is not None:
            self._previous = dict(self.coordinator.data.line_statuses)

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if data is None:
            return
        current = dict(data.line_statuses)
        previous, self._previous = self._previous, current
        if previous is None:
            return
        for event_type, line in train_transitions(previous, current):
            self._trigger_event(
                event_type,
                {"line": line, "status": current[line], "details": data.details},
            )
            self.async_write_ha_state()
