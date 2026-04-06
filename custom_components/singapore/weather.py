"""Weather platform for Singapore area forecasts."""

from __future__ import annotations

from datetime import timedelta, timezone

from homeassistant.components.weather import (
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .weather_coordinator import SingaporeWeatherCoordinator, _wind_direction_to_degrees

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
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up weather entities for each forecast area."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SingaporeWeatherCoordinator = entry_data["weather"]

    if coordinator.data is None:
        async_add_entities([])
        return

    entities = [
        SingaporeAreaWeatherEntity(coordinator, entry.entry_id, area)
        for area in sorted(coordinator.data.areas)
    ]
    async_add_entities(entities)


class SingaporeAreaWeatherEntity(
    CoordinatorEntity[SingaporeWeatherCoordinator], WeatherEntity
):
    """One weather entity per Singapore forecast area."""

    _attr_has_entity_name = False
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_HOURLY | WeatherEntityFeature.FORECAST_DAILY
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
        self._attr_name = f"Singapore Weather {area}"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()
        self.hass.async_create_task(self.async_update_listeners())

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

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        area = (
            self.coordinator.data.areas.get(self._area)
            if self.coordinator.data
            else None
        )
        if area is None:
            return None

        # Approximate 2-hour interval forecast to two hourly points.
        start_utc = area.valid_start.astimezone(timezone.utc)
        condition = _map_condition(area.condition_text)

        readings = self.coordinator.data.readings

        def _point(dt):
            payload = {
                "datetime": dt.isoformat(),
                "condition": condition,
            }
            if readings.temperature is not None:
                payload["native_temperature"] = readings.temperature
            if readings.humidity is not None:
                payload["humidity"] = readings.humidity
            if readings.wind_speed is not None:
                payload["native_wind_speed"] = readings.wind_speed
            if readings.wind_bearing is not None:
                payload["wind_bearing"] = readings.wind_bearing
            if readings.precipitation is not None:
                payload["precipitation"] = readings.precipitation
            return Forecast(**payload)

        return [
            _point(start_utc),
            _point(start_utc + timedelta(hours=1)),
        ]

    async def async_forecast_daily(self) -> list[Forecast] | None:
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

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_weather")},
            "name": "Singapore Weather",
            "manufacturer": "Singapore",
            "model": "NEA Weather",
        }


def _map_condition(text: str) -> str:
    return _CONDITION_MAP.get(text.strip().lower(), "cloudy")
