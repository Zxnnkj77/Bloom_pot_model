# Bloom Pot Refactor

`plant_facts.json` contains plant-facing records for the pilot set only. Each record now stores identity, legacy-backed evidence fields, the curated `controller_family` assignment, normalized `special_handling` tags, explicit `manual_review_reasons`, and provenance.

`controller_profiles.json` contains controller and hardware behavior only. Each profile now defines an explicit `moisture_target`, moisture cutoffs, fixed watering dose, cooldown, confirmation count, daily max dose, sensor model, substrate type, `autowater_enabled`, and any `manual_review_reasons`.

`plant_facts.schema.json` and `controller_profiles.schema.json` are machine-readable JSON Schemas for the two data files.

`bloom_controller.py` validates both JSON files against those schemas, enforces cross-file consistency, then runs the persistent watering controller. It keeps state such as reservoir level, low-reading confirmations, last watering time, and daily dose usage, then returns a pump decision with an explicit reason.

`bloom_evaluation.py` adds deterministic replay evaluation on top of the controller. It validates replay fixtures against `controller_replay.schema.json`, replays ordered timestamped moisture observations through the current deterministic controller, records every decision trace step, and emits controller-focused summary metrics.

`controller_calibration.json` and `controller_calibration.py` define the current calibration-ready search surface. This is not tuning yet. The file only marks which existing numeric controller parameters are eligible for future search and constrains them to bounded ranges around the current profile values.

`unresolved_species.json` and `unresolved_species.schema.json` preserve the migrated unresolved-species catalog from the earlier catalog-expansion round. Replay evaluation uses that catalog to distinguish a known unresolved plant id from a truly unknown plant id; those cases now produce different rejection codes.

`DATA_MODEL_AUDIT.md` summarizes the schema audit, the field splits and removals, and the unresolved provenance limits that remain.

Run the tests with:

```bash
pytest -q
```

Run replay evaluation on one fixture or a directory of fixtures with:

```bash
python bloom_evaluation.py tests/fixtures/peace_lily_full_day.json
python bloom_evaluation.py tests/fixtures --summary-only
```

Run a scenario replay directly from Python with:

```python
from bloom_controller import BloomPotController

controller = BloomPotController()
scenario = controller.simulate_scenario(
    "peace_lily",
    [
        {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.04},
        {"timestamp": "2026-03-29T14:00:00+00:00", "soil_moisture": 0.20},
    ],
    initial_state={"reservoir_ml": 200.0},
)

print(scenario["trace"])
```

The evaluation layer is for honest controller comparison and future calibration work. It lets the repo answer questions like:

- How often did the current deterministic controller water in this trace?
- How much water did it dispense?
- How often was watering blocked by cooldown, daily budget, manual review, wet cutoff, or reservoir limits?
- How many hard-dry readings or confirmation-wait events occurred?

This is not ML training yet. There is no fitted model in this repo, no labeled biological outcome target, and no optimization step that changes parameters from data. The current controller is still a hand-specified deterministic policy with explicit families and thresholds.

Before any real training or data-driven calibration claim would be defensible, the project would need real observed data such as:

- Timestamped moisture readings tied to stable plant ids and actual controller actions.
- Logged irrigation outcomes, including how much water was dispensed and when.
- Reliable context about pot size, substrate, sensor model, and reservoir state for each run.
- A defined objective for fitting, such as reducing preventable blocks or matching human-reviewed interventions.
- An explicit fitting and validation workflow that produces new parameters from held-out data rather than manual edits.
