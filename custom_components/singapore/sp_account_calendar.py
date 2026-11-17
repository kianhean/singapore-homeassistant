"""Calendar of an SP e-account's dates: bill due dates and meter-reading windows.

One calendar per linked account, on the account's device, added once the
account's first poll has loaded (like its sensors). Events come straight from
the poll: every bill in the history becomes an all-day event on its due date,
and the self-submitted meter reading window a timed event.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .sp_account.const import ATTRIBUTION
from .sp_account.models import SG_TZ, UsageReadings
from .sp_account_coordinator import SpAccountCoordinator
from .sp_account_insights import parse_instant


def sp_account_events(usage: UsageReadings | None) -> list[CalendarEvent]:
    """Bill due dates and the meter reading window, oldest first."""
    if usage is None:
        return []
    events: list[CalendarEvent] = []
    for bill in usage.bills:
        due = parse_instant(bill.due_date)
        if due is None:
            continue
        day = due.astimezone(SG_TZ).date()
        issued = parse_instant(bill.date)
        description = f"Bill amount S${bill.amount_sgd:,.2f}"
        if issued is not None:
            description += f", issued {issued.astimezone(SG_TZ).date().isoformat()}"
        events.append(
            CalendarEvent(
                summary=f"SP bill due (S${bill.amount_sgd:,.2f})",
                start=day,
                end=day + timedelta(days=1),
                description=description,
            )
        )
    reading = usage.meter_reading
    if reading is not None:
        opens, closes = parse_instant(reading.start), parse_instant(reading.end)
        if opens is not None and closes is not None and closes > opens:
            title = f" ({reading.title})" if reading.title else ""
            events.append(
                CalendarEvent(
                    summary=f"Submit SP meter reading{title}",
                    start=opens.astimezone(SG_TZ),
                    end=closes.astimezone(SG_TZ),
                    description=reading.message or "",
                )
            )
    events.sort(key=lambda event: _instant(event.start))
    return events


def _instant(value: object) -> datetime:
    """An event bound as an aware datetime (all-day dates start at SGT midnight)."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, datetime.min.time(), SG_TZ)  # type: ignore[arg-type]


@callback
def async_setup_sp_account_calendars(
    entry_id: str,
    coordinators: dict[str, SpAccountCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> list:
    """Add each account's calendar once its data arrives; return the unsubscribers."""
    unsubs = []
    for subentry_id, coordinator in coordinators.items():
        added: list[bool] = []

        @callback
        def _add(
            coordinator: SpAccountCoordinator = coordinator,
            subentry_id: str = subentry_id,
            added: list[bool] = added,
        ) -> None:
            if added or coordinator.data is None:
                return
            added.append(True)
            async_add_entities(
                [SpAccountCalendar(coordinator, entry_id)],
                config_subentry_id=subentry_id,
            )

        _add()
        unsubs.append(coordinator.async_add_listener(_add))
    return unsubs


class SpAccountCalendar(CoordinatorEntity[SpAccountCoordinator], CalendarEntity):
    """Bill due dates and meter-reading windows for one SP e-account."""

    _attr_has_entity_name = True
    _attr_translation_key = "sp_account_dates"
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: SpAccountCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        usage = coordinator.data
        premise_id = usage.premise_id
        self._attr_unique_id = f"{entry_id}_sp_{premise_id}_calendar"
        # Same identifiers as the account's sensors, so it joins their device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_sp_{premise_id}")},
            name=usage.premise.address or "SP account",
            manufacturer="SP Group",
            model=usage.premise.premise_type or "e-account",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.spgroup.com.sg",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """The event in progress, else the next one."""
        now = dt_util.now()
        for event in sp_account_events(self.coordinator.data):
            end = _instant(event.end)
            if end > now:
                return event
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Events overlapping the requested range."""
        return [
            event
            for event in sp_account_events(self.coordinator.data)
            if _instant(event.start) < end_date and _instant(event.end) > start_date
        ]
