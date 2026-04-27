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


class ConfigEntryAuthFailed(Exception):
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

    async def async_config_entry_first_refresh(self):
        await self.async_refresh()

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
    ENERGY = "energy"
    WATER = "water"


class SensorStateClass:
    MEASUREMENT = "measurement"


class Platform:
    SENSOR = "sensor"
    WEATHER = "weather"
    CALENDAR = "calendar"


class HomeAssistant:
    pass


class ConfigEntry:
    pass


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


class OptionsFlow:
    pass


class AddEntitiesCallback:
    pass


class WeatherEntity:
    pass


class WeatherEntityFeature:
    FORECAST_HOURLY = 1
    FORECAST_DAILY = 2


class UnitOfTemperature:
    CELSIUS = "°C"


class UnitOfSpeed:
    KILOMETERS_PER_HOUR = "km/h"


class UnitOfEnergy:
    KILO_WATT_HOUR = "kWh"


class UnitOfVolume:
    CUBIC_METERS = "m³"


class Forecast(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class CalendarEntity:
    pass


class CalendarEvent(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


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
        UnitOfTemperature=UnitOfTemperature,
        UnitOfSpeed=UnitOfSpeed,
        UnitOfEnergy=UnitOfEnergy,
        UnitOfVolume=UnitOfVolume,
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
        ConfigFlowResult=dict,
        OptionsFlow=OptionsFlow,
        SOURCE_USER="user",
    ),
    "homeassistant.exceptions": _mod(
        "homeassistant.exceptions",
        ConfigEntryAuthFailed=ConfigEntryAuthFailed,
    ),
    "homeassistant.components.recorder": _mod(
        "homeassistant.components.recorder",
        get_instance=MagicMock(return_value=MagicMock()),
    ),
    "homeassistant.components.recorder.models": _mod(
        "homeassistant.components.recorder.models",
        StatisticData=MagicMock(side_effect=lambda **kw: kw),
        StatisticMetaData=MagicMock(side_effect=lambda **kw: kw),
    ),
    "homeassistant.components.recorder.statistics": _mod(
        "homeassistant.components.recorder.statistics",
        async_add_external_statistics=MagicMock(),
    ),
}

for _name, _mod_obj in _HA_MODULES.items():
    sys.modules.setdefault(_name, _mod_obj)


class _NiquestsResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def json(self):
        return {}


class _NiquestsAsyncSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return _NiquestsResponse()


class _NiquestsSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, *args, **kwargs):
        return _NiquestsResponse()


sys.modules.setdefault(
    "niquests",
    _mod(
        "niquests",
        AsyncSession=_NiquestsAsyncSession,
        Session=_NiquestsSession,
        Response=_NiquestsResponse,
    ),
)


# sp_services mock — provides the public surface used by sp_services_coordinator
class _SpServicesError(Exception):
    pass


class _AuthenticationError(_SpServicesError):
    pass


class _SessionExpiredError(_AuthenticationError):
    pass


class _ApiError(_SpServicesError):
    pass


from dataclasses import dataclass  # noqa: E402
from datetime import datetime  # noqa: E402


@dataclass
class _LoginChallenge:
    oauth_state: str = ""
    login_state: str = ""
    code_verifier: str = ""
    csrf: str = ""
    phone_number: str | None = None
    transaction_id: str | None = None


@dataclass
class _UsagePoint:
    period: str = ""
    value: float = 0.0
    status: str | None = None


@dataclass
class _UsageData:
    electricity_today_kwh: float | None = None
    electricity_month_kwh: float | None = None
    water_today_m3: float | None = None
    water_month_m3: float | None = None
    account_no: str | None = None
    last_updated: datetime = None  # type: ignore[assignment]
    electricity_last_month_kwh: float | None = None
    water_last_month_m3: float | None = None
    electricity_monthly_history: list | None = None
    water_monthly_history: list | None = None
    electricity_daily_history: list | None = None
    electricity_hourly_history: list | None = None


class _SpServicesClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def login(self, username, password):
        return _LoginChallenge()

    async def verify_otp(self, otp, login_challenge=None):
        return "mock_token"

    async def fetch_usage(self, token):
        return _UsageData(last_updated=datetime.now())

    async def close(self):
        pass


sys.modules.setdefault(
    "sp_services",
    _mod(
        "sp_services",
        SpServicesClient=_SpServicesClient,
        LoginChallenge=_LoginChallenge,
        UsageData=_UsageData,
        UsagePoint=_UsagePoint,
        SpServicesError=_SpServicesError,
        AuthenticationError=_AuthenticationError,
        SessionExpiredError=_SessionExpiredError,
        ApiError=_ApiError,
    ),
)

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
        _mod(
            "voluptuous",
            Schema=_Schema,
            Required=lambda k, **kw: k,
            Optional=lambda k, **kw: k,
        ),
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


def pytest_pyfunc_call(pyfuncitem):
    import asyncio
    import inspect

    if inspect.iscoroutinefunction(pyfuncitem.obj):
        kwargs = {
            name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames
        }
        asyncio.run(pyfuncitem.obj(**kwargs))
        return True
    return None
