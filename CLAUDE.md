# Singapore Home Assistant Integration

A general-purpose HACS integration for Singapore-specific Home Assistant sensors.
The integration domain is `singapore`.

## Structure

```
custom_components/singapore/
├── __init__.py           # Entry setup/teardown; creates and stores both coordinators
├── coordinator.py        # SPGroupCoordinator: fetches + parses SP Group tariff page
├── coe_coordinator.py    # CoeCoordinator: fetches COE results from data.gov.sg API
├── config_flow.py        # UI config flow (name input)
├── sensor.py             # Sensor entities (tariff + COE)
├── manifest.json         # Integration metadata; declares beautifulsoup4 dep
├── strings.json          # Config flow UI strings
└── translations/
    └── en.json           # English translations (mirrors strings.json)

tests/
├── conftest.py              # Mocks HA modules so tests run without installing homeassistant
├── test_init.py             # Domain constant check
├── test_config_flow.py      # Config flow schema check
├── test_coordinator.py      # SP Group parser unit tests + coordinator HTTP mock tests
├── test_coe_coordinator.py  # COE parser unit tests + coordinator HTTP mock tests
├── test_sensor.py           # Sensor value, unit, attributes, unique_id, None-safety
└── test_e2e.py              # Live scrape tests (run with -m e2e, skipped in CI by default)

.github/workflows/tests.yml   # CI: three jobs — unit tests, e2e scrape, ruff lint
```

Entry data is stored as a dict per `entry_id`:
```python
hass.data[DOMAIN][entry_id] = {"tariff": SPGroupCoordinator, "coe": CoeCoordinator}
```

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

## Development Setup

No full Home Assistant installation required. Tests mock all HA modules via `conftest.py`.

```bash
pip install -r requirements_test.txt
```

## Running Tests

Unit tests (no network, always fast):

```bash
pytest tests/ -v -m "not e2e"
```

Live e2e tests (hit the real SP Group website — run locally when the scraper may have broken):

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

## Linting and Formatting

After every code change, always run and fix before committing:

```bash
ruff check custom_components/ tests/ --fix
ruff format custom_components/ tests/
```

Both commands must exit cleanly — CI will fail otherwise.

## Key Conventions

- All HA I/O must be `async`; use `async_`-prefixed HA helpers
- Use `async_get_clientsession(hass)` — never create a bare `aiohttp.ClientSession`
- Entity unique IDs must be stable: `{entry_id}_{suffix}`
- Keep `manifest.json` version in sync with releases
- Translations live in `translations/en.json` and must mirror `strings.json`
