# Bloom Pot Refactor

`plant_facts.json` contains plant-facing records for the pilot set only. Each record now stores identity, legacy-backed evidence fields, the curated `controller_family` assignment, normalized `special_handling` tags, explicit `manual_review_reasons`, and provenance.

`controller_profiles.json` contains controller and hardware behavior only. Each profile now defines an explicit `moisture_target`, moisture cutoffs, fixed watering dose, cooldown, confirmation count, daily max dose, sensor model, substrate type, `autowater_enabled`, and any `manual_review_reasons`.

`plant_facts.schema.json` and `controller_profiles.schema.json` are machine-readable JSON Schemas for the two data files.

`bloom_controller.py` validates both JSON files against those schemas, enforces cross-file consistency, then runs the persistent watering controller. It keeps state such as reservoir level, low-reading confirmations, last watering time, and daily dose usage, then returns a pump decision with an explicit reason.

`DATA_MODEL_AUDIT.md` summarizes the schema audit, the field splits and removals, and the unresolved provenance limits that remain.

Run the tests with:

```bash
pytest -q
```
