"""Sensors for SP Group e-accounts (usage, bills, meters, optional EV/GreenUP/FCU).

Adapted from maci0/home-assistant-sp (MIT, Copyright (c) 2026 Marcel W.
Wysocki): https://github.com/maci0/home-assistant-sp. The set of sensors and
their values come from the vendored ``sp_account.mapper``; this module turns
each ``SensorSpec`` into an entity attached to the account's config subentry.
Translation keys carry an ``sp_`` prefix so they cannot clash with this
integration's other sensor keys (``sp_electricity`` vs the tariff sensors).
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sp_account.const import ATTRIBUTION
from .sp_account.mapper import SensorSpec
from .sp_account_coordinator import SpAccountCoordinator

TRANSLATION_PREFIX = "sp_"

_UNITS = {
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "m³": UnitOfVolume.CUBIC_METERS,
    "°C": UnitOfTemperature.CELSIUS,
}


@callback
def async_setup_sp_account_sensors(
    entry_id: str,
    coordinators: dict[str, SpAccountCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> list:
    """Add each account's sensors as its data arrives; return the unsubscribers.

    Which sensors exist depends on what the account has (gas, EV, FCU...), so
    entities are created from the specs after every poll, and a key that shows
    up later is added without a reload.
    """
    unsubs = []
    for subentry_id, coordinator in coordinators.items():
        known: set[str] = set()

        @callback
        def _add_new(
            coordinator: SpAccountCoordinator = coordinator,
            subentry_id: str = subentry_id,
            known: set[str] = known,
        ) -> None:
            if coordinator.data is None:
                return
            fresh = [s for s in coordinator.sensor_specs if s.key not in known]
            if not fresh:
                return
            known.update(spec.key for spec in fresh)
            async_add_entities(
                [SpAccountSensor(coordinator, entry_id, spec) for spec in fresh],
                config_subentry_id=subentry_id,
            )

        _add_new()
        unsubs.append(coordinator.async_add_listener(_add_new))
    return unsubs


class SpAccountSensor(CoordinatorEntity[SpAccountCoordinator], SensorEntity):
    """One SP e-account reading, described by a mapper ``SensorSpec``."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self, coordinator: SpAccountCoordinator, entry_id: str, spec: SensorSpec
    ) -> None:
        super().__init__(coordinator)
        self._key = spec.key
        self._spec: SensorSpec | None = None
        usage = coordinator.data
        premise_id = usage.premise_id
        self._attr_unique_id = f"{entry_id}_sp_{premise_id}_{spec.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_sp_{premise_id}")},
            name=usage.premise.address or "SP account",
            manufacturer="SP Group",
            model=usage.premise.premise_type or "e-account",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.spgroup.com.sg",
        )
        self._apply_spec(spec)

    def _apply_spec(self, spec: SensorSpec) -> None:
        self._spec = spec
        self._attr_translation_key = f"{TRANSLATION_PREFIX}{spec.translation_key}"
        if spec.name:
            self._attr_name = spec.name
        elif hasattr(self, "_attr_name"):
            # _attr_name outranks the translation key; drop a stale one.
            del self._attr_name
        unit = spec.unit_of_measurement
        self.entity_description = SensorEntityDescription(
            key=spec.key,
            device_class=(
                SensorDeviceClass(spec.device_class) if spec.device_class else None
            ),
            state_class=(
                SensorStateClass(spec.state_class) if spec.state_class else None
            ),
            native_unit_of_measurement=_UNITS.get(unit, unit) if unit else None,
            entity_category=(
                EntityCategory(spec.entity_category) if spec.entity_category else None
            ),
            suggested_display_precision=spec.suggested_display_precision,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        spec = self.coordinator.spec_for(self._key)
        if spec is None:
            self._spec = None
        else:
            self._apply_spec(spec)
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return super().available and self._spec is not None

    @property
    def native_value(self) -> float | str | None:
        return self._spec.native_value if self._spec else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self.coordinator.extra_attributes(self._key)
