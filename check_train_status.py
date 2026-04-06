# /// script
# requires-python = ">=3.11"
# dependencies = ["niquests"]
# ///
"""Debug script: fetch live train status and print parsed results.

Usage:
    uv run check_train_status.py
"""

from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Inject minimal HA fakes so train_coordinator.py can be imported standalone
# ---------------------------------------------------------------------------


def _mod(name: str, **attrs) -> ModuleType:
    m = ModuleType(name)
    m.__dict__.update(attrs)
    return m


class _UpdateFailed(Exception):
    pass


class _DataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, name, update_interval):
        pass


_HA_MODULES = {
    "homeassistant": _mod("homeassistant"),
    "homeassistant.core": _mod(
        "homeassistant.core", HomeAssistant=object, callback=lambda f: f
    ),
    "homeassistant.const": _mod("homeassistant.const"),
    "homeassistant.helpers": _mod("homeassistant.helpers"),
    "homeassistant.helpers.update_coordinator": _mod(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_DataUpdateCoordinator,
        CoordinatorEntity=object,
        UpdateFailed=_UpdateFailed,
    ),
    "homeassistant.helpers.aiohttp_client": _mod(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=MagicMock(),
    ),
    "homeassistant.helpers.entity_platform": _mod(
        "homeassistant.helpers.entity_platform"
    ),
    "homeassistant.helpers.event": _mod(
        "homeassistant.helpers.event",
        async_track_time_change=MagicMock(return_value=MagicMock()),
    ),
}
for _name, _mod_obj in _HA_MODULES.items():
    sys.modules.setdefault(_name, _mod_obj)

sys.path.insert(0, ".")

# ---------------------------------------------------------------------------
# Now safe to import coordinator
# ---------------------------------------------------------------------------

import niquests  # noqa: E402

from custom_components.singapore.train_coordinator import (  # noqa: E402
    _TRAIN_STATUS_REFERER,
    TRAIN_LINES,
    TRAIN_STATUS_URL,
    _parse_train_status,
)

# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

with niquests.Session() as s:
    r = s.post(
        TRAIN_STATUS_URL,
        data={"serviceName": "LTAGOVTrainServiceAlerts", "param": ""},
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": _TRAIN_STATUS_REFERER,
        },
        timeout=30,
    )
    print(f"HTTP {r.status_code}")
    payload = r.json()

print("\n=== Raw payload ===")
print(json.dumps(payload, indent=2))

data = _parse_train_status(payload)

print("\n=== Parsed result ===")
print(f"Overall status : {data.status}")
print(f"Details        : {data.details or '(none)'}")
print("\nPer-line statuses:")
for line in TRAIN_LINES:
    status = data.line_statuses.get(line, "unknown")
    marker = " <--" if status != "normal" else ""
    print(f"  {line:<30} {status}{marker}")
