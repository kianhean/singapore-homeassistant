"""
Mock Home Assistant modules so tests run without installing homeassistant.

The integration imports several HA symbols. We inject lightweight fakes into
sys.modules before any integration code is imported, so pytest can collect
and run tests on pure Python 3.11+ without the full HA wheel.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Minimal HA fakes
# ---------------------------------------------------------------------------


class UpdateFailed(Exception):
    pass


class DataUpdateCoordinator:
    """Minimal coordinator that drives _async_update_data."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.data = None
        self.last_update_success = True
        self._logger = logger

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except Exception as err:
            self._logger.warning("Update failed: %s", err)
            self.last_update_success = False

    async def _async_update_data(self):
        raise NotImplementedError


class CoordinatorEntity:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return self._attr_name

    @property
    def native_unit_of_measurement(self):
        return self._attr_native_unit_of_measurement


class SensorEntity:
    @property
    def device_class(self):
        return getattr(self, "_attr_device_class", None)


class SensorDeviceClass:
    pass


class SensorStateClass:
    MEASUREMENT = "measurement"


class Platform:
    SENSOR = "sensor"


class HomeAssistant:
    pass


class ConfigEntry:
    pass


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


class AddEntitiesCallback:
    pass


# ---------------------------------------------------------------------------
# Build fake module tree and inject into sys.modules
# ---------------------------------------------------------------------------


def _mod(name: str, **attrs) -> ModuleType:
    m = ModuleType(name)
    m.__dict__.update(attrs)
    return m


_HA_MODULES: dict[str, ModuleType] = {
    "homeassistant": _mod("homeassistant"),
    "homeassistant.core": _mod(
        "homeassistant.core", HomeAssistant=HomeAssistant, callback=lambda f: f
    ),
    "homeassistant.const": _mod(
        "homeassistant.const", Platform=Platform, CONF_NAME="name"
    ),
    "homeassistant.helpers": _mod("homeassistant.helpers"),
    "homeassistant.helpers.update_coordinator": _mod(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=DataUpdateCoordinator,
        CoordinatorEntity=CoordinatorEntity,
        UpdateFailed=UpdateFailed,
    ),
    "homeassistant.helpers.aiohttp_client": _mod(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=MagicMock(),
    ),
    "homeassistant.helpers.entity_platform": _mod(
        "homeassistant.helpers.entity_platform",
        AddEntitiesCallback=AddEntitiesCallback,
    ),
    "homeassistant.components": _mod("homeassistant.components"),
    "homeassistant.components.sensor": _mod(
        "homeassistant.components.sensor",
        SensorEntity=SensorEntity,
        SensorDeviceClass=SensorDeviceClass,
        SensorStateClass=SensorStateClass,
    ),
    "homeassistant.config_entries": _mod(
        "homeassistant.config_entries",
        ConfigEntry=ConfigEntry,
        ConfigFlow=ConfigFlow,
        ConfigFlowResult=dict,
        SOURCE_USER="user",
    ),
}

for _name, _mod_obj in _HA_MODULES.items():
    sys.modules.setdefault(_name, _mod_obj)

# voluptuous is a real package we can install
try:
    import voluptuous  # noqa: F401
except ImportError:
    sys.modules.setdefault(
        "voluptuous", _mod("voluptuous", Schema=dict, Required=lambda k, **kw: k)
    )
