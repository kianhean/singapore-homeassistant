"""Data coordinator for NEA dengue clusters (data.gov.sg GeoJSON dataset)."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

DENGUE_DATASET_ID = "d_dbfabf16158d1b0e1c420627c0819168"
_DOWNLOAD_API = (
    "https://api-open.data.gov.sg/v1/public/api/datasets/" + DENGUE_DATASET_ID
)
POLL_DOWNLOAD_URL = f"{_DOWNLOAD_API}/poll-download"
INITIATE_DOWNLOAD_URL = f"{_DOWNLOAD_API}/initiate-download"
DATASET_PAGE_URL = f"https://data.gov.sg/datasets/{DENGUE_DATASET_ID}/view"

# NEA updates the cluster file about once a day.
UPDATE_INTERVAL = timedelta(hours=6)
# After a failed refresh with no data yet, retry sooner than the 6 h interval so
# a transient data.gov.sg 429 at startup doesn't leave sensors unavailable for hours.
RETRY_INTERVAL = timedelta(minutes=10)

# data.gov.sg rate-limits bursts (HA startup fires several integrations' requests
# at once), so retry HTTP 429 with Retry-After / exponential backoff.
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2  # seconds; doubles per attempt
_MAX_RETRY_WAIT = 60.0  # seconds; cap on a server-supplied Retry-After

_EARTH_RADIUS_KM = 6371.0088

# A polygon is a list of rings; each ring a list of (lon, lat) points.
Ring = list[tuple[float, float]]


@dataclass
class DengueCluster:
    """One active dengue cluster."""

    locality: str
    case_size: int
    polygons: list[list[Ring]]
    latitude: float | None = None
    longitude: float | None = None

    def as_dict(self) -> dict:
        return {
            "locality": self.locality,
            "case_size": self.case_size,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass
class DengueData:
    """All active dengue clusters, largest first."""

    clusters: list[DengueCluster] = field(default_factory=list)

    @property
    def total_cases(self) -> int:
        return sum(c.case_size for c in self.clusters)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _point_in_ring(lat: float, lon: float, ring: Ring) -> bool:
    """Ray-casting point-in-polygon test (lon = x, lat = y)."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def _point_segment_km(
    lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Distance from a point to segment a-b, via a local equirectangular projection.

    Accurate to well under a metre at Singapore's scale (clusters span < 2 km).
    """
    kx = math.cos(math.radians(lat)) * math.pi / 180 * _EARTH_RADIUS_KM
    ky = math.pi / 180 * _EARTH_RADIUS_KM
    ax, ay = (a[0] - lon) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lon) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    t = (
        0.0
        if seg_len_sq == 0
        else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_len_sq))
    )
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py)


def distance_to_cluster_km(lat: float, lon: float, cluster: DengueCluster) -> float:
    """Distance from a point to the cluster boundary; 0.0 if inside the cluster."""
    best = math.inf
    for polygon in cluster.polygons:
        if not polygon:
            continue
        outer, holes = polygon[0], polygon[1:]
        if _point_in_ring(lat, lon, outer) and not any(
            _point_in_ring(lat, lon, h) for h in holes
        ):
            return 0.0
        for ring in polygon:
            for i in range(len(ring)):
                best = min(
                    best,
                    _point_segment_km(lat, lon, ring[i], ring[(i + 1) % len(ring)]),
                )
    return best


def nearest_cluster(
    lat: float, lon: float, data: DengueData
) -> tuple[DengueCluster, float] | None:
    """Return (cluster, distance_km) for the cluster closest to the point."""
    best: tuple[DengueCluster, float] | None = None
    for cluster in data.clusters:
        dist = distance_to_cluster_km(lat, lon, cluster)
        if best is None or dist < best[1]:
            best = (cluster, dist)
    return best


def _parse_ring(raw: object) -> Ring:
    ring: Ring = []
    if isinstance(raw, list):
        for pt in raw:
            if isinstance(pt, list | tuple) and len(pt) >= 2:
                try:
                    ring.append((float(pt[0]), float(pt[1])))
                except (TypeError, ValueError):
                    continue
    return ring


def _parse_geometry(geometry: object) -> list[list[Ring]]:
    if not isinstance(geometry, dict):
        return []
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Polygon":
        raw_polygons = [coords]
    elif gtype == "MultiPolygon":
        raw_polygons = coords if isinstance(coords, list) else []
    else:
        return []
    polygons: list[list[Ring]] = []
    for raw in raw_polygons:
        if not isinstance(raw, list):
            continue
        rings = [r for r in (_parse_ring(x) for x in raw) if len(r) >= 3]
        if rings:
            polygons.append(rings)
    return polygons


def _parse_dengue_geojson(payload: dict) -> DengueData:
    """Parse the NEA dengue clusters GeoJSON FeatureCollection.

    Each feature's ``properties`` carry ``LOCALITY`` and ``CASE_SIZE``; geometry is a
    Polygon (or MultiPolygon) in lon/lat. Raises ``UpdateFailed`` if the payload
    isn't a FeatureCollection. Zero features is valid (no active clusters).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise UpdateFailed("Dengue clusters payload is not a GeoJSON FeatureCollection")

    clusters: list[DengueCluster] = []
    for feature in payload["features"]:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        try:
            case_size = int(props.get("CASE_SIZE") or 0)
        except (TypeError, ValueError):
            case_size = 0
        polygons = _parse_geometry(feature.get("geometry"))
        outer_points = [pt for poly in polygons for pt in poly[0]]
        lat = lon = None
        if outer_points:
            lon = round(sum(p[0] for p in outer_points) / len(outer_points), 6)
            lat = round(sum(p[1] for p in outer_points) / len(outer_points), 6)
        clusters.append(
            DengueCluster(
                locality=str(props.get("LOCALITY") or "").strip(),
                case_size=case_size,
                polygons=polygons,
                latitude=lat,
                longitude=lon,
            )
        )
    clusters.sort(key=lambda c: c.case_size, reverse=True)
    return DengueData(clusters=clusters)


def _retry_after_seconds(header: str | None, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After if numeric, else backoff."""
    try:
        wait = float(header) if header is not None else None
    except (TypeError, ValueError):
        wait = None
    if wait is None or wait < 0:
        wait = float(_RETRY_BACKOFF_BASE ** (attempt + 1))
    return min(wait, _MAX_RETRY_WAIT)


class DengueClusterCoordinator(DataUpdateCoordinator[DengueData]):
    """Downloads the NEA dengue clusters GeoJSON from data.gov.sg."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore NEA Dengue Clusters",
            update_interval=UPDATE_INTERVAL,
            # Parsed data is a dataclass, so unchanged polls skip state writes.
            always_update=False,
        )

    async def _get_json(self, session: aiohttp.ClientSession, url: str) -> dict:
        """GET url as JSON, retrying HTTP 429 up to _MAX_RETRIES times."""
        for attempt in range(_MAX_RETRIES + 1):
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    return await response.json(content_type=None)
                if response.status != 429 or attempt == _MAX_RETRIES:
                    raise UpdateFailed(f"{url} returned HTTP {response.status}")
                wait = _retry_after_seconds(
                    response.headers.get("Retry-After"), attempt
                )
            _LOGGER.debug(
                "HTTP 429 from %s; retrying in %.1fs (%d/%d)",
                url,
                wait,
                attempt + 1,
                _MAX_RETRIES,
            )
            await asyncio.sleep(wait)
        raise UpdateFailed(f"{url} returned HTTP 429")  # pragma: no cover

    async def _download_url(self, session: aiohttp.ClientSession) -> str:
        """Get a signed S3 URL for the dataset file (initiating a download if needed)."""
        poll = await self._get_json(session, POLL_DOWNLOAD_URL)
        url = ((poll or {}).get("data") or {}).get("url")
        if url:
            return url
        init = await self._get_json(session, INITIATE_DOWNLOAD_URL)
        url = ((init or {}).get("data") or {}).get("url")
        if url:
            return url
        raise UpdateFailed("data.gov.sg did not return a dengue clusters download URL")

    async def _async_update_data(self) -> DengueData:
        try:
            data = await self._fetch()
        except UpdateFailed:
            if self.data is None:
                # Nothing to show yet: retry soon instead of waiting 6 hours.
                self.update_interval = RETRY_INTERVAL
            raise
        self.update_interval = UPDATE_INTERVAL
        return data

    async def _fetch(self) -> DengueData:
        session = async_get_clientsession(self.hass)
        try:
            file_url = await self._download_url(session)
            payload = await self._get_json(session, file_url)
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching dengue clusters (%s); using last known values", err
                )
                return self.data
            raise UpdateFailed(f"Error fetching dengue clusters: {err}") from err
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching dengue clusters (%s); using last known values", err
                )
                return self.data
            raise

        return _parse_dengue_geojson(payload)
