"""Tests for the Singapore binary_sensor platform."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry

from custom_components.singapore import SingaporeData, async_remove_stale
from custom_components.singapore.binary_sensor import (
    SingaporeFloodAlertNearbyBinarySensor,
    SingaporeInDengueClusterBinarySensor,
    SingaporeLightningNearbyBinarySensor,
    SingaporePublicHolidayTodayBinarySensor,
    SingaporeSchoolHolidayTodayBinarySensor,
    SingaporeTrafficIncidentNearbyBinarySensor,
    async_setup_entry,
)
from custom_components.singapore.const import (
    CONF_DISABLED_SCHOOL_EVENTS,
    SCHOOL_HOLIDAYS,
    SOURCES,
)
from custom_components.singapore.dengue_coordinator import (
    DengueCluster,
    DengueData,
)
from custom_components.singapore.flood_coordinator import FloodAlert, FloodAlertData
from custom_components.singapore.holiday_coordinator import PublicHoliday
from custom_components.singapore.lightning_coordinator import (
    LightningData,
    LightningStrike,
)
from custom_components.singapore.school_coordinator import SchoolEvent
from custom_components.singapore.traffic_incidents_coordinator import (
    TrafficIncident,
    TrafficIncidentsData,
)


def _coord(data):
    c = MagicMock()
    c.data = data
    c.last_update_success = True
    return c


def _attach_home(entity, lat: float = 1.3521, lon: float = 103.8198) -> None:
    entity.hass = SimpleNamespace(config=SimpleNamespace(latitude=lat, longitude=lon))


def test_public_holiday_today_binary_sensor():
    holidays = [
        PublicHoliday(name="National Day", day=date(2026, 8, 9)),
        PublicHoliday(name="Deepavali", day=date(2026, 11, 8)),
    ]
    sensor = SingaporePublicHolidayTodayBinarySensor(_coord(holidays), "e1")
    assert sensor.unique_id == "e1_public_holiday_today"

    with patch(
        "custom_components.singapore.binary_sensor.dt_util.now",
        return_value=datetime(2026, 8, 9, 9, 0, tzinfo=UTC),
    ):
        assert sensor.is_on is True
        attrs = sensor.extra_state_attributes
        assert attrs["holiday"] == "National Day"
        assert attrs["next_holiday"] == "Deepavali"
        assert attrs["next_holiday_date"] == "2026-11-08"

    with patch(
        "custom_components.singapore.binary_sensor.dt_util.now",
        return_value=datetime(2026, 8, 8, 9, 0, tzinfo=UTC),
    ):
        assert sensor.is_on is False
        assert sensor.extra_state_attributes["holiday"] is None
        assert sensor.extra_state_attributes["next_holiday"] == "National Day"

    empty = SingaporePublicHolidayTodayBinarySensor(_coord(None), "e1")
    assert empty.is_on is None
    assert empty.extra_state_attributes == {}


def test_school_holiday_today_binary_sensor():
    events = [
        SchoolEvent(
            name="June Holidays",
            kind=SCHOOL_HOLIDAYS,
            start=date(2026, 5, 30),
            end=date(2026, 6, 28),
        ),
        SchoolEvent(
            name="Term 3",
            kind="terms",
            start=date(2026, 6, 29),
            end=date(2026, 9, 4),
        ),
    ]
    sensor = SingaporeSchoolHolidayTodayBinarySensor(_coord(events), "e1")
    assert sensor.unique_id == "e1_school_holiday_today"

    with patch(
        "custom_components.singapore.binary_sensor.dt_util.now",
        return_value=datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
    ):
        assert sensor.is_on is True
        attrs = sensor.extra_state_attributes
        assert attrs["holiday"] == "June Holidays"
        assert attrs["start"] == "2026-05-30"
        assert attrs["end"] == "2026-06-28"

    with patch(
        "custom_components.singapore.binary_sensor.dt_util.now",
        return_value=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
    ):
        assert sensor.is_on is False
        assert sensor.extra_state_attributes["holiday"] is None

    empty = SingaporeSchoolHolidayTodayBinarySensor(_coord(None), "e1")
    assert empty.is_on is None
    assert empty.extra_state_attributes == {}


def test_lightning_nearby_binary_sensor():
    now = datetime(2026, 11, 20, 5, 0, tzinfo=UTC)
    near_data = LightningData(
        strikes=[
            LightningStrike(
                time=now, latitude=1.36, longitude=103.82, type="cloud_to_ground"
            )
        ]
    )
    sensor = SingaporeLightningNearbyBinarySensor(_coord(near_data), "e1")
    _attach_home(sensor, 1.3521, 103.8198)
    assert sensor.device_class == BinarySensorDeviceClass.SAFETY
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["strikes_within_10_km"] == 1
    assert sensor.extra_state_attributes["nearest_km"] < 2.0

    far_data = LightningData(
        strikes=[
            LightningStrike(
                time=now, latitude=1.0, longitude=104.0, type="cloud_to_cloud"
            )
        ]
    )
    far_sensor = SingaporeLightningNearbyBinarySensor(_coord(far_data), "e1")
    _attach_home(far_sensor, 1.3521, 103.8198)
    assert far_sensor.is_on is False
    assert far_sensor.extra_state_attributes["strikes_within_10_km"] == 0

    empty = SingaporeLightningNearbyBinarySensor(_coord(None), "e1")
    _attach_home(empty)
    assert empty.is_on is None
    assert empty.extra_state_attributes == {}


def test_flood_alert_nearby_binary_sensor():
    now = datetime(2026, 11, 20, 5, 0, tzinfo=UTC)
    alert_near = FloodAlert(
        identifier="a1",
        issued=now,
        headline="Flash Flood Alert",
        description="Bukit Timah Rd",
        area="Central",
        instruction="Avoid",
        severity="Moderate",
        urgency="Immediate",
        latitude=1.355,
        longitude=103.82,
    )
    alert_far = FloodAlert(
        identifier="a2",
        issued=now,
        headline="Flash Flood Alert",
        description="Changi",
        area="East",
        instruction="Avoid",
        severity="Moderate",
        urgency="Immediate",
        latitude=1.364,
        longitude=103.99,
    )
    sensor = SingaporeFloodAlertNearbyBinarySensor(
        _coord(FloodAlertData(alerts=[alert_far, alert_near])), "e1"
    )
    _attach_home(sensor, 1.3521, 103.8198)
    assert sensor.device_class == BinarySensorDeviceClass.SAFETY
    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["nearby_alerts"] == ["Bukit Timah Rd"]
    assert attrs["nearest_km"] < 1.0

    far_sensor = SingaporeFloodAlertNearbyBinarySensor(
        _coord(FloodAlertData(alerts=[alert_far])), "e1"
    )
    _attach_home(far_sensor, 1.3521, 103.8198)
    assert far_sensor.is_on is False
    assert far_sensor.extra_state_attributes["nearby_alerts"] == []

    empty = SingaporeFloodAlertNearbyBinarySensor(_coord(None), "e1")
    _attach_home(empty)
    assert empty.is_on is None
    assert empty.extra_state_attributes == {}


def test_in_dengue_cluster_binary_sensor():
    # A square polygon around (1.35, 103.82) — lon/lat tuples
    ring = [
        (103.81, 1.34),
        (103.83, 1.34),
        (103.83, 1.36),
        (103.81, 1.36),
        (103.81, 1.34),
    ]
    cluster = DengueCluster(
        locality="Bishan",
        case_size=12,
        polygons=[[ring]],
        latitude=1.35,
        longitude=103.82,
    )
    data = DengueData(clusters=[cluster])

    inside = SingaporeInDengueClusterBinarySensor(_coord(data), "e1")
    _attach_home(inside, 1.35, 103.82)
    assert inside.device_class == BinarySensorDeviceClass.PROBLEM
    assert inside.is_on is True
    assert inside.extra_state_attributes["nearest_km"] == 0.0
    assert inside.extra_state_attributes["locality"] == "Bishan"
    assert inside.extra_state_attributes["cases"] == 12

    outside = SingaporeInDengueClusterBinarySensor(_coord(data), "e1")
    _attach_home(outside, 1.40, 103.90)
    assert outside.is_on is False
    assert outside.extra_state_attributes["nearest_km"] > 0.0

    empty = SingaporeInDengueClusterBinarySensor(_coord(None), "e1")
    _attach_home(empty)
    assert empty.is_on is None
    assert empty.extra_state_attributes == {}


def test_traffic_incident_nearby_binary_sensor():
    near = TrafficIncident(
        type="Accident",
        message="Accident on PIE",
        latitude=1.355,
        longitude=103.82,
    )
    far = TrafficIncident(
        type="Roadwork",
        message="Road Works on TPE",
        latitude=1.395,
        longitude=103.96,
    )
    sensor = SingaporeTrafficIncidentNearbyBinarySensor(
        _coord(TrafficIncidentsData(incidents=[far, near])), "e1"
    )
    _attach_home(sensor, 1.3521, 103.8198)
    assert sensor.device_class == BinarySensorDeviceClass.PROBLEM
    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["nearby_incidents"] == ["Accident on PIE"]
    assert attrs["nearest_type"] == "Accident"
    assert attrs["nearest_km"] < 1.0

    far_sensor = SingaporeTrafficIncidentNearbyBinarySensor(
        _coord(TrafficIncidentsData(incidents=[far])), "e1"
    )
    _attach_home(far_sensor, 1.3521, 103.8198)
    assert far_sensor.is_on is False
    assert far_sensor.extra_state_attributes["nearby_incidents"] == []

    empty = SingaporeTrafficIncidentNearbyBinarySensor(_coord(None), "e1")
    _attach_home(empty)
    assert empty.is_on is None
    assert empty.extra_state_attributes == {}


async def test_setup_entry_respects_school_holidays_option():
    entry = ConfigEntry("e1")
    entry.options = {CONF_DISABLED_SCHOOL_EVENTS: [SCHOOL_HOLIDAYS]}
    entry.runtime_data = SingaporeData(
        **{s: MagicMock() if s in ("holiday", "school") else None for s in SOURCES},
    )
    added = []
    await async_setup_entry(MagicMock(), entry, added.extend)
    keys = [e._attr_translation_key for e in added]
    assert keys == ["public_holiday_today"]

    # Stale cleanup also removes school_holiday_today when school holidays are off.
    entity_registry = MagicMock()
    device_registry = MagicMock()
    reg_entries = [
        SimpleNamespace(
            entity_id="binary_sensor.school_holiday_today",
            domain="binary_sensor",
            translation_key="school_holiday_today",
            config_subentry_id=None,
            unique_id="e1_school_holiday_today",
        )
    ]
    with (
        patch("custom_components.singapore.er.async_get", return_value=entity_registry),
        patch(
            "custom_components.singapore.er.async_entries_for_config_entry",
            return_value=reg_entries,
        ),
        patch("custom_components.singapore.dr.async_get", return_value=device_registry),
        patch(
            "custom_components.singapore.dr.async_entries_for_config_entry",
            return_value=[],
        ),
    ):
        async_remove_stale(MagicMock(), entry, set(SOURCES))
    entity_registry.async_remove.assert_called_once_with(
        "binary_sensor.school_holiday_today"
    )
