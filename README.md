# Singapore Home Assistant Integration

A [HACS](https://hacs.xyz) custom integration for Home Assistant that tracks Singapore's
residential electricity tariff published by [SP Group](https://www.spgroup.com.sg).

## Sensors

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_electricity_tariff` | Singapore Electricity Tariff | ¢/kWh | Total residential electricity tariff |
| `sensor.singapore_solar_export_price` | Singapore Solar Export Price | ¢/kWh | Tariff minus network costs — the rate paid for excess solar exported to the grid |

Both sensors refresh every **24 hours** and expose `quarter` (e.g. `Q1`), `year`, and `source` as state attributes.
The solar export price sensor additionally exposes `network_cost` and `total_tariff` as attributes.

## Example sensor states

```yaml
sensor.singapore_electricity_tariff:
  state: 29.29
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q1
    year: 2025
    source: SP Group

sensor.singapore_solar_export_price:
  state: 21.68
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q1
    year: 2025
    source: SP Group
    network_cost: 7.61
    total_tariff: 29.29
```

## Installation via HACS

1. Open HACS in your Home Assistant instance.
2. Go to **Integrations → Custom repositories** (three-dot menu).
3. Add `https://github.com/kianhean/singapore-homeassistant` with category **Integration**.
4. Search for **Singapore Electricity Tariff** and install it.
5. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Singapore Electricity Tariff**.
3. Enter a name and click **Submit**.

## Data source

Tariff data is scraped from the [SP Group tariff information page](https://www.spgroup.com.sg/our-services/utilities/tariff-information).
Prices are published quarterly in Singapore cents per kilowatt-hour.

The solar export price is calculated as `total tariff − network costs`.
Network costs (transmission and distribution charges) are not applicable
to electricity exported back to the grid.

## Development

See [CLAUDE.md](CLAUDE.md) for project structure, test instructions, and conventions.

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```
