"""Tests for the diagnostics platform."""

from datetime import date
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntry

from custom_components.singapore import SingaporeData
from custom_components.singapore.capitaland_coordinator import (
    CapitaLandData,
    CapitaLandSite,
)
from custom_components.singapore.carpark_coordinator import (
    CarparkAvailability,
    CarparkData,
    LotCount,
)
from custom_components.singapore.dengue_coordinator import DengueData
from custom_components.singapore.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.singapore.flood_coordinator import FloodAlertData
from custom_components.singapore.fuel_coordinator import FuelData, FuelPrice
from custom_components.singapore.hawker_coordinator import HawkerData
from custom_components.singapore.holiday_coordinator import PublicHoliday
from custom_components.singapore.lightning_coordinator import LightningData
from custom_components.singapore.psi_coordinator import PsiData
from custom_components.singapore.radar_coordinator import RadarData
from custom_components.singapore.school_coordinator import SchoolEvent
from custom_components.singapore.taxi_coordinator import TaxiData, TaxiPosition
from custom_components.singapore.tide_coordinator import TideData
from custom_components.singapore.traffic_coordinator import TrafficImagesData
from custom_components.singapore.traffic_incidents_coordinator import (
    TrafficIncidentsData,
)
from custom_components.singapore.train_coordinator import TrainStatusData
from custom_components.singapore.uv_coordinator import UvData
from custom_components.singapore.warning_coordinator import WeatherWarningsData
from custom_components.singapore.wbgt_coordinator import WbgtData


def _coordinator(data, success=True, exc=None):
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = success
    coordinator.last_exception = exc
    return coordinator


async def test_diagnostics_serializes_all_coordinators():
    entry = ConfigEntry()
    entry.runtime_data = SingaporeData(
        tariff=_coordinator(None, success=False, exc=RuntimeError("boom")),
        coe=_coordinator(None),
        weather=_coordinator(None),
        holiday=_coordinator([PublicHoliday(name="New Year", day=date(2026, 1, 1))]),
        train=_coordinator(
            TrainStatusData(status="normal", details="", line_statuses={})
        ),
        psi=_coordinator(PsiData(national={"psi_twenty_four_hourly": 39.0})),
        flood=_coordinator(FloodAlertData()),
        traffic=_coordinator(TrafficImagesData()),
        dengue=_coordinator(DengueData()),
        lightning=_coordinator(LightningData()),
        uv=_coordinator(UvData()),
        radar=_coordinator(RadarData()),
        school=_coordinator(
            [SchoolEvent("Term 1", "terms", date(2026, 1, 2), date(2026, 3, 13))]
        ),
        wbgt=_coordinator(WbgtData()),
        warnings=_coordinator(WeatherWarningsData()),
        tides=_coordinator(TideData()),
        hawker=_coordinator(HawkerData()),
        traffic_incidents=_coordinator(TrafficIncidentsData()),
        fuel=_coordinator(
            FuelData(
                prices={"ron95": FuelPrice(2.92, "2026 Sep", "Petrol, 95 Octane")},
                data_last_updated="23/10/2026",
            )
        ),
        carpark=_coordinator(
            CarparkData(
                tracked=["BJ55"],
                availability={
                    "BJ55": CarparkAvailability("BJ55", {"C": LotCount(10, 100)})
                },
            )
        ),
        taxi=_coordinator(
            TaxiData(3000, 1, 4, 12, 0.3, None, (TaxiPosition(1.3, 103.8, 0.3),))
        ),
        capitaland=_coordinator(
            CapitaLandData(
                {"BM": CapitaLandSite("BM", "Bedok Mall", "Retail", 39, 264, False)}
            )
        ),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    coordinators = result["coordinators"]
    assert set(coordinators) == {
        "tariff",
        "coe",
        "weather",
        "holiday",
        "train",
        "psi",
        "flood",
        "traffic",
        "dengue",
        "lightning",
        "uv",
        "radar",
        "school",
        "wbgt",
        "warnings",
        "tides",
        "hawker",
        "traffic_incidents",
        "fuel",
        "carpark",
        "taxi",
        "capitaland",
    }
    assert coordinators["tariff"]["last_update_success"] is False
    assert "boom" in coordinators["tariff"]["last_exception"]
    assert coordinators["holiday"]["data"] == [
        {"name": "New Year", "day": date(2026, 1, 1)}
    ]
    assert coordinators["train"]["data"]["status"] == "normal"
    assert coordinators["coe"]["data"] is None
    assert coordinators["psi"]["data"]["national"]["psi_twenty_four_hourly"] == 39.0
    assert coordinators["flood"]["data"]["alerts"] == []
    assert coordinators["dengue"]["data"]["clusters"] == []
    assert coordinators["lightning"]["data"]["strikes"] == []
    assert coordinators["uv"]["data"]["readings"] == []
    assert coordinators["school"]["data"][0]["kind"] == "terms"
    assert coordinators["carpark"]["data"]["availability"]["BJ55"]["lots"] == {
        "C": {"available": 10, "total": 100}
    }
    assert coordinators["taxi"]["data"]["within_1_km"] == 4
    # Taxi positions near home would reveal it: never in diagnostics.
    assert "nearby_taxis" not in coordinators["taxi"]["data"]
    assert coordinators["capitaland"]["data"]["sites"]["BM"]["available"] == 39
    assert result["options"]["disabled_school_events"] == []
