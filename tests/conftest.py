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
    def __init__(self, *args, **translation_kwargs):
        # Real HA accepts translation_domain/_key/_placeholders kwargs.
        super().__init__(*args)
        self.translation_kwargs = translation_kwargs


class ConfigEntryNotReady(Exception):
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

    def __init__(
        self,
        hass,
        logger,
        *,
        config_entry=None,
        name,
        update_interval=None,
        always_update=True,
    ):
        self.hass = hass
        self.config_entry = config_entry
        self.name = name
        self.update_interval = update_interval
        self.always_update = always_update
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self._logger = logger

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
            self.last_exception = None
        except Exception as err:
            self._logger.warning("Update failed: %s", err)
            self.last_update_success = False
            self.last_exception = err

    async def async_config_entry_first_refresh(self):
        await self.async_refresh()
        if not self.last_update_success:
            raise ConfigEntryNotReady(f"{self.name} first refresh failed")

    async def _async_update_data(self):
        raise NotImplementedError

    def async_add_listener(self, update_callback):
        listeners = self.__dict__.setdefault("_listeners", [])
        listeners.append(update_callback)
        return lambda: listeners.remove(update_callback)

    def async_update_listeners(self):
        for update_callback in list(self.__dict__.get("_listeners", [])):
            update_callback()


class CoordinatorEntity:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success

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


class SensorDeviceClass(str):
    ENUM = "enum"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    WIND_DIRECTION = "wind_direction"
    WIND_SPEED = "wind_speed"
    PRECIPITATION = "precipitation"
    AQI = "aqi"
    PM25 = "pm25"
    PM10 = "pm10"
    OZONE = "ozone"
    NITROGEN_DIOXIDE = "nitrogen_dioxide"
    SULPHUR_DIOXIDE = "sulphur_dioxide"
    DISTANCE = "distance"


class SensorStateClass(str):
    MEASUREMENT = "measurement"
    MEASUREMENT_ANGLE = "measurement_angle"


class BinarySensorEntity:
    @property
    def device_class(self):
        return getattr(self, "_attr_device_class", None)


class BinarySensorDeviceClass(str):
    PROBLEM = "problem"
    SAFETY = "safety"


class Platform:
    BINARY_SENSOR = "binary_sensor"
    SENSOR = "sensor"
    WEATHER = "weather"
    CALENDAR = "calendar"
    CAMERA = "camera"
    EVENT = "event"
    GEO_LOCATION = "geo_location"


class HomeAssistant:
    pass


class ConfigEntry:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, entry_id: str = "test_entry"):
        self.entry_id = entry_id
        self.title = "Singapore"
        self.version = 1
        self.runtime_data = None
        self.subentries: dict = {}
        self.options: dict = {}
        self._on_unload: list = []

    def async_on_unload(self, func):
        self._on_unload.append(func)

    def add_update_listener(self, listener):
        self.update_listeners = [*getattr(self, "update_listeners", []), listener]
        return lambda: None

    def async_create_background_task(self, hass, coro, name):
        import asyncio

        return asyncio.ensure_future(coro)

    def async_create_task(self, hass, coro, name=None):
        import asyncio

        return asyncio.ensure_future(coro)


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


class ConfigSubentry:
    def __init__(
        self, *, data, subentry_type, title, unique_id=None, subentry_id="sub1"
    ):
        self.data = data
        self.subentry_type = subentry_type
        self.title = title
        self.unique_id = unique_id
        self.subentry_id = subentry_id


class ConfigSubentryFlow:
    pass


class OptionsFlow:
    def async_create_entry(self, *, data, title=""):
        return {"type": "create_entry", "data": data}

    def async_show_form(self, *, step_id, data_schema=None, errors=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors,
        }

    def add_suggested_values_to_schema(self, schema, suggested_values):
        return schema


class GeolocationEvent:
    """Mirrors HA's GeolocationEvent: the state is the distance, rounded."""

    hass = None

    @property
    def state(self):
        distance = self.distance
        return None if distance is None else round(distance, 1)

    @property
    def state_attributes(self):
        attrs = {"source": self.source}
        if self.latitude is not None:
            attrs["latitude"] = round(self.latitude, 5)
        if self.longitude is not None:
            attrs["longitude"] = round(self.longitude, 5)
        return attrs

    async def async_remove(self):
        self.removed = True


class EventEntity:
    """Mirrors HA's EventEntity: last event type + attributes, restored on add."""

    _attr_event_types: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def event_types(self):
        return self._attr_event_types

    def _trigger_event(self, event_type, event_attributes=None):
        if event_type not in self.event_types:
            raise ValueError(event_type)
        self._last_event_type = event_type
        self._last_event_attributes = event_attributes
        self.fired = [*getattr(self, "fired", []), (event_type, event_attributes)]

    @property
    def state_attributes(self):
        attrs = {"event_type": getattr(self, "_last_event_type", None)}
        attrs |= getattr(self, "_last_event_attributes", None) or {}
        return attrs

    async def async_added_to_hass(self):
        return None


class SensorEntityDescription:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class EntityCategory(str):
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


class UnitOfEnergy:
    KILO_WATT_HOUR = "kWh"


class UnitOfVolume:
    CUBIC_METERS = "m³"


class UnitOfPower:
    WATT = "W"


class IssueSeverity:
    ERROR = "error"
    WARNING = "warning"


class _Selector:
    def __init__(self, config=None):
        self.config = config

    def __call__(self, value):
        return value


class _SelectorConfig(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class _SelectorEnum:
    LIST = "list"
    DROPDOWN = "dropdown"
    EMAIL = "email"
    PASSWORD = "password"
    BOX = "box"


class AddConfigEntryEntitiesCallback:
    pass


class SingleCoordinatorWeatherEntity(CoordinatorEntity):
    """Mirrors HA: forecast listeners are notified on every coordinator update."""

    def _handle_coordinator_update(self):
        super()._handle_coordinator_update()
        self.coordinator.config_entry.async_create_task(
            self.hass, self.async_update_listeners(None)
        )

    async def async_update_listeners(self, forecast_types):
        return None

    async def async_forecast_daily(self):
        return self._async_forecast_daily()


class WeatherEntityFeature:
    FORECAST_HOURLY = 1
    FORECAST_DAILY = 2


class UnitOfTemperature:
    CELSIUS = "°C"


class UnitOfSpeed:
    KILOMETERS_PER_HOUR = "km/h"


class UnitOfLength:
    KILOMETERS = "km"


class UnitOfPrecipitationDepth:
    MILLIMETERS = "mm"


PERCENTAGE = "%"
DEGREE = "°"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
CONCENTRATION_MICROGRAMS_PER_CUBIC_METER = "µg/m³"
CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER = "mg/m³"


class Forecast(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Camera:
    def __init__(self):
        pass


class CalendarEntity:
    pass


class CalendarEvent(dict):
    """Dict access for older tests, attribute access like HA's dataclass."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.__dict__.update(kwargs)


class DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class DeviceEntryType:
    SERVICE = "service"


def _dt_now():
    from datetime import datetime

    return datetime.now()


def _dt_utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)


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
        CONF_USERNAME="username",
        CONF_PASSWORD="password",
        EntityCategory=EntityCategory,
        UnitOfEnergy=UnitOfEnergy,
        UnitOfVolume=UnitOfVolume,
        UnitOfPower=UnitOfPower,
        UnitOfTemperature=UnitOfTemperature,
        UnitOfSpeed=UnitOfSpeed,
        UnitOfPrecipitationDepth=UnitOfPrecipitationDepth,
        UnitOfLength=UnitOfLength,
        PERCENTAGE=PERCENTAGE,
        UV_INDEX="UV index",
        DEGREE=DEGREE,
        ATTR_LATITUDE=ATTR_LATITUDE,
        ATTR_LONGITUDE=ATTR_LONGITUDE,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER=CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER,
    ),
    "homeassistant.exceptions": _mod(
        "homeassistant.exceptions", ConfigEntryNotReady=ConfigEntryNotReady
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
        AddConfigEntryEntitiesCallback=AddConfigEntryEntitiesCallback,
    ),
    "homeassistant.helpers.event": _mod(
        "homeassistant.helpers.event",
        async_track_time_change=MagicMock(return_value=MagicMock()),
        async_track_time_interval=MagicMock(return_value=MagicMock()),
    ),
    "homeassistant.helpers.issue_registry": _mod(
        "homeassistant.helpers.issue_registry",
        async_create_issue=MagicMock(),
        async_delete_issue=MagicMock(),
        IssueSeverity=IssueSeverity,
    ),
    "homeassistant.helpers.selector": _mod(
        "homeassistant.helpers.selector",
        TextSelector=_Selector,
        TextSelectorConfig=_SelectorConfig,
        TextSelectorType=_SelectorEnum,
        NumberSelector=_Selector,
        NumberSelectorConfig=_SelectorConfig,
        NumberSelectorMode=_SelectorEnum,
        SelectSelector=_Selector,
        SelectSelectorConfig=_SelectorConfig,
        SelectSelectorMode=_SelectorEnum,
    ),
    "homeassistant.helpers.device_registry": _mod(
        "homeassistant.helpers.device_registry",
        DeviceInfo=DeviceInfo,
        DeviceEntryType=DeviceEntryType,
        DeviceEntry=MagicMock,
        async_get=MagicMock(),
        async_entries_for_config_entry=MagicMock(return_value=[]),
    ),
    "homeassistant.helpers.entity_registry": _mod(
        "homeassistant.helpers.entity_registry",
        async_get=MagicMock(),
        async_entries_for_config_entry=MagicMock(return_value=[]),
    ),
    "homeassistant.util": _mod("homeassistant.util"),
    "homeassistant.util.dt": _mod(
        "homeassistant.util.dt",
        now=_dt_now,
        utcnow=_dt_utcnow,
    ),
    "homeassistant.components": _mod("homeassistant.components"),
    "homeassistant.components.weather": _mod(
        "homeassistant.components.weather",
        SingleCoordinatorWeatherEntity=SingleCoordinatorWeatherEntity,
        WeatherEntityFeature=WeatherEntityFeature,
        Forecast=Forecast,
    ),
    "homeassistant.components.calendar": _mod(
        "homeassistant.components.calendar",
        CalendarEntity=CalendarEntity,
        CalendarEvent=CalendarEvent,
    ),
    "homeassistant.components.geo_location": _mod(
        "homeassistant.components.geo_location", GeolocationEvent=GeolocationEvent
    ),
    "homeassistant.components.event": _mod(
        "homeassistant.components.event", EventEntity=EventEntity
    ),
    "homeassistant.components.camera": _mod(
        "homeassistant.components.camera", Camera=Camera
    ),
    "homeassistant.components.binary_sensor": _mod(
        "homeassistant.components.binary_sensor",
        BinarySensorEntity=BinarySensorEntity,
        BinarySensorDeviceClass=BinarySensorDeviceClass,
    ),
    "homeassistant.components.sensor": _mod(
        "homeassistant.components.sensor",
        SensorEntity=SensorEntity,
        SensorDeviceClass=SensorDeviceClass,
        SensorStateClass=SensorStateClass,
        SensorEntityDescription=SensorEntityDescription,
    ),
    "homeassistant.config_entries": _mod(
        "homeassistant.config_entries",
        ConfigEntry=ConfigEntry,
        ConfigFlow=ConfigFlow,
        ConfigFlowResult=dict,
        ConfigSubentry=ConfigSubentry,
        ConfigSubentryFlow=ConfigSubentryFlow,
        OptionsFlow=OptionsFlow,
        SubentryFlowResult=dict,
        SOURCE_USER="user",
        SOURCE_RECONFIGURE="reconfigure",
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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def datagov_time(monkeypatch):
    """A fake clock for the shared data.gov.sg limiter: sleeping advances it.

    Returns a dict whose "sleeps" list records every wait, so tests can check
    the limiter's pacing and 429 back-off without real delays.
    """
    from custom_components.singapore import datagov

    state = {"now": 1000.0, "sleeps": []}

    async def _sleep(seconds):
        state["sleeps"].append(round(seconds, 3))
        state["now"] += seconds

    monkeypatch.setattr(datagov, "_sleep", _sleep)
    monkeypatch.setattr(datagov, "_clock", lambda: state["now"])
    return state


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as asyncio")
