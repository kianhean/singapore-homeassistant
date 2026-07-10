# Singapore Home Assistant Integration

A general-purpose HACS integration for Singapore-specific Home Assistant entities.
The integration domain is `singapore`.

## Structure

```
custom_components/singapore/
├── __init__.py             # Entry setup/teardown; creates and stores coordinators
├── coordinator.py          # SPGroupCoordinator: fetches + parses SP Group tariff page
├── coe_coordinator.py      # CoeCoordinator: fetches COE results from data.gov.sg API
├── holiday_coordinator.py  # PublicHolidayCoordinator: fetches + parses MOM holidays
├── weather_coordinator.py  # SingaporeWeatherCoordinator: 2-hour forecasts + collection 1459 readings
├── train_coordinator.py    # TrainStatusCoordinator: scrapes mytransport.sg MRT/LRT status
├── calendar.py             # Calendar entity (Singapore public holidays)
├── weather.py              # Weather entities (one per Singapore forecast area)
├── config_flow.py          # UI config flow (name input)
├── sensor.py               # Sensor entities (tariff + COE + weather readings + train status)
├── manifest.json           # Integration metadata; declares beautifulsoup4 dep
├── strings.json            # Config flow UI strings
└── translations/
    └── en.json             # English translations (mirrors strings.json)

tests/
├── conftest.py                  # Mocks HA modules so tests run without installing homeassistant
├── test_init.py                 # Domain constant check + async_setup_entry wiring tests
├── test_config_flow.py          # Config flow schema check
├── test_coordinator.py          # SP Group parser unit tests + coordinator HTTP mock tests
├── test_coe_coordinator.py      # COE parser unit tests + coordinator HTTP mock tests
├── test_holiday_coordinator.py  # MOM parser unit tests + coordinator HTTP mock tests
├── test_weather_coordinator.py  # Weather coordinator parser + HTTP mock tests
├── test_train_coordinator.py    # Train status parser + HTTP mock tests
├── test_calendar.py             # Calendar event and range query tests
├── test_sensor.py               # Sensor value, unit, attributes, unique_id, None-safety
├── test_weather.py              # Weather entity condition mapping + forecast tests
└── test_e2e.py                  # Live scrape tests (run with -m e2e, skipped in CI by default)

.github/workflows/tests.yml   # CI: three jobs — unit tests, e2e scrape, ruff lint
```

Entry data is stored on `entry.runtime_data` as a `SingaporeData` dataclass (not
`hass.data[DOMAIN]`, which is the legacy pattern):
```python
@dataclass
class SingaporeData:
    tariff: SPGroupCoordinator
    coe: CoeCoordinator
    weather: SingaporeWeatherCoordinator
    holiday: PublicHolidayCoordinator
    train: TrainStatusCoordinator

SingaporeConfigEntry: TypeAlias = ConfigEntry[SingaporeData]
```
Platforms read it via `entry.runtime_data.<coordinator>` (see `sensor.py`, `weather.py`,
`calendar.py`). The COE refresh-time unsubscribe callable is registered with
`entry.async_on_unload(unsub_coe)` instead of being stored manually and unsubscribed in
`async_unload_entry`.

## Sensors

### SP Group Utility Tariffs

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_electricity_tariff` | Singapore Electricity Tariff | ¢/kWh | Total residential electricity tariff (with GST) |
| `sensor.singapore_solar_export_price` | Singapore Solar Export Price | ¢/kWh | Tariff minus network costs |
| `sensor.singapore_gas_tariff` | Singapore Gas Tariff | ¢/kWh | Piped natural gas tariff (with GST) |
| `sensor.singapore_water_tariff` | Singapore Water Tariff | SGD/m³ | Water tariff, lower residential tier (≤40 m³, with GST) |

### COE Bidding Results

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_coe_category_a` | Singapore COE Category A | SGD | Cars ≤1600cc / ≤97kW electric |
| `sensor.singapore_coe_category_b` | Singapore COE Category B | SGD | Cars >1600cc / >97kW electric |
| `sensor.singapore_coe_category_c` | Singapore COE Category C | SGD | Goods vehicles and buses |
| `sensor.singapore_coe_category_d` | Singapore COE Category D | SGD | Motorcycles |
| `sensor.singapore_coe_category_e` | Singapore COE Category E (Open) | SGD | All except motorcycles |

### NEA Realtime Weather Readings (Collection 1459)

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_temperature` | Singapore Temperature | °C | Aggregated air temperature |
| `sensor.singapore_humidity` | Singapore Humidity | % | Aggregated relative humidity |
| `sensor.singapore_wind_speed` | Singapore Wind Speed | km/h | Aggregated wind speed |
| `sensor.singapore_wind_bearing` | Singapore Wind Bearing | ° | Aggregated wind direction |
| `sensor.singapore_rainfall` | Singapore Rainfall | mm | Aggregated rainfall |

### Weather Entities (collection 1456)

One `weather` entity is created per forecast area (e.g. Bedok, Woodlands, Ang Mo Kio).
The entity exposes Home Assistant weather conditions and hourly forecasts approximated
from NEA's 2-hour periods.

### Train Status Sensors

| Entity ID | Name | Description |
|-----------|------|-------------|
| `sensor.singapore_train_status` | Singapore Train Status | Overall MRT/LRT network status (`normal` / `disrupted`) |
| `sensor.singapore_<line>_status` | Singapore \<Line\> Status | Per-line status for NSL, EWL, NEL, CCL, DTL, TEL, BPLRT, SKLRT, PGLRT |

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

Unit tests (no network, always fast):

```bash
pytest tests/ -v -m "not e2e"
```

Live e2e tests (hit real external APIs — run locally when a scraper may have broken):

```bash
pytest tests/test_e2e.py -v -s -m e2e
```

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
  to 2 in-flight at a time, avoiding 429s from data.gov.sg.
- `_fetch_with_retry()` — retries up to `_MAX_RETRIES = 3` times on HTTP 429, respecting
  the `Retry-After` response header (falls back to exponential backoff).

Forecast parsing supports both common payload styles:
- `items[0].forecasts` (legacy shape)
- `data.records[0].periods[0].regions` (newer shape)

Each weather entity converts a single 2-hour interval into two hourly forecast points
for Home Assistant (`t` and `t+1h`) with mapped HA conditions.

### Forecast Subscription Pattern

In HA 2024.3+, weather forecast data is subscription-based. `SingaporeAreaWeatherEntity`
overrides `_handle_coordinator_update` to call `async_update_listeners()` after every
coordinator refresh — without this, forecast subscribers (the HA frontend weather card)
never receive updated data and the spinner stays permanently. `async_update_listeners`
takes a required `forecast_types` argument (a tuple of the forecast types this entity
supports, e.g. `("daily",)` since this entity only declares `FORECAST_DAILY`) — omitting
it raises `TypeError` on every coordinator refresh:

```python
@callback
def _handle_coordinator_update(self) -> None:
    super()._handle_coordinator_update()
    self.hass.async_create_task(self.async_update_listeners(("daily",)))
```

### Collection 1456 — Not Yet Integrated

Two additional NEA forecast APIs in collection 1456 are unused:

| Endpoint | Coverage | Periods | Has temp/humidity? |
|----------|----------|---------|-------------------|
| `twenty-four-hr-forecast` | 5 regions (N/S/E/W/central) | ~4 × 6-hour periods | No |
| `four-day-outlook` | Singapore-wide | 4 daily forecasts | Yes (high/low ranges) |

The 4-day outlook is the most valuable to add: it would enable `FORECAST_DAILY` support
on weather entities with proper temperature, humidity, and wind ranges.
The 24-hour forecast adds regional granularity for hourly forecasts beyond the current
2-hour window, but provides no temperature/humidity data.

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
- `details` — all message content joined with ` | ` (empty string when normal); exposed as `details` attribute on `sensor.singapore_train_status`
- `line_statuses` — dict mapping each line name to `"normal"`, `"planned"`, or `"disruption"`

### Calendar Entity

| Entity ID | Name | Description |
|-----------|------|-------------|
| `calendar.singapore_public_holidays` | Singapore Public Holidays | Singapore public holiday all-day events (year >= current year) |

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

`custom_components/singapore/brand/icon.png` and `logo.png` look like unused duplicates of
`custom_components/singapore/images/icon.png` (they're byte-for-byte identical and oversized
for actual `home-assistant/brands` size limits), but **do not delete them**. The `hacs/action`
CI check (`.github/workflows/hacs.yaml`) validates brand assets by checking for these files
locally as a fallback whenever the repository isn't registered in the `home-assistant/brands`
repo — this repo isn't, so removing `brand/` fails the `<Validation brands>` check and breaks
CI. If this integration is ever submitted to `home-assistant/brands` with correctly-sized
assets there, the local fallback files can be removed at that point, not before.

## Linting and Formatting

After every code change, always run and fix before committing:

```bash
ruff check custom_components/ tests/ --fix
ruff format custom_components/ tests/
```

Both commands must exit cleanly — CI will fail otherwise.

## Key Conventions

- All HA I/O must be `async`; use `async_`-prefixed HA helpers
- Use `async_get_clientsession(hass)` for aiohttp — never create a bare `aiohttp.ClientSession`
- All coordinators use `async_get_clientsession(hass)` for aiohttp requests
- `weather_coordinator.py` handles HTTP 429 rate limits from data.gov.sg with
  `_fetch_with_retry()` (3 retries, respects `Retry-After` header, exponential backoff)
  and an `asyncio.Semaphore` capping concurrent readings requests at 2
- Entity unique IDs must be stable: `{entry_id}_{suffix}`
- Entities use `_attr_has_entity_name = True` with `_attr_translation_key`
  (+ `_attr_translation_placeholders` where dynamic) and `DeviceInfo` (not a raw dict) with
  `entry_type=DeviceEntryType.SERVICE`. Entity strings live under `entity.sensor.<key>` in
  `strings.json`/`translations/en.json`. Unique IDs are unaffected by naming, so entity IDs
  stay stable across the naming migration — only the displayed friendly name changes.
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
- Translations live in `translations/en.json` and must mirror `strings.json`
- For quick one-off API/scraping investigations, use `uv run script.py` with inline `# /// script` dependency blocks — avoids venv setup entirely
