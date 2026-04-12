<p align="center">
  <img src="custom_components/singapore/images/icon.png" alt="Singapore Home Assistant custom integration" width="300">
</p>

# Singapore Home Assistant custom integration

A [HACS](https://hacs.xyz) custom integration for Singapore-specific data: utility
tariffs, COE bidding results, live weather, train status, public holidays, and
optionally your household electricity and water usage via SP Services.

### HACS installation (manual, pre-merge)

This integration is **not merged into the default HACS store yet**, so install it as a
custom repository first:

1. Open **HACS** in Home Assistant.
2. Go to **Integrations**.
3. Open the three-dot menu (top-right) → **Custom repositories**.
4. Repository: `https://github.com/kianhean/singapore-homeassistant`
5. Category: **Integration**
6. Click **Add**.
7. Search for **Singapore** in HACS and install it.
8. Restart Home Assistant.

## What you get

### SP Group utility tariffs

Scraped from the [SP Group tariff page](https://www.spgroup.com.sg/our-services/utilities/tariff-information)
every 24 hours.

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_electricity_tariff` | Singapore Electricity Tariff | ¢/kWh | Total residential electricity tariff |
| `sensor.singapore_solar_export_price` | Singapore Solar Export Price | ¢/kWh | Electricity tariff minus network costs |
| `sensor.singapore_gas_tariff` | Singapore Gas Tariff | ¢/kWh | Piped natural gas tariff |
| `sensor.singapore_water_tariff` | Singapore Water Tariff | SGD/m³ | Water tariff, lower residential tier (≤40 m³, with GST) |

Tariff sensors include `quarter` (e.g. `Q1`), `year`, and `source` as state attributes.
The solar export price sensor also includes `network_cost` and `total_tariff`.

<p align="center">
  <img src="images/energy-sensors.jpeg" alt="Singapore Energy device page showing electricity, gas, solar export, and water tariff sensors" width="320">
</p>

### COE bidding results

Pulled from the [LTA dataset on data.gov.sg](https://data.gov.sg/datasets/d_69b3380ad7e51aff3a7dcc84eba52b8a/view)
daily at **19:30**, after each bidding exercise.

| Entity ID | Name | Unit | Category |
|-----------|------|------|----------|
| `sensor.singapore_coe_category_a` | Singapore COE Category A | SGD | Cars ≤1600cc / ≤97kW (electric) |
| `sensor.singapore_coe_category_b` | Singapore COE Category B | SGD | Cars >1600cc / >97kW (electric) |
| `sensor.singapore_coe_category_c` | Singapore COE Category C | SGD | Goods vehicles and buses |
| `sensor.singapore_coe_category_d` | Singapore COE Category D | SGD | Motorcycles |
| `sensor.singapore_coe_category_e` | Singapore COE Category E (Open) | SGD | Open — all except motorcycles |

Each sensor includes `category`, `description`, `month`, `bidding_no`, and `source` as
state attributes.

<p align="center">
  <img src="images/coe-sensors.jpeg" alt="Singapore COE device page showing Category A to E certificate of entitlement sensors" width="320">
</p>

### NEA realtime weather readings

Updated every **10 minutes** from [data.gov.sg collection 1459](https://data.gov.sg/collections/1459/view).
Station readings are averaged across all available stations at fetch time.

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_temperature` | Singapore Temperature | °C | Aggregated air temperature |
| `sensor.singapore_humidity` | Singapore Humidity | % | Aggregated relative humidity |
| `sensor.singapore_wind_speed` | Singapore Wind Speed | km/h | Aggregated wind speed |
| `sensor.singapore_wind_bearing` | Singapore Wind Bearing | ° | Aggregated wind direction |
| `sensor.singapore_rainfall` | Singapore Rainfall | mm | Aggregated rainfall |

<p align="center">
  <img src="images/weather-overview.jpeg" alt="Singapore Weather entity list showing area forecasts alongside humidity, rainfall, temperature, wind bearing, and wind speed sensors" width="320">
</p>

### Weather entities (2-hour forecast, by area)

One weather entity per forecast area from [data.gov.sg collection 1456](https://data.gov.sg/collections/1456/view),
updated every **10 minutes**. Each 2-hour NEA forecast block becomes two hourly forecast
points in Home Assistant.

Example entities: `weather.singapore_weather_bedok`, `weather.singapore_weather_ang_mo_kio`,
`weather.singapore_weather_woodlands`.

Each entity has a mapped HA condition (`sunny`, `partlycloudy`, `rainy`, etc.), an hourly
forecast list, and attributes like `raw_condition`, `valid_start`, and `valid_end`.

<p align="center">
  <img src="images/weather-forecast-bedok.jpeg" alt="Singapore Weather Bedok forecast card showing current condition and multi-day forecast" width="320">
</p>

### MRT/LRT train status

Updated every **5 minutes** from [mytransport.sg](https://www.mytransport.sg/trainstatus).
Tracks both an overall network status and a per-line status for each MRT/LRT line.

| Entity ID | Name | Description |
|-----------|------|-------------|
| `sensor.singapore_train_status` | Singapore Train Status | Overall network status: `normal`, `planned`, or `disruption` |
| `sensor.singapore_<line>_status` | e.g. Singapore Circle Line Status | Per-line status |

Lines: North-South, East-West, North East, Circle, Downtown, Thomson-East Coast,
Bukit Panjang LRT, Sengkang LRT, Punggol LRT.

<p align="center">
  <img src="images/train-status.jpeg" alt="Singapore MRT/LRT device page showing overall and per-line train status sensors" width="320">
</p>

### SP Services household usage (optional)

> **Disclaimer:** This integration is an independent, community-built project and is
> **not affiliated with, endorsed by, or officially supported by SP Group or SP Services.**
> All authentication — your username, password, OTP, and session token — is handled
> entirely on your own Home Assistant device and is never transmitted to any third-party
> server. Credentials are stored in your local HA config entry only.

When configured (see [Setup](#setup) below), six additional sensors track your household
consumption as reported by [SP Services](https://services.spservices.sg):

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.singapore_sp_electricity_today` | Singapore SP Electricity Today | kWh | Electricity consumed today |
| `sensor.singapore_sp_electricity_month` | Singapore SP Electricity This Month | kWh | Electricity consumed this month |
| `sensor.singapore_sp_electricity_last_month` | Singapore SP Electricity Last Month | kWh | Electricity consumed last month |
| `sensor.singapore_sp_electricity_bill_estimate` | Singapore SP Electricity Bill Estimate | SGD | Estimated current-month electricity bill using the current SP Group tariff |
| `sensor.singapore_sp_water_month` | Singapore SP Water This Month | m³ | Water consumed this month |
| `sensor.singapore_sp_water_last_month` | Singapore SP Water Last Month | m³ | Water consumed last month |

The electricity and water month-based sensors use `SensorDeviceClass.ENERGY` /
`SensorDeviceClass.WATER` with `SensorStateClass.TOTAL`, so they can be used in the
Home Assistant **Energy Dashboard**. Water data is only available monthly from the
observed SP Services exports, so there is no daily water sensor. The bill estimate is a
derived `measurement` sensor that multiplies the SP Services month-to-date electricity
usage by the current SP Group residential electricity tariff.

SP Services requires two-factor authentication (OTP via SMS). Credentials are entered
through the integration's **Configure** dialog after setup — see [Setup](#setup).

If your SP Services session expires, Home Assistant will show a **Re-authenticate**
repair notification. Follow the prompt to re-enter your credentials and receive a
new OTP.

### Public holidays

Updated every 24 hours from [MOM](https://www.mom.gov.sg/employment-practices/public-holidays).
Shows up as a Home Assistant calendar with all-day events from the current year onward.

| Entity ID | Name |
|-----------|------|
| `calendar.singapore_public_holidays` | Singapore Public Holidays |

<p align="center">
  <img src="images/public-holidays-calendar.jpeg" alt="Singapore public holidays calendar showing Good Friday and Saturday as all-day events" width="320">
</p>

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

sensor.singapore_coe_category_a:
  state: 95501
  unit_of_measurement: SGD
  attributes:
    category: Category A
    description: Cars up to 1600cc / 97kW (electric)
    month: "2026-03"
    bidding_no: 1
    source: data.gov.sg / LTA

sensor.singapore_temperature:
  state: 31.2
  unit_of_measurement: °C
  attributes:
    source: data.gov.sg / NEA (collection 1459)
```

## Installation via HACS (manual custom repository)

1. Open HACS in your Home Assistant instance.
2. Go to **Integrations → Custom repositories** (three-dot menu).
3. Add `https://github.com/kianhean/singapore-homeassistant` with category **Integration**.
4. Search for **Singapore** and install it.
5. Restart Home Assistant.

## Setup

### Basic setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Singapore**.
3. Enter a name and click **Submit**.

All public data sources (tariffs, COE, weather, trains, holidays) are enabled immediately.

### Adding SP Services household usage

SP Services credentials are configured separately after the integration is set up:

1. Go to **Settings → Devices & Services**.
2. Find the **Singapore** integration and click **Configure**.
3. Enter your SP Services username and password.
4. Enter the OTP sent to your registered mobile number.

Leave both fields blank and submit to remove SP Services credentials and disable
the household usage sensors.

> **Privacy note:** Your SP Services credentials and session token are stored only in
> your local Home Assistant configuration. They are never sent to any server other than
> `services.spservices.sg` directly from your device.

## Data sources

| Source | Data | Refresh |
|--------|------|---------|
| [SP Group](https://www.spgroup.com.sg/our-services/utilities/tariff-information) | Electricity, gas, water tariffs | Every 24 h |
| [data.gov.sg / LTA](https://data.gov.sg/datasets/d_69b3380ad7e51aff3a7dcc84eba52b8a/view) | COE bidding results | Daily at 19:30 |
| [data.gov.sg / NEA (collection 1456)](https://data.gov.sg/collections/1456/view) | 2-hour area weather forecasts | Every 10 min |
| [data.gov.sg / NEA (collection 1459)](https://data.gov.sg/collections/1459/view) | Realtime weather readings | Every 10 min |
| [MOM](https://www.mom.gov.sg/employment-practices/public-holidays) | Public holidays | Every 24 h |
| [mytransport.sg](https://www.mytransport.sg/trainstatus) | MRT/LRT train status | Every 5 min |
| [SP Services](https://services.spservices.sg) | Household electricity & water usage | Every 30 min (optional) |

## Development

See [CLAUDE.md](CLAUDE.md) for project structure, test instructions, and conventions.

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```

## License

This project is licensed under the [MIT License](LICENSE).
