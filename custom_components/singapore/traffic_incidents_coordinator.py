"""Data coordinator for LTA traffic incidents (via mytransport.sg)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_TRAFFIC_INCIDENTS, scaled_interval
from .dengue_coordinator import _haversine_km
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

# Same AEM servlet on mytransport.sg that proxies LTA DataMall TrainServiceAlerts,
# given its own query parameter so test mocks can register it separately.
TRAFFIC_INCIDENTS_URL = (
    "https://www.mytransport.sg"
    "/content/ltagov/en/map/train/jcr:content/left-menu/ltaDatamallAPI"
    ".ltaDatamallAPI.POST.html?service=TrafficIncidents"
)
_TRAFFIC_INCIDENTS_REFERER = "https://www.mytransport.sg/trainstatus#"

UPDATE_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class TrafficIncident:
    """A single active LTA traffic incident."""

    type: str
    message: str
    latitude: float | None = None
    longitude: float | None = None

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "message": self.message,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass
class TrafficIncidentsData:
    """Active LTA traffic incidents across Singapore."""

    incidents: list[TrafficIncident] = field(default_factory=list)

    @property
    def counts_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for incident in self.incidents:
            counts[incident.type] = counts.get(incident.type, 0) + 1
        return counts

    def nearest(self, lat: float, lon: float) -> tuple[TrafficIncident, float] | None:
        """Return (incident, distance_km) for the incident closest to the point."""
        best: tuple[TrafficIncident, float] | None = None
        for incident in self.incidents:
            if incident.latitude is None or incident.longitude is None:
                continue
            dist = _haversine_km(lat, lon, incident.latitude, incident.longitude)
            if best is None or dist < best[1]:
                best = (incident, dist)
        return best


def _parse_traffic_incidents(payload: dict) -> TrafficIncidentsData:
    """Parse the LTA DataMall TrafficIncidents JSON payload.

    Expected shape::

        {
          "odata.metadata": "https://datamall2.mytransport.sg/ltaodataservice/TrafficIncidents",
          "value": [
            {
              "Type": "Accident",
              "Latitude": 1.38248,
              "Longitude": 103.91454,
              "Message": "(21/11)10:09 Accident on PIE ..."
            }
          ]
        }

    Raises ``UpdateFailed`` if ``value`` is not a list.
    """
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, list):
        raise source_error(
            ERROR_PAYLOAD, SOURCE_TRAFFIC_INCIDENTS, detail="no value list"
        )

    incidents: list[TrafficIncident] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        incident_type = str(item.get("Type") or "").strip() or "Miscellaneous"
        message = str(item.get("Message") or "").strip()
        if not message:
            continue
        lat = lon = None
        try:
            raw_lat = item.get("Latitude")
            raw_lon = item.get("Longitude")
            if raw_lat is not None and raw_lon is not None:
                lat = round(float(raw_lat), 6)
                lon = round(float(raw_lon), 6)
        except (TypeError, ValueError):
            lat = lon = None
        incidents.append(
            TrafficIncident(
                type=incident_type,
                message=message,
                latitude=lat,
                longitude=lon,
            )
        )
    return TrafficIncidentsData(incidents=incidents)


class TrafficIncidentsCoordinator(DataUpdateCoordinator[TrafficIncidentsData]):
    """Polls LTA traffic incidents from mytransport.sg."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore LTA Traffic Incidents",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )

    async def _async_update_data(self) -> TrafficIncidentsData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                TRAFFIC_INCIDENTS_URL,
                data={"serviceName": "TrafficIncidents", "param": ""},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": _TRAFFIC_INCIDENTS_REFERER,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise source_error(
                        ERROR_HTTP,
                        SOURCE_TRAFFIC_INCIDENTS,
                        status=response.status,
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching traffic incidents (%s); using last known values",
                    err,
                )
                return self.data
            raise source_error(
                ERROR_FETCH, SOURCE_TRAFFIC_INCIDENTS, error=err
            ) from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching traffic incidents (%s); using last known values",
                    err,
                )
                return self.data
            raise

        return _parse_traffic_incidents(payload)
