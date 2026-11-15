"""Constants for the Singapore integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Final

DOMAIN: Final = "singapore"

# --- Options (Settings → Devices & services → Singapore → Configure) -------

# Stored as the *disabled* sources so a source added in a later release is on
# for everyone who saved options before it existed.
CONF_DISABLED_SOURCES: Final = "disabled_sources"
# The options form shows the enabled sources (a ticked box means "on").
CONF_SOURCES: Final = "sources"
CONF_API_KEY: Final = "api_key"
CONF_POLLING: Final = "polling"

POLLING_NORMAL: Final = "normal"
POLLING_REDUCED: Final = "reduced"
POLLING_OPTIONS: Final = [POLLING_NORMAL, POLLING_REDUCED]
# "Reduced" stretches every sub-hourly poll by this factor (2 min → 6 min,
# 10 min → 30 min). Daily/6-hourly sources are already slow and keep theirs.
REDUCED_POLLING_FACTOR: Final = 3
REDUCED_POLLING_MAX_BASE: Final = timedelta(hours=1)

# Data sources, keyed like the SingaporeData fields. Order = options form order.
SOURCE_TARIFF: Final = "tariff"
SOURCE_COE: Final = "coe"
SOURCE_WEATHER: Final = "weather"
SOURCE_HOLIDAY: Final = "holiday"
SOURCE_TRAIN: Final = "train"
SOURCE_PSI: Final = "psi"
SOURCE_FLOOD: Final = "flood"
SOURCE_TRAFFIC: Final = "traffic"
SOURCE_DENGUE: Final = "dengue"
SOURCE_LIGHTNING: Final = "lightning"
SOURCE_UV: Final = "uv"
SOURCE_RADAR: Final = "radar"

SOURCES: Final = (
    SOURCE_TARIFF,
    SOURCE_COE,
    SOURCE_WEATHER,
    SOURCE_HOLIDAY,
    SOURCE_TRAIN,
    SOURCE_PSI,
    SOURCE_FLOOD,
    SOURCE_TRAFFIC,
    SOURCE_DENGUE,
    SOURCE_LIGHTNING,
    SOURCE_UV,
    SOURCE_RADAR,
)

# English names for log lines and repair placeholders (UI labels for the
# options form are translated under selector.sources in strings.json).
SOURCE_NAMES: Final = {
    SOURCE_TARIFF: "SP Group tariffs",
    SOURCE_COE: "COE bidding results",
    SOURCE_WEATHER: "NEA weather",
    SOURCE_HOLIDAY: "Public holidays (MOM)",
    SOURCE_TRAIN: "MRT/LRT status",
    SOURCE_PSI: "Air quality (PSI)",
    SOURCE_FLOOD: "PUB flood alerts",
    SOURCE_TRAFFIC: "Checkpoint traffic cameras",
    SOURCE_DENGUE: "NEA dengue clusters",
    SOURCE_LIGHTNING: "NEA lightning",
    SOURCE_UV: "NEA UV index",
    SOURCE_RADAR: "NEA rain radar",
}

# data.gov.sg reads an optional API key from this header (higher rate limits).
DATA_GOV_API_KEY_HEADER: Final = "x-api-key"

ISSUE_TRACKER_URL: Final = "https://github.com/kianhean/singapore-homeassistant/issues"


def _options(entry: Any) -> Mapping[str, Any]:
    """A config entry's options (or an options mapping itself)."""
    if isinstance(entry, Mapping):
        return entry
    return getattr(entry, "options", None) or {}


def enabled_sources(entry: Any) -> set[str]:
    """The data sources turned on for this entry (all of them by default)."""
    disabled = set(_options(entry).get(CONF_DISABLED_SOURCES) or ())
    return {source for source in SOURCES if source not in disabled}


def data_gov_headers(entry: Any) -> dict[str, str]:
    """Request headers for data.gov.sg: the user's API key, when one is set."""
    key = (_options(entry).get(CONF_API_KEY) or "").strip()
    return {DATA_GOV_API_KEY_HEADER: key} if key else {}


def scaled_interval(entry: Any, interval: timedelta | None) -> timedelta | None:
    """A coordinator's poll interval after the entry's polling option."""
    if (
        interval is not None
        and _options(entry).get(CONF_POLLING) == POLLING_REDUCED
        and interval < REDUCED_POLLING_MAX_BASE
    ):
        return interval * REDUCED_POLLING_FACTOR
    return interval
