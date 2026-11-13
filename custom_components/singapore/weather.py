"""Weather platform for Singapore area forecasts."""

from __future__ import annotations

from homeassistant.components.weather import (
    Forecast,
    SingleCoordinatorWeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.const import UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SingaporeConfigEntry
from .const import DOMAIN
from .weather_coordinator import SingaporeWeatherCoordinator, _wind_direction_to_degrees

PARALLEL_UPDATES = 0

_CONDITION_MAP = {
    "fair": "sunny",
    "fair (day)": "sunny",
    "fair (night)": "clear-night",
    "partly cloudy": "partlycloudy",
    "partly cloudy (day)": "partlycloudy",
    "partly cloudy (night)": "partlycloudy",
    "cloudy": "cloudy",
    "hazy": "fog",
    "slightly hazy": "fog",
    "mist": "fog",
    "light rain": "rainy",
    "moderate rain": "rainy",
    "heavy rain": "pouring",
    "passing showers": "rainy",
    "showers": "rainy",
    "thundery showers": "lightning-rainy",
    "windy": "windy",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up weather entities for each forecast area."""
    coordinator = entry.runtime_data.weather

    if coordinator.data is None:
        async_add_entities([])
        return

    entities = [
        SingaporeAreaWeatherEntity(coordinator, entry.entry_id, area)
        for area in sorted(coordinator.data.areas)
    ]
    async_add_entities(entities)


class SingaporeAreaWeatherEntity(
    SingleCoordinatorWeatherEntity[SingaporeWeatherCoordinator]
):
    """One weather entity per Singapore forecast area.

    SingleCoordinatorWeatherEntity notifies forecast subscribers (e.g. the
    frontend weather card) after every coordinator refresh, so no manual
    async_update_listeners call is needed.
    """

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by NEA via data.gov.sg"
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR

    def __init__(
        self, coordinator: SingaporeWeatherCoordinator, entry_id: str, area: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._area = area
        slug = area.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry_id}_weather_{slug}"
        # Area names are dynamic Singapore place names, not translatable
        # strings, so this uses a raw name rather than a translation_key.
        self._attr_name = area

    @property
    def native_temperature(self) -> float | None:
        if self.coordinator.data is None:
            return None
        t = self.coordinator.data.readings.temperature
        if t is not None:
            return t
        fc = self.coordinator.data.four_day_forecast
        if fc:
            return fc[0].temp_high
        return None

    @property
    def condition(self) -> str | None:
        area = (
            self.coordinator.data.areas.get(self._area)
            if self.coordinator.data
            else None
        )
        if area is None:
            return None
        return _map_condition(area.condition_text)

    @property
    def extra_state_attributes(self) -> dict:
        area = (
            self.coordinator.data.areas.get(self._area)
            if self.coordinator.data
            else None
        )
        if area is None:
            return {}
        readings = self.coordinator.data.readings
        return {
            "forecast_area": self._area,
            "forecast_region": area.region,
            "source": "data.gov.sg / NEA",
            "raw_condition": area.condition_text,
            "valid_start": area.valid_start.isoformat(),
            "valid_end": area.valid_end.isoformat(),
            "temperature": readings.temperature,
            "humidity": readings.humidity,
            "wind_speed": readings.wind_speed,
            "wind_bearing": readings.wind_bearing,
            "precipitation": readings.precipitation,
        }

    @callback
    def _async_forecast_daily(self) -> list[Forecast] | None:
        """Return the 4-day outlook as daily forecasts in native units."""
        if self.coordinator.data is None:
            return None
        fc = self.coordinator.data.four_day_forecast
        if not fc:
            return None

        result: list[Forecast] = []
        for entry in fc:
            payload: dict = {
                "datetime": entry.date.isoformat(),
                "condition": _map_condition(entry.condition_text),
            }
            if entry.temp_high is not None:
                payload["native_temperature"] = entry.temp_high
            if entry.temp_low is not None:
                payload["native_templow"] = entry.temp_low
            if entry.wind_speed_high is not None and entry.wind_speed_low is not None:
                payload["native_wind_speed"] = round(
                    (entry.wind_speed_low + entry.wind_speed_high) / 2, 1
                )
            elif entry.wind_speed_high is not None:
                payload["native_wind_speed"] = entry.wind_speed_high
            bearing = _wind_direction_to_degrees(entry.wind_direction)
            if bearing is not None:
                payload["wind_bearing"] = bearing
            if entry.humidity_high is not None and entry.humidity_low is not None:
                payload["humidity"] = round(
                    (entry.humidity_low + entry.humidity_high) / 2, 1
                )
            elif entry.humidity_high is not None:
                payload["humidity"] = entry.humidity_high
            result.append(Forecast(**payload))
        return result

    @callback
    def _async_forecast_hourly(self) -> list[Forecast] | None:
        """Return the 24-hour forecast for this area's region.

        NEA forecasts the next 24 hours in a few periods (e.g. 6 pm-6 am,
        6 am-midday, midday-6 pm) per region, with no temperature, so each
        period becomes one entry at its start time carrying only a condition.
        Periods that have already ended are skipped.
        """
        data = self.coordinator.data
        if data is None or not data.regional_forecast:
            return None
        area = data.areas.get(self._area)
        if area is None or area.region is None:
            return None
        now = dt_util.utcnow()
        result: list[Forecast] = []
        for period in data.regional_forecast:
            text = period.regions.get(area.region)
            if text is None or period.end <= now:
                continue
            result.append(
                Forecast(
                    datetime=period.start.isoformat(),
                    condition=_map_condition(text),
                )
            )
        return result or None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_weather")},
            name="Weather",
            manufacturer="Singapore",
            model="NEA Weather",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.nea.gov.sg",
        )


def _map_condition(text: str) -> str:
    return _CONDITION_MAP.get(text.strip().lower(), "cloudy")
