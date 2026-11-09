<p align="center">
  <img src="custom_components/singapore/images/icon.png" alt="Singapore Home Assistant custom integration" width="300">
</p>

# Singapore Home Assistant custom integration

A [HACS](https://hacs.xyz) custom integration for Singapore-specific data:

- **Utilities:** SP Group electricity, gas, water and solar export tariffs
- **Transport:** COE bidding results, MRT/LRT train status, Woodlands and Tuas checkpoint traffic cameras
- **Weather:** NEA weather by forecast area with a 4-day daily forecast, plus live temperature, humidity, wind and rainfall
- **Environment:** air quality (PSI and pollutants, with per-region map markers), PUB flash-flood alerts, NEA dengue clusters with distance from your home
- **Calendar:** Singapore public holidays

Requires **Home Assistant 2025.4** or newer.

## Installation

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

## Setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Singapore**.
3. Enter a name and click **Submit**.

Only one Singapore entry can be added. Entities are grouped into devices (Energy, COE,
Weather, MRT/LRT, Air Quality, PUB, NEA, Traffic Cameras, Public Holidays).

> **Entity IDs on older installs:** since v0.3.0, entity names come from their device
> (for example *Energy Electricity Tariff*). New installs get IDs like
> `sensor.energy_electricity_tariff`. Installs that started before v0.3.0 keep their
> original IDs (for example `sensor.singapore_electricity_tariff`), so existing dashboards
> and automations keep working. The tables below show the IDs a new install gets.

## What you get

### SP Group utility tariffs

Scraped from the [SP Group tariff page](https://www.spgroup.com.sg/our-services/utilities/tariff-information)
every 24 hours.

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.energy_electricity_tariff` | Energy Electricity Tariff | ¢/kWh | Total residential electricity tariff (with GST) |
| `sensor.energy_solar_export_price` | Energy Solar Export Price | ¢/kWh | Electricity tariff minus network costs |
| `sensor.energy_gas_tariff` | Energy Gas Tariff | ¢/kWh | Piped natural gas tariff (with GST) |
| `sensor.energy_water_tariff` | Energy Water Tariff | SGD/m³ | Water tariff, lower residential tier (≤40 m³, with GST) |

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
| `sensor.coe_category_a` | COE Category A | SGD | Cars ≤1600cc / ≤97kW (electric) |
| `sensor.coe_category_b` | COE Category B | SGD | Cars >1600cc / >97kW (electric) |
| `sensor.coe_category_c` | COE Category C | SGD | Goods vehicles and buses |
| `sensor.coe_category_d` | COE Category D | SGD | Motorcycles |
| `sensor.coe_category_e` | COE Category E | SGD | Open — all except motorcycles |

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
| `sensor.weather_temperature` | Weather Temperature | °C | Aggregated air temperature |
| `sensor.weather_humidity` | Weather Humidity | % | Aggregated relative humidity |
| `sensor.weather_wind_speed` | Weather Wind Speed | km/h | Aggregated wind speed |
| `sensor.weather_wind_bearing` | Weather Wind Bearing | ° | Aggregated wind direction |
| `sensor.weather_rainfall` | Weather Rainfall | mm | Aggregated rainfall |

<p align="center">
  <img src="images/weather-overview.jpeg" alt="Singapore Weather entity list showing area forecasts alongside humidity, rainfall, temperature, wind bearing, and wind speed sensors" width="320">
</p>

### Weather entities (by forecast area)

One weather entity per NEA forecast area from [data.gov.sg collection 1456](https://data.gov.sg/collections/1456/view),
updated every **10 minutes**.

Example entities: `weather.weather_bedok`, `weather.weather_ang_mo_kio`,
`weather.weather_woodlands`.

- **Current condition** comes from the area's 2-hour NEA forecast, mapped to an HA
  condition (`sunny`, `partlycloudy`, `rainy`, `lightning-rainy`, etc.).
- **Current temperature, humidity and wind** come from the live readings above.
- **Daily forecast** is NEA's Singapore-wide 4-day outlook, with high/low temperature,
  humidity and wind for each day. It is the same for every area.

Attributes: `forecast_area`, `raw_condition` (NEA's original text), `valid_start` /
`valid_end` (the 2-hour forecast window), plus the live readings.

<p align="center">
  <img src="images/weather-forecast-bedok.jpeg" alt="Singapore Weather Bedok forecast card showing current condition and multi-day forecast" width="320">
</p>

### MRT/LRT train status

Updated every **5 minutes** from [mytransport.sg](https://www.mytransport.sg/trainstatus).
Tracks both an overall network status and a per-line status for each MRT/LRT line.

| Entity ID | Name | Description |
|-----------|------|-------------|
| `sensor.mrt_lrt_status` | MRT/LRT Status | Overall network status: `normal`, `planned`, or `disruption`; `details` attribute holds LTA's messages |
| `sensor.mrt_lrt_<line>_status` | e.g. MRT/LRT Circle Line Status | Per-line status, e.g. `sensor.mrt_lrt_circle_line_status` |

Lines: North-South, East-West, North East, Circle, Downtown, Thomson-East Coast,
Bukit Panjang LRT, Sengkang LRT, Punggol LRT.

<p align="center">
  <img src="images/train-status.jpeg" alt="Singapore MRT/LRT device page showing overall and per-line train status sensors" width="320">
</p>

### Air quality (PSI)

Updated every **15 minutes** from [data.gov.sg / NEA](https://data.gov.sg/datasets/d_fe37906a0182569d891506e815e819b7/view).
Grouped under an **Air Quality** device.

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.air_quality_psi` | Air Quality PSI | — | Singapore-wide 24-hour PSI (highest region); `band` attribute: `good` / `moderate` / `unhealthy` / `very_unhealthy` / `hazardous` |
| `sensor.air_quality_psi_<region>` | e.g. Air Quality PSI North | — | 24-hour PSI for north, south, east, west, central — **with map location and every reading for that region** |
| `sensor.air_quality_pm2_5` | Air Quality PM2.5 | µg/m³ | 24-hour PM2.5 (highest region) |
| `sensor.air_quality_pm10` | Air Quality PM10 | µg/m³ | 24-hour PM10 (highest region) |
| `sensor.air_quality_ozone` | Air Quality Ozone | µg/m³ | 8-hour max O₃ (highest region) |
| `sensor.air_quality_nitrogen_dioxide` | Air Quality Nitrogen Dioxide | µg/m³ | 1-hour max NO₂ (highest region) |
| `sensor.air_quality_sulphur_dioxide` | Air Quality Sulphur Dioxide | µg/m³ | 24-hour SO₂ (highest region) |
| `sensor.air_quality_carbon_monoxide` | Air Quality Carbon Monoxide | mg/m³ | 8-hour max CO (highest region) |

The Singapore-wide pollutant sensors also expose a `regions` attribute with each region's value.

#### PSI on the map

Each regional PSI sensor has `latitude`/`longitude` attributes (NEA's region label
locations), so it shows up as one circle per region on a Map card. Use `label_mode: state`
to show the PSI number inside each circle; tap a circle to see every reading for that
region (3-hour and 24-hour PSI, PM2.5, PM10, O₃, NO₂, SO₂, CO and the sub-indices):

```yaml
type: map
title: Singapore PSI
default_zoom: 11
entities:
  - entity: sensor.air_quality_psi_north
    label_mode: state
  - entity: sensor.air_quality_psi_south
    label_mode: state
  - entity: sensor.air_quality_psi_east
    label_mode: state
  - entity: sensor.air_quality_psi_west
    label_mode: state
  - entity: sensor.air_quality_psi_central
    label_mode: state
```

### PUB flood alerts

Updated every **2 minutes** from [PUB via data.gov.sg](https://data.gov.sg/datasets/d_f1404e08587ce555b9ea3f565e2eb9a3/view).

| Entity ID | Name | Description |
|-----------|------|-------------|
| `sensor.pub_flood_alerts` | PUB Flood Alerts | Number of active flash-flood alerts (`0` when none) |

Attributes: `locations` (list of alert descriptions) and `alerts` (full details per alert:
description, area, severity, instruction, issued time, latitude/longitude). An alert stays
active until PUB issues its matching Cancel, or for 3 hours at most.

Example automation that sends a notification when a new alert appears:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.pub_flood_alerts
    above: 0
actions:
  - action: notify.notify
    data:
      title: PUB flood alert
      message: "{{ state_attr('sensor.pub_flood_alerts', 'locations') | join(', ') }}"
```

### Checkpoint traffic cameras

Updated every **2 minutes** from [LTA via data.gov.sg](https://data.gov.sg/datasets/d_6cdb6b405b25aaaacbaf7689bcc6fae0/view).
Only the Woodlands and Tuas checkpoint cameras are exposed, as `camera` entities showing the latest still.

| Entity ID | LTA camera | View |
|-----------|-----------|------|
| `camera.traffic_cameras_woodlands_causeway` | 2701 | Woodlands Causeway (towards Johor) |
| `camera.traffic_cameras_woodlands_checkpoint` | 2702 | Woodlands Checkpoint (towards BKE) |
| `camera.traffic_cameras_tuas_second_link` | 4703 | Tuas Second Link |
| `camera.traffic_cameras_tuas_checkpoint` | 4713 | Tuas Checkpoint |

A camera shows as unavailable while LTA leaves it out of the feed.

### Dengue clusters

Updated every **6 hours** from [NEA via data.gov.sg](https://data.gov.sg/datasets/d_dbfabf16158d1b0e1c420627c0819168/view).

| Entity ID | Name | Unit | Description |
|-----------|------|------|-------------|
| `sensor.nea_dengue_clusters` | NEA Dengue Clusters | — | Number of active clusters; attributes `total_cases` and `clusters` (locality, cases, centroid) |
| `sensor.nea_nearest_dengue_cluster` | NEA Nearest Dengue Cluster | km | Distance from your HA home location to the nearest cluster boundary (`0` = inside a cluster) |

The nearest-cluster distance uses the home location in **Settings → System → General**.

### Public holidays

Updated every 24 hours from [MOM](https://www.mom.gov.sg/employment-practices/public-holidays).
Shows up as a Home Assistant calendar with all-day events from the current year onward.

| Entity ID | Name |
|-----------|------|
| `calendar.public_holidays` | Public Holidays |

<p align="center">
  <img src="images/public-holidays-calendar.jpeg" alt="Singapore public holidays calendar showing Good Friday and Saturday as all-day events" width="320">
</p>

## Example sensor states

```yaml
sensor.energy_electricity_tariff:
  state: 29.72
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q2
    year: 2026
    source: SP Group

sensor.energy_solar_export_price:
  state: 23.47
  unit_of_measurement: ¢/kWh
  attributes:
    quarter: Q2
    year: 2026
    source: SP Group
    network_cost: 6.25
    total_tariff: 29.72

sensor.coe_category_a:
  state: 95501
  unit_of_measurement: SGD
  attributes:
    category: Category A
    description: Cars up to 1600cc / 97kW (electric)
    month: "2026-03"
    bidding_no: 1
    source: data.gov.sg / LTA

sensor.weather_temperature:
  state: 31.2
  unit_of_measurement: °C
  attributes:
    source: data.gov.sg / NEA (collection 1459)

sensor.air_quality_psi_east:
  state: 39
  attributes:
    region: east
    band: good
    latitude: 1.35735
    longitude: 103.94
    psi_twenty_four_hourly: 39
    psi_three_hourly: 0
    pm25_twenty_four_hourly: 0
    pm10_twenty_four_hourly: 3
    o3_eight_hour_max: 9
    no2_one_hour_max: 12
    so2_twenty_four_hourly: 5
    co_eight_hour_max: 19
    reading_time: "2024-07-17T14:00:00+00:00"
    source: data.gov.sg / NEA
```

## Troubleshooting

- **Diagnostics:** on the Singapore integration page, open the three-dot menu and choose
  **Download diagnostics**. The file shows each data source's last update result, error
  and parsed data. Attach it when opening an issue.
- **Unavailable sensors:** flood alerts, traffic cameras, dengue clusters, PSI and COE
  load in the background, so the rest of the integration still starts if one of those
  sources is down. Their sensors show as unavailable until a fetch succeeds.
- **HTTP 429 in the log:** data.gov.sg rate-limits bursts of requests, which can happen
  when Home Assistant starts. Weather and dengue requests retry right away; other
  sources try again on their next scheduled update.

## Data sources

| Source | Data | Refresh |
|--------|------|---------|
| [SP Group](https://www.spgroup.com.sg/our-services/utilities/tariff-information) | Electricity, gas, water tariffs | Every 24 h |
| [data.gov.sg / LTA](https://data.gov.sg/datasets/d_69b3380ad7e51aff3a7dcc84eba52b8a/view) | COE bidding results | Daily at 19:30 |
| [data.gov.sg / NEA (collection 1456)](https://data.gov.sg/collections/1456/view) | 2-hour area forecasts and 4-day outlook | Every 10 min |
| [data.gov.sg / NEA (collection 1459)](https://data.gov.sg/collections/1459/view) | Realtime weather readings | Every 10 min |
| [data.gov.sg / NEA](https://data.gov.sg/datasets/d_fe37906a0182569d891506e815e819b7/view) | PSI and pollutant readings | Every 15 min |
| [data.gov.sg / PUB](https://data.gov.sg/datasets/d_f1404e08587ce555b9ea3f565e2eb9a3/view) | Flash-flood alerts | Every 2 min |
| [data.gov.sg / LTA](https://data.gov.sg/datasets/d_6cdb6b405b25aaaacbaf7689bcc6fae0/view) | Woodlands & Tuas checkpoint camera images | Every 2 min |
| [data.gov.sg / NEA](https://data.gov.sg/datasets/d_dbfabf16158d1b0e1c420627c0819168/view) | Dengue clusters | Every 6 h |
| [MOM](https://www.mom.gov.sg/employment-practices/public-holidays) | Public holidays | Every 24 h |
| [mytransport.sg](https://www.mytransport.sg/trainstatus) | MRT/LRT train status | Every 5 min |

## Development

See [CLAUDE.md](CLAUDE.md) for project structure, test instructions, and conventions.

```bash
pip install -r requirements_test.txt
pytest tests/ -v -m "not e2e"
```

`tests_ha/` runs the integration inside a real Home Assistant; `pytest tests/test_e2e.py -m e2e`
hits the live data sources. See CLAUDE.md for both.

## License

This project is licensed under the [MIT License](LICENSE).
