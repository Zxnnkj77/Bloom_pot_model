# Bloom Pot Refactor

`plant_facts.json` contains plant-facing records for the pilot set only. Each record now stores identity, legacy-backed evidence fields, the curated `controller_family` assignment, normalized `special_handling` tags, explicit `manual_review_reasons`, and provenance.

`controller_profiles.json` contains controller and hardware behavior only. Each profile now defines an explicit `moisture_target`, moisture cutoffs, fixed watering dose, cooldown, confirmation count, daily max dose, sensor model, substrate type, `autowater_enabled`, and any `manual_review_reasons`.

`plant_facts.schema.json` and `controller_profiles.schema.json` are machine-readable JSON Schemas for the two data files.

`bloom_controller.py` validates both JSON files against those schemas, enforces cross-file consistency, then runs the persistent watering controller. It keeps state such as reservoir level, low-reading confirmations, last watering time, and daily dose usage, then returns a pump decision with an explicit reason.

It also exposes `simulate_scenario(...)` for deterministic scenario replay from ordered timestamped moisture readings, returning a full per-step trace and final controller state.

`DATA_MODEL_AUDIT.md` summarizes the schema audit, the field splits and removals, and the unresolved provenance limits that remain.

Run the tests with:

```bash
pytest -q
```

Run a scenario replay from Python with:

```python
from bloom_controller import BloomPotController

controller = BloomPotController()
scenario = controller.simulate_scenario(
    "peace_lily",
    [
        {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.04},
        {"timestamp": "2026-03-29T14:00:00+00:00", "soil_moisture": 0.20},
    ],
    initial_reservoir_ml=200.0,
)

print(scenario["trace"])
```
