# Singapore Home Assistant Integration

A [HACS](https://hacs.xyz) custom integration for Home Assistant that tracks Singapore
utility tariffs and COE (Certificate of Entitlement) bidding results.

## Sensors

### SP Group Utility Tariffs

Updated every 24 hours from the [SP Group tariff page](https://www.spgroup.com.sg/our-services/utilities/tariff-information).

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_electricity_tariff` | Singapore Electricity Tariff | ¢/kWh | Total residential electricity tariff |
| `sensor.singapore_solar_export_price` | Singapore Solar Export Price | ¢/kWh | Electricity tariff minus network costs |
| `sensor.singapore_gas_tariff` | Singapore Gas Tariff | ¢/kWh | Piped natural gas tariff |
| `sensor.singapore_water_tariff` | Singapore Water Tariff | SGD/m³ | Water tariff, lower residential tier (≤40 m³, with GST) |

Tariff sensors expose `quarter` (e.g. `Q1`), `year`, and `source` as state attributes.
The solar export price sensor additionally exposes `network_cost` and `total_tariff`.

### COE Bidding Results

Updated daily at **19:30** from the [LTA dataset on data.gov.sg](https://data.gov.sg/datasets/d_69b3380ad7e51aff3a7dcc84eba52b8a/view).
Sensor value is the COE premium in SGD from the latest completed bidding exercise.

| Entity ID | Name | Unit | Category |
|-----------|------|------|----------|
| `sensor.singapore_coe_category_a` | Singapore COE Category A | SGD | Cars ≤1600cc / ≤97kW (electric) |
| `sensor.singapore_coe_category_b` | Singapore COE Category B | SGD | Cars >1600cc / >97kW (electric) |
| `sensor.singapore_coe_category_c` | Singapore COE Category C | SGD | Goods vehicles and buses |
| `sensor.singapore_coe_category_d` | Singapore COE Category D | SGD | Motorcycles |
| `sensor.singapore_coe_category_e` | Singapore COE Category E (Open) | SGD | Open — all except motorcycles |

COE sensors expose `category`, `description`, `month`, `bidding_no`, and `source` as state attributes.

## Example sensor states

```yaml
sensor.singapore_electricity_tariff:
  state: 29.72
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q2
    year: 2026
    source: SP Group

sensor.singapore_solar_export_price:
  state: 23.47
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q2
    year: 2026
    source: SP Group
    network_cost: 6.25
    total_tariff: 29.72

sensor.singapore_gas_tariff:
  state: 23.89
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q2
    year: 2026
    source: SP Group

sensor.singapore_water_tariff:
  state: 1.56
  unit_of_measurement: SGD/m³
  attributes:
    quarter: Q2
    year: 2026
    source: SP Group

sensor.singapore_coe_category_a:
  state: 95501
  unit_of_measurement: SGD
  attributes:
    category: Category A
    description: Cars up to 1600cc / 97kW (electric)
    month: "2026-03"
    bidding_no: 1
    source: data.gov.sg / LTA

sensor.singapore_coe_category_e:
  state: 118001
  unit_of_measurement: SGD
  attributes:
    category: Category E
    description: Open (all except motorcycles)
    month: "2026-03"
    bidding_no: 1
    source: data.gov.sg / LTA
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

## Data sources

- **Utility tariffs** — scraped from the SP Group tariff information page; published quarterly.
  - Electricity and gas: Singapore cents per kilowatt-hour (¢/kWh)
  - Water: SGD per cubic metre (SGD/m³); lower residential tier (≤40 m³)
  - Solar export price = electricity tariff − network costs
- **COE results** — fetched from the LTA dataset on [data.gov.sg](https://data.gov.sg/datasets/d_69b3380ad7e51aff3a7dcc84eba52b8a/view); refreshed daily at 19:30 to pick up results after each bidding exercise.

## Development

See [CLAUDE.md](CLAUDE.md) for project structure, test instructions, and conventions.

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```
