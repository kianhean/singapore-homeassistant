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
# Opt-in sources (OPT_IN_SOURCES) are stored the other way round: the ones the
# user turned on. They poll large payloads and only suit some users.
CONF_ENABLED_SOURCES: Final = "enabled_sources"
CONF_API_KEY: Final = "api_key"
CONF_LTA_API_KEY: Final = "lta_api_key"
CONF_POLLING: Final = "polling"
# Which MOE school calendars to create; stored inverted like the sources.
CONF_SCHOOL_EVENTS: Final = "school_events"
CONF_DISABLED_SCHOOL_EVENTS: Final = "disabled_school_events"
# HDB carpark numbers to track, comma separated (empty: the ones nearest home).
CONF_CARPARKS: Final = "carparks"
# MRT/LRT station codes for LTA DataMall crowd density, comma separated.
CONF_TRAIN_CROWD_STATIONS: Final = "train_crowd_stations"
DEFAULT_TRAIN_CROWD_STATIONS: Final = ("EW13", "EW14", "NS24")
# Optional 6-digit Singapore postal codes for EV charger lookup (empty: nearest home).
CONF_EV_POSTAL_CODES: Final = "ev_postal_codes"
# Malls / developments for LTA DataMall carpark availability: CarParkIDs or
# parts of development names, comma separated (empty: the ones nearest home).
CONF_MALL_CARPARKS: Final = "mall_carparks"
# Foreign currencies to quote against SGD from ECB reference rates.
CONF_FX_CURRENCIES: Final = "fx_currencies"

DEFAULT_FX_CURRENCIES: Final = ("USD", "EUR", "GBP", "JPY", "MYR", "CNY", "AUD", "HKD")
FX_CURRENCIES: Final = (
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "MYR",
    "CNY",
    "AUD",
    "HKD",
    "THB",
    "IDR",
    "KRW",
    "INR",
    "PHP",
    "NZD",
    "CAD",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
    "CZK",
    "HUF",
    "RON",
    "ISK",
    "TRY",
    "BRL",
    "MXN",
    "ILS",
    "ZAR",
)

# MUIS prayer timetable columns, in the day's order; each is a sensor
# (translation key prayer_<key>). Syuruk is sunrise, not a prayer.
PRAYER_TIMES: Final = ("subuh", "syuruk", "zohor", "asar", "maghrib", "isyak")
PRAYER_SYURUK: Final = "syuruk"

SCHOOL_HOLIDAYS: Final = "holidays"
SCHOOL_TERMS: Final = "terms"
SCHOOL_EXAMS: Final = "exams"
# One calendar each (translation key "school_<kind>"). Order = form order.
SCHOOL_EVENT_KINDS: Final = (SCHOOL_HOLIDAYS, SCHOOL_TERMS, SCHOOL_EXAMS)

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
SOURCE_SCHOOL: Final = "school"
SOURCE_TRAIN: Final = "train"
SOURCE_PSI: Final = "psi"
SOURCE_FLOOD: Final = "flood"
SOURCE_TRAFFIC: Final = "traffic"
SOURCE_DENGUE: Final = "dengue"
SOURCE_LIGHTNING: Final = "lightning"
SOURCE_UV: Final = "uv"
SOURCE_RADAR: Final = "radar"
SOURCE_WBGT: Final = "wbgt"
SOURCE_WARNINGS: Final = "warnings"
SOURCE_TIDES: Final = "tides"
SOURCE_HAWKER: Final = "hawker"
SOURCE_TRAFFIC_INCIDENTS: Final = "traffic_incidents"
SOURCE_FX: Final = "fx"
SOURCE_INTEREST: Final = "interest"
SOURCE_CARPARK: Final = "carpark"
SOURCE_TAXI: Final = "taxi"
SOURCE_CAPITALAND: Final = "capitaland"
SOURCE_TRAIN_CROWD: Final = "train_crowd"
SOURCE_EV_CHARGERS: Final = "ev_chargers"
SOURCE_MALL_CARPARK: Final = "mall_carpark"
SOURCE_PRAYER: Final = "prayer"

SOURCES: Final = (
    SOURCE_TARIFF,
    SOURCE_COE,
    SOURCE_WEATHER,
    SOURCE_HOLIDAY,
    SOURCE_SCHOOL,
    SOURCE_TRAIN,
    SOURCE_PSI,
    SOURCE_FLOOD,
    SOURCE_TRAFFIC,
    SOURCE_DENGUE,
    SOURCE_LIGHTNING,
    SOURCE_UV,
    SOURCE_RADAR,
    SOURCE_WBGT,
    SOURCE_WARNINGS,
    SOURCE_TIDES,
    SOURCE_HAWKER,
    SOURCE_TRAFFIC_INCIDENTS,
    SOURCE_FX,
    SOURCE_INTEREST,
    SOURCE_CARPARK,
    SOURCE_TAXI,
    SOURCE_CAPITALAND,
    SOURCE_TRAIN_CROWD,
    SOURCE_EV_CHARGERS,
    SOURCE_MALL_CARPARK,
    SOURCE_PRAYER,
)

# Off until ticked in the options (stored in CONF_ENABLED_SOURCES): carparks and
# taxis poll a large island-wide payload every few minutes for a few local
# numbers; CapitaLand's carparks come from a mall operator's website; train
# crowd density, EV chargers and mall carparks require an optional LTA DataMall
# AccountKey; prayer times only suit some users (seven sensors).
OPT_IN_SOURCES: Final = (
    SOURCE_CARPARK,
    SOURCE_TAXI,
    SOURCE_CAPITALAND,
    SOURCE_TRAIN_CROWD,
    SOURCE_EV_CHARGERS,
    SOURCE_MALL_CARPARK,
    SOURCE_PRAYER,
)
LTA_DATAMALL_SOURCES: Final = (
    SOURCE_TRAIN_CROWD,
    SOURCE_EV_CHARGERS,
    SOURCE_MALL_CARPARK,
)

# English names for log lines and repair placeholders (UI labels for the
# options form are translated under selector.sources in strings.json).
SOURCE_NAMES: Final = {
    SOURCE_TARIFF: "SP Group tariffs",
    SOURCE_COE: "COE bidding results",
    SOURCE_WEATHER: "NEA weather",
    SOURCE_HOLIDAY: "Public holidays (MOM)",
    SOURCE_SCHOOL: "MOE school calendar",
    SOURCE_TRAIN: "MRT/LRT status",
    SOURCE_PSI: "Air quality (PSI)",
    SOURCE_FLOOD: "PUB flood alerts",
    SOURCE_TRAFFIC: "Checkpoint traffic cameras",
    SOURCE_DENGUE: "NEA dengue clusters",
    SOURCE_LIGHTNING: "NEA lightning",
    SOURCE_UV: "NEA UV index",
    SOURCE_RADAR: "NEA rain radar",
    SOURCE_WBGT: "NEA heat stress (WBGT)",
    SOURCE_WARNINGS: "NEA weather warnings",
    SOURCE_TIDES: "NEA tide timings",
    SOURCE_HAWKER: "Hawker centre closures",
    SOURCE_TRAFFIC_INCIDENTS: "LTA traffic incidents",
    SOURCE_FX: "FX rates (ECB)",
    SOURCE_INTEREST: "Interest rates (MAS/SingStat)",
    SOURCE_CARPARK: "HDB carpark availability",
    SOURCE_TAXI: "Taxi availability",
    SOURCE_CAPITALAND: "CapitaLand mall carparks",
    SOURCE_TRAIN_CROWD: "MRT/LRT crowd density (LTA DataMall)",
    SOURCE_EV_CHARGERS: "EV charger availability (LTA DataMall)",
    SOURCE_MALL_CARPARK: "Mall carparks (LTA DataMall)",
    SOURCE_PRAYER: "Prayer times (MUIS)",
}

# data.gov.sg reads an optional API key from this header (higher rate limits).
DATA_GOV_API_KEY_HEADER: Final = "x-api-key"
# LTA DataMall reads the user's AccountKey from this header.
LTA_DATAMALL_API_KEY_HEADER: Final = "AccountKey"

ISSUE_TRACKER_URL: Final = "https://github.com/kianhean/singapore-homeassistant/issues"


def _options(entry: Any) -> Mapping[str, Any]:
    """A config entry's options (or an options mapping itself)."""
    if isinstance(entry, Mapping):
        return entry
    return getattr(entry, "options", None) or {}


def enabled_sources(entry: Any) -> set[str]:
    """The data sources turned on for this entry.

    Every source is on by default except the opt-in ones, which are on only
    once ticked in the options.
    """
    options = _options(entry)
    disabled = set(options.get(CONF_DISABLED_SOURCES) or ())
    opted_in = set(options.get(CONF_ENABLED_SOURCES) or ())
    return {
        source
        for source in SOURCES
        if source not in disabled
        and (source not in OPT_IN_SOURCES or source in opted_in)
    }


def enabled_school_events(entry: Any) -> set[str]:
    """The MOE school calendars turned on for this entry (all by default)."""
    disabled = set(_options(entry).get(CONF_DISABLED_SCHOOL_EVENTS) or ())
    return {kind for kind in SCHOOL_EVENT_KINDS if kind not in disabled}


def carpark_numbers(entry: Any) -> list[str]:
    """The HDB carpark numbers chosen in the options (upper-cased, in order)."""
    raw = _options(entry).get(CONF_CARPARKS) or ""
    numbers: list[str] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        if (number := part.strip().upper()) and number not in numbers:
            numbers.append(number)
    return numbers


def mall_carpark_terms(entry: Any) -> list[str]:
    """Mall carparks chosen in the options: CarParkIDs or name parts, in order.

    Only commas and semicolons separate them: development names have spaces.
    """
    raw = _options(entry).get(CONF_MALL_CARPARKS) or ""
    terms: list[str] = []
    for part in raw.replace(";", ",").split(","):
        term = " ".join(part.split())
        if term and term.casefold() not in {t.casefold() for t in terms}:
            terms.append(term)
    return terms


def fx_currencies(entry: Any) -> list[str]:
    """Currencies to quote against SGD (defaults when none stored)."""
    chosen = _options(entry).get(CONF_FX_CURRENCIES)
    if not chosen:
        return list(DEFAULT_FX_CURRENCIES)
    valid = set(FX_CURRENCIES)
    result = [code for code in chosen if code in valid]
    return result or list(DEFAULT_FX_CURRENCIES)


def data_gov_headers(entry: Any) -> dict[str, str]:
    """Request headers for data.gov.sg: the user's API key, when one is set."""
    key = (_options(entry).get(CONF_API_KEY) or "").strip()
    return {DATA_GOV_API_KEY_HEADER: key} if key else {}


def lta_datamall_headers(entry: Any) -> dict[str, str]:
    """Request headers for LTA DataMall: AccountKey when set."""
    key = (_options(entry).get(CONF_LTA_API_KEY) or "").strip()
    return (
        {LTA_DATAMALL_API_KEY_HEADER: key, "accept": "application/json"} if key else {}
    )


def train_crowd_stations(entry: Any) -> list[str]:
    """MRT/LRT station codes chosen for crowd density (upper-cased, in order)."""
    raw = _options(entry).get(CONF_TRAIN_CROWD_STATIONS) or ""
    stations: list[str] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        if (code := part.strip().upper()) and code not in stations:
            stations.append(code)
    return stations or list(DEFAULT_TRAIN_CROWD_STATIONS)


def ev_postal_codes(entry: Any) -> list[str]:
    """Optional Singapore postal codes chosen for EV charger lookup."""
    raw = _options(entry).get(CONF_EV_POSTAL_CODES) or ""
    codes: list[str] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        if (code := part.strip()) and code.isdigit() and code not in codes:
            codes.append(code)
    return codes


def scaled_interval(entry: Any, interval: timedelta | None) -> timedelta | None:
    """A coordinator's poll interval after the entry's polling option."""
    if (
        interval is not None
        and _options(entry).get(CONF_POLLING) == POLLING_REDUCED
        and interval < REDUCED_POLLING_MAX_BASE
    ):
        return interval * REDUCED_POLLING_FACTOR
    return interval
