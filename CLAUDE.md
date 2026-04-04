# Singapore Home Assistant HACS Integration

A HACS custom integration that tracks Singapore's residential electricity tariff
from SP Group, updated every 24 hours.

## Structure

```
custom_components/singapore_hello/
├── __init__.py        # Entry setup/teardown; creates and stores coordinator
├── coordinator.py     # DataUpdateCoordinator: fetches + parses SP Group page
├── config_flow.py     # UI config flow (name input)
├── sensor.py          # CoordinatorEntity sensor (¢/kWh, quarter, year attrs)
├── manifest.json      # Integration metadata; declares beautifulsoup4 dep
├── strings.json       # Config flow UI strings
└── translations/
    └── en.json        # English translations (mirrors strings.json)

tests/
├── conftest.py        # Enables custom integrations for all tests
├── test_init.py       # Entry setup/unload; verifies coordinator stored
├── test_config_flow.py # Happy path + duplicate abort
├── test_coordinator.py # Parser unit tests + coordinator HTTP mock tests
└── test_sensor.py     # Sensor state, unit, attributes, unavailable, unload

.github/workflows/tests.yml   # CI: tests (Py 3.12 & 3.13) + ruff lint
```

## Development Setup

```bash
pip install -r requirements_test.txt
```

## Running Tests

```bash
pytest tests/ -v
```

With coverage:

```bash
pytest tests/ -v --cov=custom_components/singapore_hello --cov-report=term-missing
```

## How the Scraper Works

`coordinator.py` fetches `https://www.spgroup.com.sg/our-services/utilities/tariff-information`
with browser-like headers (SP Group blocks plain bots). Parsing uses three fallback strategies:

1. **Table row** — scans `<table>` rows for one containing "total" or "residential", reads last numeric cell
2. **Text regex** — scans page text for a number following "total" or "residential"
3. **Number scan** — picks the first plausible standalone number in range 5–100

Quarter is inferred from date strings like "1 January 2025 to 31 March 2025".

## Adding a New Platform

1. Create `custom_components/singapore_hello/<platform>.py`
2. Add the platform to `PLATFORMS` in `__init__.py`
3. Add tests in `tests/test_<platform>.py`

## Key Conventions

- All HA I/O must be `async`; use `async_`-prefixed HA helpers
- Use `async_get_clientsession(hass)` — never create a bare `aiohttp.ClientSession`
- Entity unique IDs must be stable: `{entry_id}_{suffix}`
- Keep `manifest.json` version in sync with releases
- Translations live in `translations/en.json` and must mirror `strings.json`
