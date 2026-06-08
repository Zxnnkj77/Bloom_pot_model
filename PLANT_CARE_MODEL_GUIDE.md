# Bloom Pot Plant Care Model Guide

This project contains a deterministic plant-care controller. It is not a trained
machine-learning model. Its purpose is to convert normalized soil moisture
readings into a clear plant-care decision:

- whether to water now
- how much water to dispense
- why that decision was made
- what controller state should be saved for the next reading

The current controller is useful for a real plant once your sensor pipeline can
convert raw sensor readings into the `soil_moisture` value expected by the
controller.

## What The Model Uses

The runtime decision is based on:

- `plant_id`: a plant id from `plant_facts.json`
- `soil_moisture`: a normalized moisture reading
- `timestamp`: an ISO 8601 timestamp with timezone
- `state`: saved controller state from the previous reading
- `controller_profiles.json`: plant-family watering thresholds and limits

The controller does not currently use light, temperature, humidity, image data,
or raw ADC values directly.

## Main Runtime Files

- `bloom_controller.py`: runtime controller API
- `bloom_evaluation.py`: command-line replay evaluator
- `plant_facts.json`: accepted plant catalog
- `controller_profiles.json`: watering thresholds by controller family
- `unresolved_species.json`: plants that should not be auto-loaded
- `tests/fixtures/*.json`: example replay inputs

## How The Decision Works

Each plant in `plant_facts.json` maps to a `controller_family`. Each controller
family has a profile in `controller_profiles.json`.

For every reading, the controller:

1. Validates the plant catalog and controller profiles.
2. Loads the plant by `plant_id`.
3. Finds the plant's controller family.
4. Normalizes `soil_moisture` to a `0.0` to `1.0` scale.
5. Rolls the daily watering budget forward if the date changed.
6. Applies safety and care rules in order:
   - block manual-review plants
   - block disabled controller families
   - block readings at or above the hard wet cutoff
   - block if the reservoir does not have enough water
   - block if cooldown is still active
   - block if the daily max dose would be exceeded
   - water immediately if the reading is at or below hard dry cutoff
   - otherwise water only after enough low readings are confirmed
   - otherwise return no-watering with a reason
7. Updates state if water was dispensed.
8. Returns a decision object.

## Controller Families

Current controller profiles:

| Family | Target moisture | Dose | Cooldown | Confirm low readings | Daily max | Autowater |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `soil_even_moist` | `0.18-0.28` | `60 ml` | `360 min` | `1` | `240 ml` | yes |
| `soil_dry_between` | `0.12-0.22` | `50 ml` | `720 min` | `2` | `150 ml` | yes |
| `succulent_fast_drain` | `0.05-0.12` | `35 ml` | `1440 min` | `2` | `70 ml` | yes |
| `fern_high_moisture` | `0.28-0.38` | `70 ml` | `240 min` | `1` | `280 ml` | yes |
| `orchid_bark` | `0.18-0.30` | `40 ml` | `720 min` | `2` | `80 ml` | no |
| `bog_carnivorous` | `0.35-0.50` | `60 ml` | `360 min` | `1` | `180 ml` | no |

The `orchid_bark` and `bog_carnivorous` families are intentionally blocked from
autowatering because they need manual review.

## Supported Plants

The catalog currently contains 165 accepted plants:

- 155 `accepted_auto` plants can produce automatic watering decisions.
- 10 `accepted_manual` plants are loaded but blocked from autowatering.
- 3 unresolved species are kept in `unresolved_species.json` and are rejected.

Examples of auto-enabled plant IDs:

- `peace_lily`
- `golden_pothos`
- `aloe_vera`
- `boston_fern`
- `spider_plant`
- `zz_plant`
- `jade_plant`
- `mint`

Manual-review examples:

- `moth_orchid`
- `dendrobium_orchid`
- `venus_fly_trap`
- `cape_sundew`

Use the exact `id` field from `plant_facts.json` as the runtime `plant_id`.

## Setup For Local Use

From the project folder:

```bash
cd bloom_pot
python -m venv .venv
source .venv/bin/activate
make setup
make check
```

`make check` runs the test suite and validates the evidence layer.

## Run An Existing Example

```bash
python bloom_evaluation.py tests/fixtures/peace_lily_full_day.json --summary-only
```

This replays timestamped moisture readings through the controller and prints a
summary of watering events, blocks, and final reservoir level.

## Run One Live Reading

```bash
python - <<'PY'
from bloom_controller import BloomPotController

controller = BloomPotController()
state = controller.initialize_state(reservoir_ml=240)

result = controller.step(
    "peace_lily",
    soil_moisture=0.10,
    timestamp="2026-06-07T12:00:00-07:00",
    state=state,
)

print(result)
PY
```

Example output shape:

```python
{
    "plant_id": "peace_lily",
    "controller_family": "soil_even_moist",
    "pump_on": True,
    "dose_ml": 60.0,
    "reason": "Confirmed 1 consecutive low readings below target band floor 0.18; fixed dose approved.",
    "state": {
        "reservoir_ml": 180.0,
        "low_reading_count": 0,
        "last_watered_at": "2026-06-07T12:00:00-07:00",
        "daily_dose_ml": 60.0,
        "daily_dose_day": "2026-06-07"
    },
    "soil_moisture": 0.1,
    "target_band": [0.18, 0.28]
}
```

## Convert Raw Sensor Data

The controller expects `soil_moisture` where:

- `0.0` means very dry
- `1.0` means very wet

Many capacitive soil sensors produce raw ADC values where dry soil reads higher
than wet soil. If your sensor behaves that way, calibrate with two measured
values:

- `dry_adc`: reading in dry air or fully dry soil
- `wet_adc`: reading in saturated soil or water, depending on your calibration
  standard

Then convert each raw reading:

```python
def clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(maximum, value))


def raw_adc_to_soil_moisture(raw_adc, dry_adc, wet_adc):
    moisture = (dry_adc - raw_adc) / (dry_adc - wet_adc)
    return clamp(moisture)
```

Example:

```python
dry_adc = 3200
wet_adc = 1300
raw_adc = 2500

soil_moisture = raw_adc_to_soil_moisture(raw_adc, dry_adc, wet_adc)
```

If your sensor reads higher when wet, use this instead:

```python
moisture = (raw_adc - dry_adc) / (wet_adc - dry_adc)
```

For real deployment, calibrate each sensor in the actual potting medium. Soil
mix, pot size, sensor depth, and sensor age can shift readings.

## Set Up One Actual Plant

1. Pick the plant.

   Find the closest plant in `plant_facts.json` and copy its `id`.

   Example:

   ```text
   peace_lily
   ```

2. Put the moisture sensor in the pot.

   Keep the sensor depth and position stable. Moving the sensor changes the
   calibration.

3. Calibrate the sensor.

   Record `dry_adc` and `wet_adc` for that sensor and medium. Store these values
   in your webapp or hardware config, not in the controller unless you add a
   sensor adapter module.

4. Initialize controller state.

   Each pot should have its own saved state:

   ```python
   controller = BloomPotController()
   state = controller.initialize_state(reservoir_ml=500)
   controller.save_state(state, "peace_lily_state.json")
   ```

5. On each sensor update:

   - read raw sensor value
   - convert raw value to `soil_moisture`
   - load the saved state
   - call `controller.step(...)`
   - save the returned `state`
   - show `reason` and `dose_ml` in the app
   - only activate a pump if `pump_on` is true

6. Start in recommendation-only mode.

   For the first few days, display recommendations but do not automatically run
   the pump. Compare decisions with the actual soil and plant condition. After
   confidence is good, enable pump control.

## Minimal App Integration Pattern

Recommended input from your webapp or device layer:

```json
{
  "plant_id": "peace_lily",
  "raw_adc": 2500,
  "dry_adc": 3200,
  "wet_adc": 1300,
  "timestamp": "2026-06-07T12:00:00-07:00",
  "reservoir_ml": 500
}
```

Your adapter converts `raw_adc` to `soil_moisture`, then calls:

```python
from bloom_controller import BloomPotController

controller = BloomPotController()
state = controller.load_state("peace_lily_state.json")

soil_moisture = raw_adc_to_soil_moisture(
    raw_adc=2500,
    dry_adc=3200,
    wet_adc=1300,
)

result = controller.step(
    plant_id="peace_lily",
    soil_moisture=soil_moisture,
    timestamp="2026-06-07T12:00:00-07:00",
    state=state,
)

controller.save_state(state, "peace_lily_state.json")
```

Return this to the app:

```json
{
  "plant_id": "peace_lily",
  "controller_family": "soil_even_moist",
  "soil_moisture": 0.368,
  "target_band": [0.18, 0.28],
  "pump_on": false,
  "dose_ml": 0.0,
  "reason": "Soil moisture 0.37 is above target band ceiling 0.28; no watering.",
  "state": {
    "reservoir_ml": 500.0,
    "low_reading_count": 0,
    "last_watered_at": null,
    "daily_dose_ml": 0.0,
    "daily_dose_day": "2026-06-07"
  }
}
```

Use the actual `result` object from the controller as the source of truth.

## State Management

State is required for correct real-world behavior. It tracks:

- remaining reservoir water
- consecutive low readings
- last watering timestamp
- water already dispensed today
- daily dose date

For one pot, one state file is enough.

For multiple plants, keep separate state per plant:

```text
state/
  peace_lily.json
  golden_pothos.json
  aloe_vera.json
```

If multiple plants share one reservoir, the current controller does not manage a
shared reservoir across plants. In that case, the webapp should maintain the
shared reservoir total and pass the current value into each plant decision.

## Safety Notes For Real Hardware

Use these safeguards before enabling an actual pump:

- Require timezone-aware timestamps.
- Clamp normalized moisture to `0.0-1.0`.
- Save state after every decision.
- Keep a manual override in the app.
- Keep a maximum pump runtime independent of this Python controller.
- Add reservoir-empty detection if the hardware supports it.
- Start with recommendation-only mode before enabling automatic watering.
- Do not enable automatic watering for manual-review plants.

## Current Gaps

The current project has the core controller, fixtures, tests, and plant catalog.
For a complete webapp-connected product, you still need:

- a raw sensor adapter
- per-device calibration storage
- per-plant state storage
- a web/API layer around `BloomPotController.step(...)`
- authentication and device ownership in the webapp
- a hardware command layer for pump control
- production logging of readings, decisions, and pump events

The next engineering step is to add a small adapter/API layer that accepts raw
sensor readings from the webapp or device, normalizes them, calls
`BloomPotController`, persists state, and returns the decision to the frontend.
