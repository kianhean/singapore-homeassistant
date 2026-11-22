# Automation recipes

[← Back to the README](../README.md)

Automations to paste into an automation's YAML editor (**Settings → Automations & scenes →
Create automation → ⋮ → Edit in YAML**). Change `notify.notify` to your phone's notify action,
for example `notify.mobile_app_your_phone`. Most of the weather, safety and transport ones also
come as [one-click blueprints](../README.md#one-click-blueprints) with drop-downs instead of YAML.

- [Weather, safety and transport](#weather-safety-and-transport): lightning, MRT/LRT, floods,
  haze, COE, UV
- [Daily life and driving](#daily-life-and-driving): prayer times, pump prices, beaches, mall
  carparks
- [With a linked SP account](#with-a-linked-sp-account): new bill, due date, Green Goals,
  prepaid credit

## Weather, safety and transport

<details>
<summary><b>⚡ Warn me when lightning is close</b></summary>

```yaml
alias: Lightning nearby
triggers:
  - trigger: numeric_state
    entity_id: sensor.nea_nearest_lightning
    below: 5
actions:
  - action: notify.notify
    data:
      title: Lightning nearby
      message: >-
        Lightning {{ states('sensor.nea_nearest_lightning') | float | round(1) }} km away
        ({{ state_attr('sensor.nea_nearest_lightning', 'type') | replace('_', ' ') }})
```

Cloud-to-ground strikes are the dangerous kind. NEA notes that most strikes fall within
5–6 km of a thunderstorm, so 5 km is a sensible threshold for getting out of the pool.
</details>

<details>
<summary><b>🚇 Tell me if my MRT line is disrupted before work</b></summary>

```yaml
alias: Circle Line disruption
triggers:
  - trigger: state
    entity_id: sensor.mrt_lrt_circle_line_status
    to: disruption
conditions:
  - condition: time
    after: "06:30:00"
    before: "09:30:00"
    weekday: [mon, tue, wed, thu, fri]
actions:
  - action: notify.notify
    data:
      title: "Circle Line: Disruption"
      message: "{{ state_attr('sensor.mrt_lrt_status', 'details') }}"
```
</details>

<details>
<summary><b>🌊 Flash-flood alert</b></summary>

```yaml
alias: PUB flood alert
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
</details>

<details>
<summary><b>😷 Turn on the air purifier when the haze is bad</b></summary>

```yaml
alias: Haze mode
triggers:
  - trigger: numeric_state
    entity_id: sensor.air_quality_psi
    above: 100
actions:
  - action: fan.turn_on
    target:
      entity_id: fan.living_room_purifier
  - action: notify.notify
    data:
      message: >-
        PSI is {{ states('sensor.air_quality_psi') }}
        ({{ state_attr('sensor.air_quality_psi', 'band') | replace('_', ' ') }}). Purifier on.
```
</details>

<details>
<summary><b>🚗 Send me the COE results</b></summary>

```yaml
alias: COE results
triggers:
  - trigger: state
    entity_id: sensor.coe_category_a
    not_from: [unavailable, unknown]
    not_to: [unavailable, unknown]
actions:
  - action: notify.notify
    data:
      title: COE results
      message: >-
        Cat A {{ '{:,}'.format(states('sensor.coe_category_a') | int) }} SGD ·
        Cat B {{ '{:,}'.format(states('sensor.coe_category_b') | int) }} SGD ·
        Cat E {{ '{:,}'.format(states('sensor.coe_category_e') | int) }} SGD
```
</details>

<details>
<summary><b>☀️ Sunscreen reminder when the UV is high</b></summary>

```yaml
alias: High UV
triggers:
  - trigger: state
    entity_id: sensor.nea_uv_exposure
    to: [high, very_high, extreme]
conditions:
  - condition: time
    weekday: [sat, sun]
actions:
  - action: notify.notify
    data:
      title: UV is {{ states('sensor.nea_uv_exposure') | replace('_', ' ') }}
      message: >-
        UV index {{ states('sensor.nea_uv_index') }}. Sunscreen and a hat if you're
        heading out.
```
</details>

## Daily life and driving

These use opt-in sources: tick them under **Settings → Devices & Services → Singapore →
Configure** first ([which ones are opt-in](../README.md#data-sources-at-a-glance)).

<details>
<summary><b>🕌 Remind me 10 minutes before each prayer</b></summary>

Needs **Prayer times (MUIS)**. `sensor.prayer_times_next_prayer` is a timestamp, so a time
trigger can fire relative to it; it moves on to the following prayer as each one passes.

```yaml
alias: Prayer reminder
triggers:
  - trigger: time
    at:
      entity_id: sensor.prayer_times_next_prayer
      offset: "-00:10:00"
actions:
  - action: notify.notify
    data:
      title: "{{ state_attr('sensor.prayer_times_next_prayer', 'prayer') | title }} in 10 minutes"
      message: >-
        {{ state_attr('sensor.prayer_times_next_prayer', 'prayer') | title }} is at
        {{ (states('sensor.prayer_times_next_prayer') | as_datetime | as_local).strftime('%H:%M') }}.
```

For one prayer only, use that prayer's sensor instead, for example
`sensor.prayer_times_maghrib` to switch on the porch light at Maghrib.
</details>

<details>
<summary><b>⛽ Tell me when 95 pump prices change</b></summary>

Needs **Pump prices (Caltex, SPC, Shell)**. Fires when any brand's 95 price changes and lists
all three, cheapest first.

```yaml
alias: 95 pump prices changed
triggers:
  - trigger: state
    entity_id: [sensor.caltex_95, sensor.spc_95, sensor.shell_95]
    not_from: [unavailable, unknown]
    not_to: [unavailable, unknown]
actions:
  - action: notify.notify
    data:
      title: 95 pump prices changed
      message: >-
        {% set ns = namespace(rows=[]) %}
        {% for brand, entity in [('Caltex', 'sensor.caltex_95'), ('SPC', 'sensor.spc_95'),
                                 ('Shell', 'sensor.shell_95')] if has_value(entity) %}
          {% set ns.rows = ns.rows + [(states(entity) | float, brand)] %}
        {% endfor %}
        {% for price, brand in ns.rows | sort %}{{ brand }} {{ '%.2f' | format(price) }}{{ ' · ' if not loop.last }}{% endfor %} SGD/L
```

The prices are before discounts; Caltex and Shell also have a `discounted_price` attribute.
</details>

<details>
<summary><b>🏖️ Check the beach before the weekend</b></summary>

Beach water quality is on by default. On Friday evening, warn if NEA's band for East Coast
isn't normal.

```yaml
alias: East Coast water quality
triggers:
  - trigger: time
    at: "18:00:00"
conditions:
  - condition: time
    weekday: [fri]
  - condition: not
    conditions:
      - condition: state
        entity_id: sensor.beaches_east_coast
        state: [normal, unavailable, unknown]
actions:
  - action: notify.notify
    data:
      title: "East Coast: {{ states('sensor.beaches_east_coast') }} bacteria levels"
      message: "{{ state_attr('sensor.beaches_east_coast', 'advisory') }}"
```

The other beaches are `sensor.beaches_changi`, `_pasir_ris`, `_punggol`, `_seletar`,
`_sembawang` and `_sentosa`.
</details>

<details>
<summary><b>🛍️ Mall carpark nearly full</b></summary>

Needs **Mall carparks (LTA DataMall)** and an LTA key, or use a CapitaLand mall
(`sensor.capitaland_carparks_<mall>`) the same way. Warns on weekend afternoons when the
carpark is filling up, before you set off.

```yaml
alias: Suntec City carpark nearly full
triggers:
  - trigger: numeric_state
    entity_id: sensor.mall_carparks_suntec_city
    below: 50
conditions:
  - condition: time
    after: "11:00:00"
    before: "19:00:00"
    weekday: [sat, sun]
actions:
  - action: notify.notify
    data:
      title: Suntec City carpark nearly full
      message: "{{ states('sensor.mall_carparks_suntec_city') }} car lots left."
```
</details>

## With a linked SP account

Replace `1_example_road_singapore` with your SP account device's slug (it's named after your
premise address; the device page shows the entity IDs).

The [SP bill blueprint](../README.md#one-click-blueprints) does both of the next two in one automation.

<details>
<summary><b>🧾 Tell me when a new SP bill arrives</b></summary>

```yaml
alias: New SP bill
triggers:
  - trigger: state
    entity_id: sensor.1_example_road_singapore_last_bill
    attribute: bill_date
conditions:
  # Skip restarts: only a real change from one bill to the next.
  - condition: template
    value_template: >-
      {{ trigger.from_state is not none
         and trigger.from_state.attributes.get('bill_date') is not none }}
actions:
  - action: notify.notify
    data:
      title: New SP bill
      message: >-
        {% set due = state_attr('sensor.1_example_road_singapore_last_bill', 'due_date') %}
        {{ states('sensor.1_example_road_singapore_last_bill') }} SGD{% if due %},
        due {{ (as_datetime(due) | as_local).strftime('%-d %b') }}{% endif %}.
```
</details>

<details>
<summary><b>💸 Remind me to pay before the due date (skips GIRO)</b></summary>

```yaml
alias: SP bill due soon
triggers:
  - trigger: time
    at: "09:00:00"
conditions:
  - condition: numeric_state
    entity_id: sensor.1_example_road_singapore_amount_due
    above: 0
  - condition: template
    value_template: >-
      {% set due = state_attr('sensor.1_example_road_singapore_last_bill', 'due_date') %}
      {{ due is not none
         and not state_attr('sensor.1_example_road_singapore_amount_due', 'giro_enabled')
         and (as_datetime(due) - now()).days < 3 }}
actions:
  - action: notify.notify
    data:
      title: SP bill due soon
      message: "{{ states('sensor.1_example_road_singapore_amount_due') }} SGD outstanding."
```
</details>

<details>
<summary><b>🎯 Warn me when I pass my Green Goals target</b></summary>

```yaml
alias: Over the Green Goals target
triggers:
  - trigger: template
    value_template: >-
      {{ states('sensor.1_example_road_singapore_electricity_this_month') | float(0)
         > state_attr('sensor.1_example_road_singapore_electricity_this_month', 'goal_target') | float(1e9) }}
actions:
  - action: notify.notify
    data:
      title: Over this month's electricity target
      message: >-
        {{ states('sensor.1_example_road_singapore_electricity_this_month') }} kWh used,
        target {{ state_attr('sensor.1_example_road_singapore_electricity_this_month', 'goal_target') }} kWh.
```
</details>

<details>
<summary><b>🪫 Prepaid credit running low</b></summary>

For prepaid (PPMS) accounts only; the sensor exists when SP reports a prepaid balance.

```yaml
alias: SP prepaid credit low
triggers:
  - trigger: numeric_state
    entity_id: sensor.1_example_road_singapore_prepaid_credit
    below: 20
actions:
  - action: notify.notify
    data:
      title: SP prepaid credit low
      message: "{{ states('sensor.1_example_road_singapore_prepaid_credit') }} SGD left. Top up in the SP app."
```
</details>
