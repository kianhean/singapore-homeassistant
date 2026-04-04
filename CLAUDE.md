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
├── conftest.py        # Mocks HA modules so tests run without installing homeassistant
├── test_init.py       # Domain constant check
├── test_config_flow.py # Config flow schema check
├── test_coordinator.py # Parser unit tests + coordinator HTTP mock tests
└── test_sensor.py     # Sensor value, unit, attributes, unique_id, None-safety

.github/workflows/tests.yml   # CI: tests + ruff lint on all pushes and PRs
```

## Sensors

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_electricity_tariff` | Singapore Electricity Tariff | ¢/kWh | Total residential electricity tariff |
| `sensor.singapore_solar_export_price` | Singapore Solar Export Price | ¢/kWh | Tariff minus network costs |
| `sensor.singapore_gas_tariff` | Singapore Gas Tariff | ¢/kWh | Piped natural gas tariff |
| `sensor.singapore_water_tariff` | Singapore Water Tariff | SGD/m³ | Water tariff |

## Development Setup

No full Home Assistant installation required. Tests mock all HA modules via `conftest.py`.

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
with browser-like headers. Parsing uses two strategies per value:

1. **Table row** — scans `<table>` rows for a keyword match, reads the last numeric cell
2. **Text regex** — scans page text for a number following the keyword

Quarter is inferred from date strings like "1 January 2025 to 31 March 2025".
Solar export price = total electricity tariff − network costs.

## Adding a New Sensor

1. Add new field(s) to `TariffData` in `coordinator.py`
2. Parse the new value(s) in `_parse_tariff` using `_extract_row_value`
3. Add a new sensor class in `sensor.py`, register it in `async_setup_entry`
4. Add tests in `tests/test_sensor.py` and `tests/test_coordinator.py`

## Key Conventions

- All HA I/O must be `async`; use `async_`-prefixed HA helpers
- Use `async_get_clientsession(hass)` — never create a bare `aiohttp.ClientSession`
- Entity unique IDs must be stable: `{entry_id}_{suffix}`
- Keep `manifest.json` version in sync with releases
- Translations live in `translations/en.json` and must mirror `strings.json`
