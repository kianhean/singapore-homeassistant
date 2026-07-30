"""Guard against clashing with DataUpdateCoordinator's own API.

A coordinator subclass that defines a method HA already uses internally silently
replaces it. `_async_refresh` is the dangerous one: HA calls it as
`_async_refresh(log_failures=..., scheduled=...)` on every poll, so overriding it
with a different signature raises TypeError before any data is fetched — and the
integration looks like it is simply producing nothing.
"""

from __future__ import annotations

import pytest

from custom_components.singapore.coe_coordinator import CoeCoordinator
from custom_components.singapore.coordinator import SPGroupCoordinator
from custom_components.singapore.holiday_coordinator import PublicHolidayCoordinator
from custom_components.singapore.train_coordinator import TrainStatusCoordinator
from custom_components.singapore.usage_coordinator import SPUsageCoordinator
from custom_components.singapore.weather_coordinator import SingaporeWeatherCoordinator

# Methods homeassistant.helpers.update_coordinator.DataUpdateCoordinator defines.
_HA_COORDINATOR_METHODS = frozenset(
    {
        "async_register_shutdown",
        "async_add_listener",
        "async_update_listeners",
        "async_shutdown",
        "_unschedule_refresh",
        "async_contexts",
        "_async_unsub_refresh",
        "_async_unsub_shutdown",
        "_schedule_refresh",
        "_handle_refresh_interval",
        "async_request_refresh",
        "async_config_entry_first_refresh",
        "_async_config_entry_first_refresh",
        "async_refresh",
        "_async_refresh",
        "async_set_update_error",
        "async_set_updated_data",
    }
)

# The extension points HA documents for subclasses.
_ALLOWED_OVERRIDES = frozenset(
    {"_async_update_data", "_async_setup", "_async_refresh_finished"}
)

_COORDINATORS = (
    SPGroupCoordinator,
    CoeCoordinator,
    PublicHolidayCoordinator,
    TrainStatusCoordinator,
    SingaporeWeatherCoordinator,
    SPUsageCoordinator,
)


@pytest.mark.parametrize("coordinator", _COORDINATORS, ids=lambda c: c.__name__)
def test_coordinator_does_not_shadow_ha_internals(coordinator):
    clashes = (set(vars(coordinator)) & _HA_COORDINATOR_METHODS) - _ALLOWED_OVERRIDES
    assert not clashes, (
        f"{coordinator.__name__} defines {sorted(clashes)}, which "
        "DataUpdateCoordinator calls internally — pick another name"
    )
