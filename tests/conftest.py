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


class ConfigEntryNotReady(Exception):
    pass


class ConfigEntryAuthFailed(Exception):
    pass


class DataUpdateCoordinator:
    """Minimal coordinator that drives _async_update_data.

    Mirrors real HA semantics closely enough for tests: async_refresh()
    never raises (it just flips last_update_success), while
    async_config_entry_first_refresh() raises ConfigEntryNotReady when the
    first refresh fails, matching homeassistant.helpers.update_coordinator.
    """

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, name, update_interval, config_entry=None):
        self.hass = hass
        self.name = name
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self.config_entry = config_entry
        self._logger = logger

    async def async_refresh(self):
        # Real HA delegates through _async_refresh(log_failures=...). Mirror
        # that call shape: a subclass that shadows _async_refresh with its own
        # signature then fails here, instead of only in production.
        await self._async_refresh(log_failures=True)

    async def _async_refresh(
        self,
        log_failures: bool = True,
        raise_on_auth_failed: bool = False,
        scheduled: bool = False,
        raise_on_entry_error: bool = False,
    ):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except Exception as err:
            if log_failures:
                self._logger.warning("Update failed: %s", err)
            self.last_exception = err
            self.last_update_success = False

    async def async_config_entry_first_refresh(self):
        await self.async_refresh()
        if not self.last_update_success:
            raise ConfigEntryNotReady(f"{self.name} first refresh failed")

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
        return getattr(self, "_attr_name", None)

    @property
    def native_unit_of_measurement(self):
        return self._attr_native_unit_of_measurement

    def _handle_coordinator_update(self):
        self.async_write_ha_state()

    def async_write_ha_state(self):
        pass


class SensorEntity:
    @property
    def device_class(self):
        return getattr(self, "_attr_device_class", None)


class SensorDeviceClass:
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    WIND_SPEED = "wind_speed"
    PRECIPITATION = "precipitation"
    ENERGY = "energy"
    WATER = "water"


class SensorStateClass:
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class Platform:
    SENSOR = "sensor"
    WEATHER = "weather"
    CALENDAR = "calendar"


class HomeAssistant:
    pass


class ConfigEntry:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, entry_id: str = "test_entry", data: dict | None = None):
        self.entry_id = entry_id
        self.data = data or {}
        self.options: dict = {}
        self.runtime_data = None
        self._on_unload: list = []

    def async_on_unload(self, func):
        self._on_unload.append(func)

    def async_create_background_task(self, hass, coro, name):
        import asyncio

        return asyncio.ensure_future(coro)


class _FlowBase:
    """Shared form/menu/entry helpers used by both flow types."""

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}


class ConfigFlow(_FlowBase):
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None


class OptionsFlow(_FlowBase):
    pass


class AddEntitiesCallback:
    pass


class WeatherEntity:
    async def async_update_listeners(self, forecast_types):
        """Real HA requires forecast_types; enforce the same signature."""
        return None


class WeatherEntityFeature:
    FORECAST_HOURLY = 1
    FORECAST_DAILY = 2


class UnitOfTemperature:
    CELSIUS = "°C"


class UnitOfSpeed:
    KILOMETERS_PER_HOUR = "km/h"


class UnitOfPrecipitationDepth:
    MILLIMETERS = "mm"


class UnitOfEnergy:
    KILO_WATT_HOUR = "kWh"


class UnitOfVolume:
    CUBIC_METERS = "m³"


PERCENTAGE = "%"
DEGREE = "°"


class Forecast(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class CalendarEntity:
    pass


class CalendarEvent(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class DeviceEntryType:
    SERVICE = "service"


def _dt_now():
    from datetime import datetime

    return datetime.now()


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
        "homeassistant.const",
        Platform=Platform,
        CONF_NAME="name",
        UnitOfTemperature=UnitOfTemperature,
        UnitOfSpeed=UnitOfSpeed,
        UnitOfPrecipitationDepth=UnitOfPrecipitationDepth,
        UnitOfEnergy=UnitOfEnergy,
        UnitOfVolume=UnitOfVolume,
        PERCENTAGE=PERCENTAGE,
        DEGREE=DEGREE,
    ),
    "homeassistant.exceptions": _mod(
        "homeassistant.exceptions",
        ConfigEntryNotReady=ConfigEntryNotReady,
        ConfigEntryAuthFailed=ConfigEntryAuthFailed,
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
    "homeassistant.helpers.event": _mod(
        "homeassistant.helpers.event",
        async_track_time_change=MagicMock(return_value=MagicMock()),
    ),
    "homeassistant.helpers.device_registry": _mod(
        "homeassistant.helpers.device_registry",
        DeviceInfo=DeviceInfo,
        DeviceEntryType=DeviceEntryType,
    ),
    "homeassistant.util": _mod("homeassistant.util"),
    "homeassistant.util.dt": _mod("homeassistant.util.dt", now=_dt_now),
    "homeassistant.components": _mod("homeassistant.components"),
    "homeassistant.components.weather": _mod(
        "homeassistant.components.weather",
        WeatherEntity=WeatherEntity,
        WeatherEntityFeature=WeatherEntityFeature,
        Forecast=Forecast,
    ),
    "homeassistant.components.calendar": _mod(
        "homeassistant.components.calendar",
        CalendarEntity=CalendarEntity,
        CalendarEvent=CalendarEvent,
    ),
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
        OptionsFlow=OptionsFlow,
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

    class _Schema:
        def __init__(self, schema):
            self.schema = schema

        def __call__(self, value):
            return value

    sys.modules.setdefault(
        "voluptuous",
        _mod("voluptuous", Schema=_Schema, Required=lambda k, **kw: k),
    )


# bs4 fallback for environments without beautifulsoup4 installed
try:
    import bs4  # noqa: F401
except ImportError:
    import re

    class _FakeScript:
        def __init__(self, text: str | None):
            self.string = text

    class BeautifulSoup:  # minimal subset used by tests
        def __init__(self, html: str, _parser: str):
            self._html = html

        def get_text(self, sep: str = " ", strip: bool = False) -> str:
            text = re.sub(r"<[^>]+>", " ", self._html)
            text = re.sub(r"\s+", " ", text)
            return text.strip() if strip else text

        def find_all(self, tag: str):
            if tag.lower() != "script":
                return []
            out = []
            for m in re.finditer(
                r"<script[^>]*>(.*?)</script>", self._html, re.I | re.S
            ):
                out.append(_FakeScript(m.group(1)))
            return out

    sys.modules.setdefault("bs4", _mod("bs4", BeautifulSoup=BeautifulSoup))


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as asyncio")
