"""Weather platform for Singapore area forecasts."""

from __future__ import annotations

from datetime import timedelta, timezone

from homeassistant.components.weather import (
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .weather_coordinator import SingaporeWeatherCoordinator

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
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self, coordinator: SingaporeWeatherCoordinator, entry_id: str, area: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._area = area
        slug = area.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry_id}_weather_{slug}"
        self._attr_name = f"Singapore Weather {area}"

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
                payload["temperature"] = readings.temperature
            if readings.humidity is not None:
                payload["humidity"] = readings.humidity
            if readings.wind_speed is not None:
                payload["wind_speed"] = readings.wind_speed
            if readings.wind_bearing is not None:
                payload["wind_bearing"] = readings.wind_bearing
            if readings.precipitation is not None:
                payload["precipitation"] = readings.precipitation
            return Forecast(**payload)

        return [
            _point(start_utc),
            _point(start_utc + timedelta(hours=1)),
        ]

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
