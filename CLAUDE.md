# Singapore Home Assistant Integration

A general-purpose HACS integration for Singapore-specific Home Assistant sensors.
The integration domain is `singapore`.

## Structure

```
custom_components/singapore/
├── __init__.py        # Entry setup/teardown; creates and stores coordinator
├── coordinator.py     # DataUpdateCoordinator: fetches + parses SP Group page
├── config_flow.py     # UI config flow (name input)
├── sensor.py          # Sensor entities
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

## Sensors

| Entity ID | Name | Description |
|-----------|------|-------------|
| `sensor.singapore_electricity_tariff` | Singapore Electricity Tariff | Total residential tariff in ¢/kWh |
| `sensor.singapore_solar_export_price` | Singapore Solar Export Price | Tariff minus network costs in ¢/kWh |

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
pytest tests/ -v --cov=custom_components/singapore --cov-report=term-missing
```

## How the Scraper Works

`coordinator.py` fetches `https://www.spgroup.com.sg/our-services/utilities/tariff-information`
with browser-like headers (SP Group blocks plain bots). Parsing uses two strategies:

1. **Table row** — scans `<table>` rows for a keyword match, reads the last numeric cell
2. **Text regex** — scans page text for a number following the keyword

Quarter is inferred from date strings like "1 January 2025 to 31 March 2025".

Solar export price = total tariff − network costs (network charges don't apply to exported electricity).

## Adding a New Sensor

1. Add any new fields to `TariffData` in `coordinator.py` and parse them in `_parse_tariff`
2. Add a new sensor class in `sensor.py` and include it in `async_setup_entry`
3. Add tests in `tests/test_sensor.py` and `tests/test_coordinator.py`

## Key Conventions

- All HA I/O must be `async`; use `async_`-prefixed HA helpers
- Use `async_get_clientsession(hass)` — never create a bare `aiohttp.ClientSession`
- Entity unique IDs must be stable: `{entry_id}_{suffix}`
- Keep `manifest.json` version in sync with releases
- Translations live in `translations/en.json` and must mirror `strings.json`
