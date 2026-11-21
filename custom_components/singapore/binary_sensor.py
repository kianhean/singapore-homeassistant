"""Binary sensor platform: holidays today and nearby environmental alerts."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SingaporeConfigEntry
from .const import SCHOOL_HOLIDAYS, enabled_school_events
from .dengue_coordinator import DengueClusterCoordinator, _haversine_km, nearest_cluster
from .entity import (
    DEVICE_FLOOD,
    DEVICE_HOLIDAY,
    DEVICE_NEA,
    DEVICE_SCHOOL,
    DEVICE_TRAFFIC_INCIDENTS,
    SingaporeEntity,
)
from .flood_coordinator import FloodAlertCoordinator
from .holiday_coordinator import PublicHolidayCoordinator
from .lightning_coordinator import (
    LightningCoordinator,
    nearest_strike,
    strike_distance_km,
)
from .school_coordinator import SchoolCalendarCoordinator
from .traffic_incidents_coordinator import TrafficIncidentsCoordinator
from .warning_coordinator import WeatherWarningsCoordinator

PARALLEL_UPDATES = 0

LIGHTNING_NEARBY_KM = 10.0
FLOOD_NEARBY_KM = 5.0
TRAFFIC_INCIDENT_NEARBY_KM = 5.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SingaporeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for enabled data sources."""
    data = entry.runtime_data
    eid = entry.entry_id
    entities: list[BinarySensorEntity] = []

    if (holiday := data.holiday) is not None:
        entities.append(SingaporePublicHolidayTodayBinarySensor(holiday, eid))
    if (school := data.school) is not None and SCHOOL_HOLIDAYS in enabled_school_events(
        entry
    ):
        entities.append(SingaporeSchoolHolidayTodayBinarySensor(school, eid))
    if (lightning := data.lightning) is not None:
        entities.append(SingaporeLightningNearbyBinarySensor(lightning, eid))
    if (flood := data.flood) is not None:
        entities.append(SingaporeFloodAlertNearbyBinarySensor(flood, eid))
    if (dengue := data.dengue) is not None:
        entities.append(SingaporeInDengueClusterBinarySensor(dengue, eid))
    if (warnings := data.warnings) is not None:
        entities.append(SingaporeHeavyRainWarningBinarySensor(warnings, eid))
    if (traffic_incidents := data.traffic_incidents) is not None:
        entities.append(
            SingaporeTrafficIncidentNearbyBinarySensor(traffic_incidents, eid)
        )

    async_add_entities(entities)


class SingaporePublicHolidayTodayBinarySensor(
    SingaporeEntity[PublicHolidayCoordinator], BinarySensorEntity
):
    """On when today is a Singapore public holiday."""

    _device = DEVICE_HOLIDAY
    _attr_translation_key = "public_holiday_today"
    _attr_attribution = "Data provided by the Ministry of Manpower"

    def __init__(self, coordinator: PublicHolidayCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "public_holiday_today")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        today = dt_util.now().date()
        return any(holiday.day == today for holiday in self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        today = dt_util.now().date()
        today_match = next(
            (h for h in self.coordinator.data if h.day == today),
            None,
        )
        upcoming = next(
            (h for h in self.coordinator.data if h.day > today),
            None,
        )
        return {
            "holiday": today_match.name if today_match else None,
            "next_holiday": upcoming.name if upcoming else None,
            "next_holiday_date": upcoming.day.isoformat() if upcoming else None,
            "source": "MOM",
        }


class SingaporeSchoolHolidayTodayBinarySensor(
    SingaporeEntity[SchoolCalendarCoordinator], BinarySensorEntity
):
    """On when today falls within an MOE school holiday."""

    _device = DEVICE_SCHOOL
    _attr_translation_key = "school_holiday_today"
    _attr_attribution = "Data provided by the Ministry of Education"

    def __init__(self, coordinator: SchoolCalendarCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "school_holiday_today")

    def _today_holiday(self):
        if self.coordinator.data is None:
            return None
        today = dt_util.now().date()
        return next(
            (
                ev
                for ev in self.coordinator.data
                if ev.kind == SCHOOL_HOLIDAYS and ev.start <= today <= ev.end
            ),
            None,
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self._today_holiday() is not None

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        match = self._today_holiday()
        return {
            "holiday": match.name if match else None,
            "start": match.start.isoformat() if match else None,
            "end": match.end.isoformat() if match else None,
            "source": "MOE",
        }


class SingaporeLightningNearbyBinarySensor(
    SingaporeEntity[LightningCoordinator], BinarySensorEntity
):
    """On when lightning was detected within 10 km of home in the last 30 min."""

    _device = DEVICE_NEA
    _attr_translation_key = "lightning_nearby"
    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_attribution = "Data provided by NEA via data.gov.sg"

    def __init__(self, coordinator: LightningCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "lightning_nearby")

    def _home(self) -> tuple[float, float]:
        return self.hass.config.latitude, self.hass.config.longitude

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        nearest = nearest_strike(*self._home(), self.coordinator.data)
        return nearest is not None and nearest[1] <= LIGHTNING_NEARBY_KM

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        lat, lon = self._home()
        data = self.coordinator.data
        nearest = nearest_strike(lat, lon, data)
        within = sum(
            1
            for s in data.strikes
            if strike_distance_km(lat, lon, s) <= LIGHTNING_NEARBY_KM
        )
        return {
            "radius_km": LIGHTNING_NEARBY_KM,
            "nearest_km": round(nearest[1], 2) if nearest is not None else None,
            "strikes_within_10_km": within,
            "source": "NEA via data.gov.sg",
        }


class SingaporeFloodAlertNearbyBinarySensor(
    SingaporeEntity[FloodAlertCoordinator], BinarySensorEntity
):
    """On when an active PUB flash-flood alert is within 5 km of home."""

    _device = DEVICE_FLOOD
    _attr_translation_key = "flood_alert_nearby"
    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_attribution = "Data provided by PUB via data.gov.sg"

    def __init__(self, coordinator: FloodAlertCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "flood_alert_nearby")

    def _home(self) -> tuple[float, float]:
        return self.hass.config.latitude, self.hass.config.longitude

    def _distances(self) -> list[tuple[float, str]]:
        data = self.coordinator.data
        if data is None:
            return []
        lat, lon = self._home()
        found: list[tuple[float, str]] = []
        for alert in data.alerts:
            if alert.latitude is None or alert.longitude is None:
                continue
            dist = _haversine_km(lat, lon, alert.latitude, alert.longitude)
            found.append((dist, alert.description or alert.area or alert.headline))
        found.sort(key=lambda item: item[0])
        return found

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        distances = self._distances()
        return bool(distances and distances[0][0] <= FLOOD_NEARBY_KM)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        distances = self._distances()
        nearby = [label for dist, label in distances if dist <= FLOOD_NEARBY_KM]
        return {
            "radius_km": FLOOD_NEARBY_KM,
            "nearest_km": round(distances[0][0], 2) if distances else None,
            "nearby_alerts": nearby,
            "source": "PUB via data.gov.sg",
        }


class SingaporeInDengueClusterBinarySensor(
    SingaporeEntity[DengueClusterCoordinator], BinarySensorEntity
):
    """On when the Home Assistant home location is inside an active dengue cluster."""

    _device = DEVICE_NEA
    _attr_translation_key = "in_dengue_cluster"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_attribution = "Data provided by NEA via data.gov.sg"

    def __init__(self, coordinator: DengueClusterCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "in_dengue_cluster")

    def _home(self) -> tuple[float, float]:
        return self.hass.config.latitude, self.hass.config.longitude

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        nearest = nearest_cluster(*self._home(), self.coordinator.data)
        return nearest is not None and nearest[1] == 0.0

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        nearest = nearest_cluster(*self._home(), self.coordinator.data)
        if nearest is None:
            return {"source": "NEA via data.gov.sg"}
        cluster, distance = nearest
        return {
            "nearest_km": round(distance, 2),
            "locality": cluster.locality,
            "cases": cluster.case_size,
            "source": "NEA via data.gov.sg",
        }


class SingaporeHeavyRainWarningBinarySensor(
    SingaporeEntity[WeatherWarningsCoordinator], BinarySensorEntity
):
    """On when NEA has an active Heavy Rain Warning."""

    _device = DEVICE_NEA
    _attr_translation_key = "heavy_rain_warning"
    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_attribution = "Data provided by NEA"

    def __init__(self, coordinator: WeatherWarningsCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "heavy_rain_warning")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        rain = self.coordinator.data.heavy_rain
        return rain is not None and rain.active

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        rain = self.coordinator.data.heavy_rain
        return {
            "warning": rain.text if rain else None,
            "issued": rain.issued.isoformat() if rain and rain.issued else None,
            "expires": rain.expires.isoformat() if rain and rain.expires else None,
            "source": "NEA",
        }


class SingaporeTrafficIncidentNearbyBinarySensor(
    SingaporeEntity[TrafficIncidentsCoordinator], BinarySensorEntity
):
    """On when an active LTA traffic incident is within 5 km of home."""

    _device = DEVICE_TRAFFIC_INCIDENTS
    _attr_translation_key = "traffic_incident_nearby"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_attribution = "Data provided by LTA via mytransport.sg"

    def __init__(self, coordinator: TrafficIncidentsCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "traffic_incident_nearby")

    def _home(self) -> tuple[float, float]:
        return self.hass.config.latitude, self.hass.config.longitude

    def _distances(self) -> list[tuple[float, str, str]]:
        data = self.coordinator.data
        if data is None:
            return []
        lat, lon = self._home()
        found: list[tuple[float, str, str]] = []
        for incident in data.incidents:
            if incident.latitude is None or incident.longitude is None:
                continue
            dist = _haversine_km(lat, lon, incident.latitude, incident.longitude)
            found.append((dist, incident.type, incident.message))
        found.sort(key=lambda item: item[0])
        return found

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        distances = self._distances()
        return bool(distances and distances[0][0] <= TRAFFIC_INCIDENT_NEARBY_KM)

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        distances = self._distances()
        nearby = [
            msg for dist, _itype, msg in distances if dist <= TRAFFIC_INCIDENT_NEARBY_KM
        ]
        return {
            "radius_km": TRAFFIC_INCIDENT_NEARBY_KM,
            "nearest_km": round(distances[0][0], 2) if distances else None,
            "nearest_type": distances[0][1] if distances else None,
            "nearby_incidents": nearby,
            "source": "LTA via mytransport.sg",
        }
