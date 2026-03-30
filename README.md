# Bloom Pot Refactor

`plant_facts.json` contains the accepted migrated plant catalog. Each record stores only identity, legacy-backed evidence fields, migration metadata, normalized `special_handling` tags, explicit `manual_review_reasons`, and provenance.

`unresolved_species.json` contains legacy species that could not be mapped into an existing controller family by the explicit migration rules. These records preserve only the legacy-backed evidence fields, explicit unresolved reason tags, and provenance.

`controller_profiles.json` contains controller and hardware behavior only. Each profile now defines an explicit `moisture_target`, moisture cutoffs, fixed watering dose, cooldown, confirmation count, daily max dose, sensor model, substrate type, `autowater_enabled`, and any `manual_review_reasons`.

`plant_facts.schema.json`, `unresolved_species.schema.json`, and `controller_profiles.schema.json` are machine-readable JSON Schemas for the data files.

`bloom_controller.py` validates both JSON files against those schemas, enforces cross-file consistency, then runs the persistent watering controller. It keeps state such as reservoir level, low-reading confirmations, last watering time, and daily dose usage, then returns a pump decision with an explicit reason.

`DATA_MODEL_AUDIT.md` summarizes the schema audit, the field splits and removals, and the unresolved provenance limits that remain.

`migrate_legacy_catalog.py` is the deterministic migration pipeline. It reads `bloom_plant_schema.json.legacy-20260329-2145.bak`, applies the explicit category and water-preference mapping rules, writes `plant_facts.json`, `unresolved_species.json`, and `migration_report.md`, then validates the generated JSON against the schemas.

Run the tests with:

```bash
pytest -q
```
