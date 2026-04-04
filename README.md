# Singapore Home Assistant Integration

A [HACS](https://hacs.xyz) custom integration for Home Assistant that tracks Singapore's
residential electricity tariff published by [SP Group](https://www.spgroup.com.sg).

## Features

- **Electricity tariff sensor** — reports the current residential rate in **¢/kWh**
- **Quarterly attributes** — `quarter` (e.g. `Q1`) and `year` on the sensor state
- **24-hour polling** — automatically refreshes once per day
- Robust HTML parser with three fallback strategies to handle SP Group page changes

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

The integration creates one sensor entity:

| Entity | Unit | Attributes |
|--------|------|------------|
| `sensor.<name>_electricity_tariff` | `¢/kWh` | `quarter`, `year`, `source` |

## Example sensor state

```yaml
state: 29.29
unit_of_measurement: ¢/kWh
attributes:
  quarter: Q1
  year: 2025
  source: SP Group
```

## Data source

Tariff data is scraped from the [SP Group tariff information page](https://www.spgroup.com.sg/our-services/utilities/tariff-information).
Prices are published quarterly and denominated in Singapore cents per kilowatt-hour.

## Development

See [CLAUDE.md](CLAUDE.md) for project structure, test instructions, and conventions.

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```
