# Singapore Home Assistant Integration

A general-purpose HACS integration for Singapore-specific Home Assistant entities.
The integration domain is `singapore`.

## Structure

```
custom_components/singapore/
├── __init__.py             # Entry setup/teardown; creates and stores coordinators
├── const.py                # DOMAIN, option keys, SOURCES + helpers (enabled_sources, data_gov_headers,
│                           #   scaled_interval); imported by config_flow without pulling in coordinators
├── datagov.py              # Shared per-host data.gov.sg limiter (pacing + host-wide 429 pause/retry)
├── health.py               # SourceHealthMonitor: hourly check, "source_failing_<source>" repair issues
├── coordinator.py          # SPGroupCoordinator: fetches + parses SP Group tariff page
├── coe_coordinator.py      # CoeCoordinator: fetches COE results from data.gov.sg API
├── holiday_coordinator.py  # PublicHolidayCoordinator: fetches + parses MOM holidays
├── weather_coordinator.py  # SingaporeWeatherCoordinator: 2-hour forecasts + collection 1459 readings
├── train_coordinator.py    # TrainStatusCoordinator: scrapes mytransport.sg MRT/LRT status
├── psi_coordinator.py      # PsiCoordinator: NEA PSI + pollutant readings per region
├── flood_coordinator.py    # FloodAlertCoordinator: PUB flash-flood alerts from data.gov.sg
├── traffic_coordinator.py  # TrafficImagesCoordinator: LTA checkpoint camera stills (Woodlands/Tuas)
├── dengue_coordinator.py   # DengueClusterCoordinator: NEA dengue cluster GeoJSON + distance helpers
├── lightning_coordinator.py # LightningCoordinator: NEA lightning strikes, 30-min rolling window
├── uv_coordinator.py       # UvCoordinator: NEA hourly UV index + exposure bands
├── radar_coordinator.py    # RadarCoordinator: NEA rain radar PNGs (70/240/480 km)
├── radar_image.py          # Pure-Pillow compositing of radar onto NEA base maps (BASEMAPS)
├── sp_account_coordinator.py # SpAccountCoordinator: one per SP e-account subentry (usage, bills, stats)
├── sp_account_sensor.py    # SP e-account sensors built from mapper SensorSpecs (added dynamically)
├── sp_account/             # Vendored from maci0/home-assistant-sp (MIT, see its LICENSE); no HA imports
│   ├── client.py           # Sync urllib Auth0/Jarvis/Njord client (runs in the executor), MFA, cooldown
│   ├── const.py / models.py / mapper.py / history.py  # constants, dataclasses, specs, statistics
│   └── LICENSE             # Upstream MIT licence: must ship with the vendored code
├── camera.py               # Camera entities (checkpoint traffic cameras + rain radar)
├── calendar.py             # Calendar entity (Singapore public holidays)
├── event.py                # Event entities: COE new results, MRT/LRT service alerts
├── weather.py              # Weather entities (one per Singapore forecast area)
├── config_flow.py          # UI config flow (name; single instance), options flow, SP account subentry flow
├── quality_scale.yaml      # Self-assessed HA quality scale rules (hassfest skips it for custom integrations)
├── diagnostics.py          # "Download diagnostics": coordinator status + parsed data
├── sensor.py               # Sensor entities (tariff, COE, weather, PSI, trains, flood, dengue, lightning, UV)
├── manifest.json           # Integration metadata; declares beautifulsoup4 dep, single_config_entry
├── icons.json              # Icon translations (entity icons live here, not in _attr_icon)
├── strings.json            # Config flow UI strings + entity names + enum state names
└── translations/
    ├── en.json             # English translations (mirrors strings.json)
    └── zh-Hans.json        # Simplified Chinese: same keys + placeholders (tests/test_translations.py)

tests/
├── conftest.py                  # Mocks HA modules so tests run without installing homeassistant
├── test_init.py                 # Domain constant check + async_setup_entry wiring tests
├── test_config_flow.py          # Config flow schema check
├── test_coordinator.py          # SP Group parser unit tests + coordinator HTTP mock tests
├── test_coe_coordinator.py      # COE parser unit tests + coordinator HTTP mock tests
├── test_holiday_coordinator.py  # MOM parser unit tests + coordinator HTTP mock tests
├── test_weather_coordinator.py  # Weather coordinator parser + HTTP mock tests
├── test_train_coordinator.py    # Train status parser + HTTP mock tests
├── test_psi_coordinator.py      # PSI parser, band mapping + HTTP mock tests
├── test_flood_coordinator.py    # Flood alert parser, Alert/Cancel tracking, HTTP mock tests
├── test_traffic_cameras.py      # Traffic images parser, coordinator, camera entity + image cache
├── test_dengue_coordinator.py   # Dengue GeoJSON parser, distance math, coordinator, sensors
├── test_lightning_coordinator.py # Lightning parser, rolling window, coordinator, sensors
├── test_uv_coordinator.py       # UV parser (v1/v2), exposure bands, coordinator, sensors
├── test_sp_account.py           # SP account coordinator, sensors, flow helpers, reload listener
├── sp_account/                  # Vendored upstream client/mapper/history tests + recorded fixtures
├── test_radar.py                # Radar parser, per-range fallback, radar camera entity + cache
├── test_calendar.py             # Calendar event and range query tests
├── test_sensor.py               # Sensor value, unit, attributes, unique_id, None-safety
├── test_weather.py              # Weather entity condition mapping + forecast tests
├── test_diagnostics.py          # Diagnostics serialization
├── test_datagov.py              # Limiter pacing vs the measured limit, 429 pause/retry, API key scoping
├── test_options.py              # Option helpers, sources off, health monitor, event entities
├── test_translations.py         # Every translations/*.json mirrors strings.json keys + placeholders
└── test_e2e.py                  # Live scrape tests (run with -m e2e, skipped in CI by default)

tests_ha/                        # Runs against a REAL Home Assistant (no module mocks)
├── conftest.py                  # pytest-homeassistant-custom-component + canned HTTP responses
├── test_integration.py          # Full entry setup/unload, config flow, diagnostics
├── test_options.py              # Options form over HTTP, sources off + reload, API key header, repairs, events
├── test_sp_account.py           # SP subentry flow over HTTP (schema serialization), MFA, repairs
└── test_sp_account_statistics.py # SP Energy statistics land in a real recorder

.github/workflows/tests.yml   # CI: unit tests, real-HA compat matrix, e2e scrape, ruff lint

blueprints/automation/singapore/  # Importable automation blueprints (README "Import" buttons)
tests_ha/test_blueprints.py      # Runs every blueprint in real HA (one test per blueprint, required)
examples/dashboard.yaml          # Built-in-cards dashboard shown in images/hero.jpg
examples/sp_account_dashboard.yaml # SP account dashboard (built-in cards); tests_ha checks every
                                 # entity/statistic in it exists (test_sp_account_statistics.py)
                                 # docs/mockups/sp_account.html mirrors it → images/sp-account-dashboard.png
docs/mockups/                    # HTML sources for the README images (node build.mjs → images/*.png)
├── fetch_cameras.py             # uv run: pulls real checkpoint stills into docs/mockups/cameras/
└── fetch_radar.py               # uv run: renders real radar (via radar_image.py) into docs/mockups/radar/
```

README images must only show what Home Assistant really renders from this integration's
entities with built-in cards (no custom cards, invented attributes or reworded states), and
`hero.html` must stay in sync with `examples/dashboard.yaml` (and `sp_account.html` with `examples/sp_account_dashboard.yaml`). Camera stills are real LTA images,
not illustrations.

Entry data is stored on `entry.runtime_data` as a `SingaporeData` dataclass (not
`hass.data[DOMAIN]`, which is the legacy pattern). Each field is named after its source key
in `const.SOURCES` and is **None when that source is turned off in the options**, so every
platform must check before creating entities:
```python
@dataclass
class SingaporeData:
    tariff: SPGroupCoordinator | None
    coe: CoeCoordinator | None
    weather: SingaporeWeatherCoordinator | None
    holiday: PublicHolidayCoordinator | None
    train: TrainStatusCoordinator | None
    psi: PsiCoordinator | None
    flood: FloodAlertCoordinator | None
    traffic: TrafficImagesCoordinator | None
    dengue: DengueClusterCoordinator | None
    lightning: LightningCoordinator | None
    uv: UvCoordinator | None
    radar: RadarCoordinator | None
    sp_accounts: dict[str, SpAccountCoordinator]  # keyed by subentry id
    options: dict  # the options this setup used; a change reloads the entry
    def coordinators(self) -> dict[str, DataUpdateCoordinator]: ...  # enabled ones

type SingaporeConfigEntry = ConfigEntry[SingaporeData]
```
Every coordinator takes the config entry (`Coordinator(hass, entry)`) and passes
`config_entry=` to `DataUpdateCoordinator`. This is required, not cosmetic:
`SingleCoordinatorWeatherEntity` uses `coordinator.config_entry` to schedule forecast
updates, and HA otherwise falls back to a deprecated ContextVar lookup.
Platforms read it via `entry.runtime_data.<coordinator>` (see `sensor.py`, `weather.py`,
`calendar.py`). The COE refresh-time unsubscribe callable is registered with
`entry.async_on_unload(unsub_coe)` instead of being stored manually and unsubscribed in
`async_unload_entry`.

## Options flow

`SingaporeOptionsFlow` (Configure button) has three fields; all serializable selectors:

- `sources` — multi-select of `SOURCES` (labels under `selector.sources` in strings.json).
  Stored inverted as `options["disabled_sources"]`, so a source added in a later release is
  on for existing users. `enabled_sources(entry)` reads it. Empty with no SP subentries is
  rejected (`no_sources`).
- `api_key` — optional data.gov.sg key, sent as `x-api-key` by `data_gov_headers(entry)` on
  every data.gov.sg request (COE, weather, PSI, flood, traffic, dengue, lightning, UV, radar);
  **never** to SP Group, MOM or mytransport.sg. data.gov.sg answers 200 even for a bogus key,
  so it is not validated. Diagnostics report `has_api_key` only.
- `polling` — `normal` / `reduced`. Each coordinator passes
  `update_interval=scaled_interval(config_entry, UPDATE_INTERVAL)`; reduced multiplies
  sub-hourly intervals by 3 (daily/6-hourly ones are unchanged).

Saving options fires the update listener; `_async_entry_updated` reloads when
`entry.options != runtime_data.options` (before the SP-subentry check). A new data source
must be added to `SOURCES`, `SOURCE_NAMES`, `_COORDINATORS` in `__init__.py`, a
`SingaporeData` field, `selector.sources.options` in strings.json, and use both helpers.

Weather, holidays and trains are the blocking first refreshes (`_BLOCKING_SOURCES`); every
other source loads in the background.

## data.gov.sg rate limits (datagov.py)

Measured 2026-11-15 without a key: `api-open.data.gov.sg` allows ~6 requests per ~10 s per
client **across all endpoints**, then 429s everything for ~10 s. The 429 has no `Retry-After`;
the body says "try again in 10 seconds". `api.data.gov.sg` (v1) didn't throttle 40 in a row.

Every data.gov.sg request goes through `data_gov_get(hass, session, url, **kwargs)` (an
`async with` drop-in for `session.get`) or, in the weather coordinator, `_fetch_with_retry(...,
limiter=limiter_for(hass, url))`. One `HostLimiter` per host lives in `hass.data`, shared by
all coordinators:
- `api-open` is paced: burst `_BURST` (2), then one per `_INTERVAL` (3 s), ≤5 per 10 s
  (GCRA: the slot is booked without awaiting, then one sleep). Other hosts aren't paced.
- A 429 (any host) → `retry_seconds()` (Retry-After, else the body's "try again in N
  seconds", else 10 s, cap 60) → `pause()` the **whole host**, retry up to `MAX_RETRIES` (2);
  the last 429 goes back to the caller (transient: last-known data).
- `x-api-key` is stripped for non-data.gov.sg hosts (dengue's file is a presigned S3 URL).
- Tests: `tests/conftest.py` has an autouse fake clock (`datagov_time`: sleeps advance it and
  are recorded); `tests_ha/conftest.py` makes the sleeps no-ops. Don't call `asyncio.sleep`
  for 429s in coordinators; don't add per-coordinator 429 loops.
- `test_startup_burst_stays_under_the_measured_limit` replays startup + 30 min of polling
  against that limit; it fails on the earlier, too-fast pacing (burst 3 / 2.5 s), which got a
  live 429.

## Repairs for failing sources

`health.py` checks the enabled coordinators hourly (`async_track_time_interval`). A source
whose `last_update_success` has been False for `FAILING_THRESHOLD` (24 h) gets a
`source_failing_<source>` issue (placeholders `source`, `error`, `issue_tracker`); the next
success, turning the source off, or removing the entry deletes it. Transient network errors
don't trigger it because coordinators serve last-known data through those (see Key
Conventions); it catches parse failures and sources that never loaded.

## Event entities

`event.py`: `event.coe_new_results` fires `new_results` once per bidding exercise. It
compares `"{month} #{bidding_no}"` with the `exercise` attribute EventEntity restored, so
results published while HA was down still fire; a fresh install only records the baseline.
`event.mrt_lrt_service_alert` fires `disruption` / `planned` / `resolved` per line from
`train_transitions(previous, current)`; the first poll is only a baseline. Event entities are
on the COE and MRT/LRT devices; names/states under `entity.event` in strings.json.

## Sensors

Entity IDs below are what a **new install** gets: with `_attr_has_entity_name = True`, HA
builds the ID from device name + entity name (e.g. device *Energy* + *Electricity Tariff*
→ `sensor.energy_electricity_tariff`). Installs created before v0.3.0 (the naming
migration) keep their legacy `sensor.singapore_*` IDs because the unique IDs didn't change.
`tests_ha/test_integration.py` asserts the new-install IDs.

### SP Group Utility Tariffs

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.energy_electricity_tariff` | Energy Electricity Tariff | ¢/kWh | Total residential electricity tariff (with GST) |
| `sensor.energy_solar_export_price` | Energy Solar Export Price | ¢/kWh | Tariff minus network costs |
| `sensor.energy_gas_tariff` | Energy Gas Tariff | ¢/kWh | Piped natural gas tariff (with GST) |
| `sensor.energy_water_tariff` | Energy Water Tariff | SGD/m³ | Water tariff, lower residential tier (≤40 m³, with GST) |

### COE Bidding Results

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.coe_category_a` | COE Category A | SGD | Cars ≤1600cc / ≤97kW electric |
| `sensor.coe_category_b` | COE Category B | SGD | Cars >1600cc / >97kW electric |
| `sensor.coe_category_c` | COE Category C | SGD | Goods vehicles and buses |
| `sensor.coe_category_d` | COE Category D | SGD | Motorcycles |
| `sensor.coe_category_e` | COE Category E | SGD | All except motorcycles |

### NEA Realtime Weather Readings (Collection 1459)

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.weather_temperature` | Weather Temperature | °C | Aggregated air temperature |
| `sensor.weather_humidity` | Weather Humidity | % | Aggregated relative humidity |
| `sensor.weather_wind_speed` | Weather Wind Speed | km/h | Aggregated wind speed |
| `sensor.weather_wind_bearing` | Weather Wind Bearing | ° | Aggregated wind direction |
| `sensor.weather_rainfall` | Weather Rainfall | mm | Aggregated rainfall |

### NEA Air Quality / PSI

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.air_quality_psi` | PSI | — (`AQI`) | 24-hour PSI, highest region; `band` + `regions` attributes |
| `sensor.air_quality_psi_<region>` | PSI \<Region\> | — (`AQI`) | Per-region 24-hour PSI; carries `latitude`/`longitude` + every reading for the region (map card) |
| `sensor.air_quality_pm2_5` | PM2.5 | µg/m³ | 24-hour PM2.5, highest region |
| `sensor.air_quality_pm10` | PM10 | µg/m³ | 24-hour PM10, highest region |
| `sensor.air_quality_ozone` | Ozone | µg/m³ | 8-hour max O3, highest region |
| `sensor.air_quality_nitrogen_dioxide` | Nitrogen Dioxide | µg/m³ | 1-hour max NO2, highest region |
| `sensor.air_quality_sulphur_dioxide` | Sulphur Dioxide | µg/m³ | 24-hour SO2, highest region |
| `sensor.air_quality_carbon_monoxide` | Carbon Monoxide | mg/m³ | 8-hour max CO, highest region (no device class: HA 2025.4 CO only accepts ppm) |

### Weather Entities (collection 1456)

One `weather` entity is created per forecast area (e.g. `weather.weather_bedok`; the name is
the raw area via `_attr_name`, under the *Weather* device). Current condition comes from the
area's 2-hour forecast; current temperature/humidity/wind from the collection 1459 readings.
`FORECAST_DAILY` comes from the Singapore-wide 4-day outlook (same for every area);
`FORECAST_HOURLY` from the 24-hour forecast for the area's region (see below).

### Train Status Sensors

| Entity ID | Name | Description |
|-----------|------|-------------|
| `sensor.mrt_lrt_status` | MRT/LRT Status | Overall MRT/LRT network status (`normal` / `planned` / `disruption`) |
| `sensor.mrt_lrt_<line>_status` | MRT/LRT \<Line\> Status | Per-line status for NSL, EWL, NEL, CCL, DTL, TEL, BPLRT, SKLRT, PGLRT |

Train status sensors are `SensorDeviceClass.ENUM` with `options = TRAIN_STATUS_OPTIONS`;
state display names live under `entity.sensor.<key>.state` in `strings.json`, state
icons in `icons.json`. A sensor value outside `options` is an error in HA, so return
`None` rather than ad-hoc strings like `"unknown"`.

Train status data is fetched every **5 minutes** via a POST to the LTA DataMall AEM servlet
(the page itself is JS-rendered and returns only a "Loading…" shell when scraped as HTML).

## Development Setup

No full Home Assistant installation required. Tests mock all HA modules via `conftest.py`.

Run tests with:
```bash
python3 -m pytest tests/ -v -m "not e2e"
```
(Note: `pip install` requires `--break-system-packages` on macOS system Python. Use
`python3 -m pytest` directly — `pytest` binary may not be on PATH.)

## Running Tests

Unit tests (no network, always fast; HA is fully stubbed by `tests/conftest.py`):

```bash
pytest tests/ -v -m "not e2e"
```

Live e2e tests (hit real external APIs — run locally when a scraper may have broken):

```bash
pytest tests/test_e2e.py -v -s -m e2e
```

Real Home Assistant compatibility tests (`tests_ha/`). These catch HA API changes the
stubbed unit tests cannot. Each `pytest-homeassistant-custom-component` release pins an
exact HA version; CI runs the minimum (`0.13.232` → HA 2025.4.0, Python 3.13), current
stable (`0.13.367` → HA 2026.9.4, Python 3.14) and newest (non-blocking) harness:

```bash
uv venv --python 3.14 .venv-ha
uv pip install --python .venv-ha/bin/python pytest-homeassistant-custom-component==0.13.367 "beautifulsoup4>=4.12.3" PyTurboJPEG
.venv-ha/bin/python -m pytest tests_ha -v
```

When bumping the minimum HA version, update `hacs.json` and the "minimum" matrix entry in
`.github/workflows/tests.yml` together. Bump the "stable" entry as new HA releases ship.

With coverage:

```bash
pytest tests/ -v -m "not e2e" --cov=custom_components/singapore --cov-report=term-missing
```

## How the COE Fetcher Works

`coe_coordinator.py` calls the data.gov.sg CKAN API:

```
https://data.gov.sg/api/action/datastore_search
  ?resource_id=d_69b3380ad7e51aff3a7dcc84eba52b8a
  &limit=10
  &sort=month%20desc%2Cbidding_no%20desc
```

The response contains records with fields `month`, `bidding_no`, `vehicle_class`, `quota`,
`bids_success`, and `premium`. The coordinator picks the most recent bidding exercise
(first record's `month` + `bidding_no`) and builds a `CoeData.premiums` dict keyed
by category letter (`"A"`–`"E"`).

The coordinator has `update_interval=None` (no automatic polling). Instead, `__init__.py`
registers an `async_track_time_change` callback that triggers a refresh every day at
**19:30** — after LTA typically publishes bidding results.

## How the SP Group Scraper Works

`coordinator.py` fetches `https://www.spgroup.com.sg/our-services/utilities/tariff-information`
with browser-like headers. The full corpus searched includes visible page text and all
inline `<script>` content (covering both classic HTML and Next.js `__NEXT_DATA__` JSON).

Parsing uses three strategies per value, tried in order:

1. **Banner format** (`_extract_banner_cents_kwh`) — matches SP Group's current layout:
   `"29.72 cents/kWh 27.27 cents/kWh (w/o GST) ELECTRICITY TARIFF"`.
   Always returns the with-GST price (the first value).

2. **Tiered water format** (`_extract_water_tiered`) — matches `"$1.56 or $1.97/m"`.
   Returns the lower residential tier (≤40 m³).

3. **Keyword + float search** (`_extract_by_keywords`) — fallback for classic table
   layouts; scans for a float within 80 non-digit chars of a keyword.

Quarter is inferred from date strings in two formats:
- Full: `"1 January 2025 to 31 March 2025"`
- Abbreviated: `"wef 1 Apr - 30 Jun 26"` (maps via `_MONTH_ABBR_TO_Q`)

Solar export price = total electricity tariff − network costs.

The tariff first refresh runs as a background task (like COE/PSI), so an unreachable
SP Group site (e.g. the TLS handshake failures in issue #44) never blocks integration setup;
the tariff sensors just stay unavailable until a fetch succeeds.

## How the Weather Coordinator Works

`weather_coordinator.py` fetches (every **10 minutes**):

- 2-hour forecast areas from `https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast`
- realtime readings from collection 1459 endpoints (`air-temperature`, `relative-humidity`,
  `wind-speed`, `wind-direction`, `rainfall`)

HTTP is handled via HA's **`async_get_clientsession`** (the standard aiohttp session managed by
Home Assistant) with manual retry on HTTP 429.  The coordinator uses
`aiohttp.ClientTimeout` for request timeouts and an `asyncio.Semaphore`
(`_READINGS_CONCURRENCY = 2`) to cap parallel readings requests, avoiding 429s
from data.gov.sg.

- `_READINGS_CONCURRENCY = 2` — `asyncio.Semaphore` caps the 5 parallel readings requests
  to 2 in-flight at a time.
- `_fetch_with_retry()` — goes through the shared `datagov` limiter (pacing on api-open,
  host-wide pause + up to 2 retries on 429); see "data.gov.sg rate limits".

Forecast parsing supports both common payload styles:
- `items[0].forecasts` (legacy shape)
- `data.records[0].periods[0].regions` (newer shape)

The 2-hour forecast sets each area entity's current condition. The four-day outlook
(see below) feeds `_async_forecast_daily()`; the 24-hour forecast feeds
`_async_forecast_hourly()`.

### Forecast Subscription Pattern

In HA 2024.3+, weather forecast data is subscription-based. `SingaporeAreaWeatherEntity`
subclasses HA's `SingleCoordinatorWeatherEntity`, which calls `async_update_listeners`
after every coordinator refresh so forecast subscribers (the frontend weather card) get
new data. Implement forecasts as the synchronous `@callback _async_forecast_daily()`;
do not override `_handle_coordinator_update` to push listeners manually.

### Collection 1456 — Other Forecast APIs

Both are integrated: `four-day-outlook` → daily forecasts, `twenty-four-hr-forecast` → hourly:

| Endpoint | Coverage | Periods | Has temp/humidity? |
|----------|----------|---------|-------------------|
| `twenty-four-hr-forecast` | 5 regions (N/S/E/W/central) | ~4 × 6-hour periods | No |
| `four-day-outlook` | Singapore-wide | 4 daily forecasts | Yes (high/low ranges) |

`twenty-four-hr-forecast` (v2: `data.records[0].periods[]` with `timePeriod{start,end}` and
`regions{<region>: {code, text}}`) is parsed into `RegionalForecastPeriod`s. Each 2-hour
area gets a `region` from `_area_regions()`: the nearest PSI region centre
(`DEFAULT_REGION_LOCATIONS`) to the area's `area_metadata[].label_location`. Hourly
forecast = one entry per period that hasn't ended, condition only (NEA gives no per-period
temperature). A non-200 24-hour response is soft (logged, `regional_forecast = None`), like
the four-day outlook.

## Blueprints

`blueprints/automation/singapore/*.yaml` are automation blueprints linked from the README
with my.home-assistant.io import buttons (which point at `main`). Each sets
`homeassistant.min_version` to the minimum supported HA version and takes the notify action
as a text input (default `notify.notify`). Template triggers must read inputs through
`trigger_variables` (plain `variables` are evaluated after the trigger). Blueprints:
lightning_nearby, mrt_line_disruption, flood_alert, haze_purifier, coe_results,
rain_forecast (weather entity turns wet), uv_high, dengue_nearby, holiday_tomorrow
(`calendar.get_events` for tomorrow at a set time), sp_bill (new bill + due-date reminder;
blueprint variables are stored as plain values, so a date must be re-parsed in each template). `tests_ha/test_blueprints.py` loads each one
with `use_blueprint` and drives sensor states, so it runs on every HA version in the CI
matrix; `test_every_blueprint_is_tested` fails if a blueprint has no test.

#### four-day-outlook payload caveat (2026-04-06)

The live `v2/real-time/api/four-day-outlook` payload may arrive in either shape:

- Legacy-ish: `items[0].forecasts[]` with direct daily entries.
- Current nested shape: `data.records[0].forecasts[]`, where each forecast row can include
  `timestamp` and object-style forecast text:
  - `forecast.text` (canonical short condition, e.g. `Thundery Showers`)
  - `forecast.summary` (longer narrative)

Parser guidance used in this repo:

- Prefer per-row `date`; else derive from per-row `timestamp`; else fall back to parent
  `record.date`.
- Prefer `forecast.text` over `forecast.summary` for condition mapping consistency.
- Support both `relative_humidity` and `relativeHumidity` field names.

## How the Train Status Coordinator Works

`train_coordinator.py` POSTs to the AEM/LTA DataMall servlet every **5 minutes**:

```
POST https://www.mytransport.sg/content/ltagov/en/map/train/jcr:content/left-menu/ltaDatamallAPI.ltaDatamallAPI.POST.html
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest
Referer: https://www.mytransport.sg/trainstatus#

serviceName=LTAGOVTrainServiceAlerts&param=
```

Response shape: `{ "value": { "Status": int, "AffectedSegments": [...], "Message": [...] } }`
- `AffectedSegments` — real-time disruptions (line is currently down)
- `Message[].Content` — planned/informational notices, format: `"HH:MM-{LINE}-{description}"`

**Do not attempt to scrape the HTML page** — it is fully JS-rendered.

`TrainStatusData` contains:
- `status` — `"normal"`, `"planned"`, or `"disruption"` (overall network)
- `details` — all message content joined with ` | ` (empty string when normal); exposed as `details` attribute on `sensor.mrt_lrt_status`
- `line_statuses` — dict mapping each line name to `"normal"`, `"planned"`, or `"disruption"`

## How the PSI Coordinator Works

`psi_coordinator.py` GETs `https://api-open.data.gov.sg/v2/real-time/api/psi` every
**15 minutes** (matching the data.gov.sg refresh rate). Payload shape (v2):

```
{"code": 0, "data": {"regionMetadata": [{"name": "West", "labelLocation": {...}}],
  "items": [{"timestamp": "...", "readings": {"psi_twenty_four_hourly": {"north": 3, ...}, ...}}]}}
```

- Every numeric reading key is kept in `PsiData.readings[key][region]` (regions:
  north/south/east/west/central; region names are lowercase in `readings`).
- `PsiData.national[key]` = the API's `national` value if present (v1 shape), else the max
  across regions (NEA headlines the highest regional PSI).
- `PsiData.region_locations` comes from `regionMetadata` (names are capitalised there,
  lowercase-matched) with `DEFAULT_REGION_LOCATIONS` fallbacks; the regional PSI sensors
  expose these as `latitude`/`longitude` so they render on HA Map cards.
- A payload with no `psi_twenty_four_hourly` raises `UpdateFailed`.
- The first refresh runs as a background task (like COE) so a PSI outage never blocks
  integration setup.

## How the Flood Alert Coordinator Works

`flood_coordinator.py` GETs `https://api-open.data.gov.sg/v2/real-time/api/weather/flood-alerts`
every **2 minutes** (no API key). Exposed as `sensor.pub_flood_alerts` (state = active
alert count; `alerts` / `locations` attributes).

Payload is CAP-style: `data.records[]` (newest first, ~25 records ≈ last 50 min), each with
`datetime` and `item`. Routine records have empty `readings` and no `msgType`. Alert records
carry `item.identifier`, `item.msgType` (`Alert` / `Cancel`), `item.references`
(`"sender, identifier, sentTime"` of the alert being cancelled) and `readings[]` with
`description`, `area.areaDesc`, `area.circle` (`[lat, lng, broadcast_radius_km]`),
`severity`, `urgency`, `headline`, `instruction`. The full OpenAPI spec is the dataset's
download (`d_f1404e08587ce555b9ea3f565e2eb9a3`).

Because the response only covers a short window, the coordinator keeps **state across polls**
(`self._active`, keyed by identifier): messages are applied oldest-first, an Alert adds,
a Cancel removes any active identifier contained in its `references`. Alerts with no Cancel
are dropped after `ALERT_MAX_AGE` (3 h). The first refresh runs as a background task so a
data.gov.sg 429 can't block setup (same for the traffic, dengue and lightning coordinators).

## How the Traffic Camera Coordinator Works

`traffic_coordinator.py` GETs `https://api.data.gov.sg/v1/transport/traffic-images` every
**2 minutes** (the v2 `api-open` route doesn't exist for this dataset). Payload:
`items[0].cameras[]` with `camera_id`, `image` (JPEG URL on images.data.gov.sg), `timestamp`,
`location`. Only IDs in `CHECKPOINT_CAMERAS` (2701, 2702 Woodlands; 4703, 4713 Tuas) are kept.
The feed sometimes omits cameras (2702 was missing on 2026-11-07), so `camera.py` reports
a camera as unavailable when its ID is absent. `async_camera_image` downloads the JPEG and
caches it until the URL changes.

## How the Dengue Cluster Coordinator Works

`dengue_coordinator.py` downloads dataset `d_dbfabf16158d1b0e1c420627c0819168` (GeoJSON)
every **6 hours**: GET `.../v1/public/api/datasets/{id}/poll-download` → `data.url`
(signed S3 link; if absent, call `initiate-download`), then GET the file. Each feature has
`properties.LOCALITY`, `properties.CASE_SIZE`, and Polygon/MultiPolygon geometry in lon/lat.
`nearest_cluster()` computes the distance from `hass.config` home lat/lon to the nearest
polygon edge (0 if inside; ray casting + local equirectangular projection), which feeds
`sensor.nea_nearest_dengue_cluster`. Zero features is valid (no active clusters).
`_get_json` goes through `data_gov_get` (shared limiter; 429 retried there). While no data has loaded, a failed refresh sets `update_interval` to
`RETRY_INTERVAL` (10 min) instead of waiting the full 6 h; the next success restores it.

## How the Lightning Coordinator Works

`lightning_coordinator.py` GETs `https://api-open.data.gov.sg/v2/real-time/api/weather?api=lightning`
every **2 minutes** (dataset `d_08238953fe0f6dd13f10714ebfbcb9f9`, MSS Lightning Detection
System). Payload: `data.records[]` with `datetime` and `item.readings[]`, each with
`location.latitude` / `location.longitude` (strings), `type` (`G` = cloud-to-ground,
`C` = cloud-to-cloud, mapped via `STRIKE_TYPES`), `text`, and `datetime` (variable fractional
seconds, e.g. `.96+08:00`). No lightning means empty `readings`; a missing `records` list
raises `UpdateFailed`. Coverage extends beyond Singapore (strikes near Batam are common).

Each response only holds the latest observation, so the coordinator keeps a set of
`LightningStrike`s (frozen dataclass, so duplicates across polls collapse) and drops any older
than `STRIKE_WINDOW` (30 min), also on failed polls. `sensor.nea_lightning_strikes` = count in
window; `sensor.nea_nearest_lightning` = haversine km from `hass.config` home to the nearest
strike (`None` when there are none) plus `strikes_within_10_km`. Both sit on the shared *NEA*
device with the dengue sensors (`_nea_device_info` in `sensor.py`).

## How the UV Coordinator Works

`uv_coordinator.py` GETs `https://api-open.data.gov.sg/v2/real-time/api/uv` every
**15 minutes**. NEA publishes one reading per hour from 7 am to 7 pm, ~10 min after the hour.
Payload (v2): `data.records[0].index[{hour, value}]`, newest first; v1 (`items[0].index[{timestamp,
value}]`) is also parsed. `UvData.readings` is today's readings oldest-first (`latest`, `peak`
properties; ties keep the earliest hour). A payload with no `index` list raises `UpdateFailed`;
an empty list is valid. `sensor.nea_uv_index` (unit `UV_INDEX`, measurement) and
`sensor.nea_uv_exposure` (ENUM, `UV_EXPOSURE_OPTIONS`: low/moderate/high/very_high/extreme,
NEA's WHO-based bands via `uv_exposure()`) sit on the shared *NEA* device. First refresh runs
in the background.

## How the SP Group e-account support works

Credit: built on [maci0/home-assistant-sp](https://github.com/maci0/home-assistant-sp) by
Marcel W. Wysocki (MIT). Keep the attribution (README "Credits", `sp_account/LICENSE`, file
headers) when touching this code.

- `sp_account/` is vendored from upstream v1.8.0 with minimal changes (listed in its
  `__init__.py`) so upstream fixes can be diffed in. It must not import Home Assistant
  (`tests/sp_account/test_layering.py`). Its client is synchronous `urllib` and runs via
  `hass.async_add_executor_job`; this is the one deliberate exception to the aiohttp
  convention (rewriting a 2,000-line protocol client was not worth the risk).
- Each e-account is a **config subentry** (`subentry_type = "sp_account"`) of the single
  `singapore` entry, offered by `async_get_supported_subentry_types`. Subentry data holds the
  username and Auth0 tokens, never the password.
- `SpAccountSubentryFlow`: `user` (credentials) → `mfa` when Auth0 says `mfa_required` (TOTP
  preferred, SMS/email OOB otherwise) → subentry. `reconfigure` is the same sign-in form
  (unique id must match).
- No price input (upstream had an "electricity price" option): the cost series is priced from
  the integration's own tariff scrape, `SpAccountCoordinator.electricity_price` =
  `SPGroupCoordinator.data.electricity_price / 100` (¢ → SGD/kWh). `__init__.py` adds a tariff
  coordinator listener that calls `async_tariff_updated()` on each account, which re-imports
  the cost series when the price changed. No tariff yet → the cost series is skipped.
- **Form schemas must be serializable** (upstream issue #21: a bare function validator made HA
  return 500 "Unable to convert schema" when the form loaded). Use selectors / plain types only
  and run custom rules in the step handler, reporting field errors.
  `tests_ha/test_sp_account.py::test_subentry_forms_serialize_over_http` loads every form via
  the HTTP API to guard this.
- `SpAccountCoordinator` polls every 30 min (first refresh in the background). An Auth0
  rejection raises `UpdateFailed` and opens the `sp_account_auth_failed_<subentry_id>` repair
  issue (never `ConfigEntryAuthFailed`, which would reauth the whole entry and take every other
  sensor down); success deletes it. Refreshed tokens are persisted with
  `async_update_subentry`, which fires the entry update listener: `_async_entry_updated`
  reloads only when the set of SP subentries changed, otherwise it calls
  `coordinator.async_subentry_updated()` (new tokens → new client + refresh).
- Energy statistics are imported with `async_add_external_statistics` as
  `singapore:<premise_id>_<key>` (`electricity`, `electricity_cost`, `gas`, `water`,
  `last_bill`). `water` (billed months, m³ only) is ours, not upstream's.
  `unit_class` is only included when the running HA's `StatisticMetaData` has it (2025.4
  rejects it, newer HA warns without it). The poll passes its readings into the import task
  because HA starts tasks eagerly, before `coordinator.data` is set.
- Sensor translation keys are the mapper keys prefixed with `sp_` (`entity.sensor.sp_*`);
  unique ids are `{entry_id}_sp_{premise_id}_{key}`; entities are added with
  `config_subentry_id`. Diagnostics report SP accounts as flags/counts only (no address,
  account number, premise id, username or tokens).

## How the Rain Radar Coordinator Works

`radar_coordinator.py` GETs `https://api-open.data.gov.sg/v2/real-time/api/weather-radar-images/{range}`
for each range in `RADAR_RANGES` (`70km`, `240km`, `480km`) every **5 minutes**, sequentially
(data.gov.sg rate-limits bursts). Dataset `d_418e9ac3414fd927b7405631e0a7bc82` (beta); its
OpenAPI spec is the dataset download. Payload: `data.records[0]` with `timestamp` and
`image.url` (presigned S3 PNG, **expires ~20 min** after the call, so the URL changes every poll)
plus `data.boundaryBox` (EPSG:4326 upper-left / lower-right) and `data.projection.center`.

A range whose fetch fails (HTTP error/429, network) keeps its previous `RadarImage`; only when
every range fails with nothing cached does the update fail. A payload with no record/image URL
raises `UpdateFailed` (parse errors propagate). `SingaporeRainRadarCamera` (`camera.py`, on the
*Weather* device) caches its image keyed by capture `timestamp` (+ whether the base map was
used), not URL, because the URL is re-signed on every poll.

The radar PNGs are transparent overlays with no map. `radar_image.py` (Pillow only, no HA
imports, so `docs/mockups/fetch_radar.py` loads it by path) draws 70 km and 240 km on the base
maps from https://www.nea.gov.sg/weather/rain-areas (`/assets/images/map/base-853.png`,
853 × 479; `240km-v2.jpg`, 853 × 853), replicating that page: both images at the same width,
top-left aligned. The crop boxes in `BASEMAPS` were found by matching NEA's own overlays to
data.gov.sg's image of the same capture (pixel-exact): NEA's 240 km overlay *is* the full
480 × 480 image; its 70 km overlay is the crop `(83, 193, 300, 313)`. NEA has no 480 km map, so
that camera serves the bare PNG and is disabled by default
(`_attr_entity_registry_enabled_default` is False when a range has no `BASEMAPS` entry). Base maps are fetched at runtime (once, retried after
`BASEMAP_RETRY_SECONDS` on failure) rather than bundled, because they are NEA website assets,
not open data. Composited output is JPEG (`content_type` is set per image). Pillow is a Home
Assistant core dependency, so it isn't in `manifest.json`; it is in `requirements_test.txt`.
data.gov.sg returns 403 to urllib's default User-Agent (aiohttp's is fine), which is why
`fetch_radar.py` sets one.

### Camera platform test dependency

HA's `camera` component imports `turbojpeg`, which a real HA install provides but the
`pytest-homeassistant-custom-component` harness does not, so `tests_ha` installs
`PyTurboJPEG` explicitly (CI workflow + the local command above).

### Calendar Entity

| Entity ID | Name | Description |
|-----------|------|-------------|
| `calendar.public_holidays` | Public Holidays | Singapore public holiday all-day events (year >= current year) |

Holiday data source: `https://www.mom.gov.sg/employment-practices/public-holidays`

`holiday_coordinator.py` refreshes every 24 hours and parses holiday rows from MOM.

## Adding a New Sensor

### SP Group tariff sensor
1. Add new field(s) to `TariffData` in `coordinator.py`
2. Parse the new value(s) in `_parse_tariff` — use `_extract_banner_cents_kwh` for
   ¢/kWh values or `_extract_by_keywords` as a fallback
3. Add a new sensor class in `sensor.py`, register it in `async_setup_entry`
4. Add tests in `tests/test_sensor.py` and `tests/test_coordinator.py`

### COE sensor
COE sensors are generated automatically for every category in `COE_CATEGORIES` in
`coe_coordinator.py`. To add a new COE-based sensor with different logic, subclass
`CoordinatorEntity[CoeCoordinator]` in `sensor.py` and register it in `async_setup_entry`.

## Brand Assets — Do Not Remove

`custom_components/singapore/brand/` holds the integration's brand images, which Home Assistant
2026.3 and newer loads for a custom integration: `icon.png` (256×256) and `icon@2x.png` (512×512),
`logo.png` (128 high) and `logo@2x.png` (256 high), and `dark_logo.png` / `dark_logo@2x.png` with
white lettering for the dark theme. All are trimmed PNGs on a transparent background, the sizes
`home-assistant/brands` asks for. The `hacs/action` CI check (`.github/workflows/hacs.yaml`) also
validates them locally, as this repo isn't in `home-assistant/brands`, so **do not delete them**:
removing `brand/` fails the `<Validation brands>` check.

The mark is a red disc (Singapore red, `#EF3340`, the little red dot) holding a white house with a
red dot inside; the logo sets it beside "Singapore" and "for Home Assistant" in Inter. It keeps
clear of the flag, the crescent and stars, and the Merlion, whose use is restricted. The sources are
`images/brand/*.svg`: edit them, then run `cd docs/mockups && node build.mjs brand social.html`
(Inter must be installed), which trims each to its drawing at the sizes above, and redraws the
social preview, whose kicker carries the mark. The README's header is `logo@2x.png`, with
`dark_logo@2x.png` on GitHub's dark theme. The red is `--sg-red` in `docs/mockups/ha.css` too.
`custom_components/singapore/images/icon.png` is a copy of `icon@2x.png`.

## Linting and Formatting

After every code change, always run and fix before committing:

```bash
ruff check custom_components/ tests/ tests_ha/ --fix
ruff format custom_components/ tests/ tests_ha/
```

Both commands must exit cleanly — CI will fail otherwise.

## Key Conventions

- All HA I/O must be `async`; use `async_`-prefixed HA helpers
- Use `async_get_clientsession(hass)` for aiohttp — never create a bare `aiohttp.ClientSession`
- All coordinators use `async_get_clientsession(hass)` for aiohttp requests
- Every data.gov.sg request goes through the shared limiter in `datagov.py`
  (`data_gov_get` / `limiter_for`), which paces api-open.data.gov.sg and handles HTTP 429
  for all coordinators at once
- Entity unique IDs must be stable: `{entry_id}_{suffix}`
- Entities use `_attr_attribution` for data credit, and icons come from `icons.json`
  (icon translations) keyed by translation key — do not set `_attr_icon`
- Platforms use `AddConfigEntryEntitiesCallback` (not the legacy `AddEntitiesCallback`)
- Entities use `_attr_has_entity_name = True` with `_attr_translation_key`
  (+ `_attr_translation_placeholders` where dynamic) and `DeviceInfo` (not a raw dict) with
  `entry_type=DeviceEntryType.SERVICE`. Entity strings live under `entity.sensor.<key>` in
  `strings.json`/`translations/en.json`. Unique IDs are unaffected by naming, so existing
  installs keep their entity IDs across the naming migration; new installs get
  device-prefixed IDs (see "Sensors" above).
- Coordinator `_async_update_data()` exception handling: only catch `(aiohttp.ClientError,
  TimeoutError)` and an explicit `UpdateFailed` raised for a bad HTTP status — those fall back
  to last-known data with a warning (transient network resilience). Do **not** catch bare
  `Exception`: a parsing failure (site markup changed, no data found) must raise `UpdateFailed`
  and propagate so the coordinator reports the update as failed and entities go unavailable,
  rather than silently reporting stale data as "successful" forever.
- `coordinator.py` and `holiday_coordinator.py` parse HTML with BeautifulSoup + regex, which is
  blocking; parsing is offloaded via `await hass.async_add_executor_job(_parse_x, html)` rather
  than run inline on the event loop.
- Always bump `custom_components/singapore/manifest.json` `version` for every PR that changes
  shipped integration behavior/code, so merge-to-main release automation can create the next
  GitHub release tag from that version.
- HACS installs the `singapore.zip` release asset (`hacs.json`: `zip_release` + `filename`),
  which `release.yml` builds with `git archive <tag>:custom_components/singapore` (files at
  the zip root). It feeds the README downloads badge. A release without that asset can't be
  installed via HACS; every `release.yml` run (merge, `workflow_dispatch`, or a release
  published by hand) attaches one, built from its own tag, to any release that lacks it.
- Translations live in `translations/en.json` and must mirror `strings.json`. Other languages
  (`zh-Hans.json`) must have exactly the same keys and `{placeholders}`; when you add a string,
  add its translation too (`tests/test_translations.py` fails otherwise). zh-Hans isn't in
  HA's `NATIVE_ENTITY_IDS`, so entity IDs stay English-derived.
- For quick one-off API/scraping investigations, use `uv run script.py` with inline `# /// script` dependency blocks — avoids venv setup entirely
