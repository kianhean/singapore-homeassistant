# Singapore Home Assistant Integration

A [HACS](https://hacs.xyz) custom integration for Home Assistant that tracks Singapore
utility tariffs published by [SP Group](https://www.spgroup.com.sg), updated every 24 hours.

## Sensors

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_electricity_tariff` | Singapore Electricity Tariff | ¢/kWh | Total residential electricity tariff |
| `sensor.singapore_solar_export_price` | Singapore Solar Export Price | ¢/kWh | Electricity tariff minus network costs |
| `sensor.singapore_gas_tariff` | Singapore Gas Tariff | ¢/kWh | Piped natural gas tariff |
| `sensor.singapore_water_tariff` | Singapore Water Tariff | SGD/m³ | Water tariff |

All sensors expose `quarter` (e.g. `Q1`), `year`, and `source` as state attributes.
The solar export price sensor additionally exposes `network_cost` and `total_tariff`.

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

sensor.singapore_gas_tariff:
  state: 20.14
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q1
    year: 2025
    source: SP Group

sensor.singapore_water_tariff:
  state: 3.69
  unit_of_measurement: SGD/m³
  attributes:
    quarter: Q1
    year: 2025
    source: SP Group
```

## Installation via HACS

1. Open HACS in your Home Assistant instance.
2. Go to **Integrations → Custom repositories** (three-dot menu).
3. Add `https://github.com/kianhean/singapore-homeassistant` with category **Integration**.
4. Search for **Singapore** and install it.
5. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Singapore**.
3. Enter a name and click **Submit**.

## Data source

Tariff data is scraped from the [SP Group tariff information page](https://www.spgroup.com.sg/our-services/utilities/tariff-information).
Prices are published quarterly.

- Electricity and gas prices are in Singapore cents per kilowatt-hour (¢/kWh)
- Water price is in Singapore dollars per cubic metre (SGD/m³)
- Solar export price = electricity tariff − network costs (network charges don't apply to exported electricity)

## Development

See [CLAUDE.md](CLAUDE.md) for project structure, test instructions, and conventions.

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```
