# Bloom Pot Refactor

`plant_facts.json` contains the accepted migrated plant catalog. Each record stores only identity, legacy-backed evidence fields, migration metadata, normalized `special_handling` tags, explicit `manual_review_reasons`, and provenance.

`unresolved_species.json` contains legacy species that could not be mapped into an existing controller family by the explicit migration rules. These records preserve only the legacy-backed evidence fields, explicit unresolved reason tags, and provenance.

`controller_profiles.json` contains controller and hardware behavior only. Each profile now defines an explicit `moisture_target`, moisture cutoffs, fixed watering dose, cooldown, confirmation count, daily max dose, sensor model, substrate type, `autowater_enabled`, and any `manual_review_reasons`.

`plant_facts.schema.json`, `unresolved_species.schema.json`, `controller_profiles.schema.json`, and `controller_replay.schema.json` are machine-readable JSON Schemas for the data files and replay fixtures.

`bloom_controller.py` validates both JSON files against those schemas, enforces cross-file consistency, then runs the persistent watering controller. It keeps state such as reservoir level, low-reading confirmations, last watering time, and daily dose usage, then returns a pump decision with an explicit reason. It also exposes deterministic scenario replay with per-step decision traces so controller behavior can be inspected without mutating the caller's starting state.

`bloom_evaluation.py` validates replay fixtures against `controller_replay.schema.json`, replays ordered timestamped moisture observations through the current controller, records each trace step, and emits a deterministic report with per-scenario traces, per-scenario summaries, family-level summaries, and an overall summary. The scenario metrics now include replay step counts, watering totals, below/inside/above-target counts, block-reason counts, hard-dry trigger counts, and rejection counts so later calibration work can compare controller behavior without changing thresholds.

`DATA_MODEL_AUDIT.md` summarizes the schema audit, the field splits and removals, and the unresolved provenance limits that remain.

`migrate_legacy_catalog.py` is the deterministic migration pipeline. It reads `bloom_plant_schema.json.legacy-20260329-2145.bak`, applies the explicit category and water-preference mapping rules, writes `plant_facts.json`, `unresolved_species.json`, and `migration_report.md`, then validates the generated JSON against the schemas.

Run the tests with:

```bash
pytest -q
```

Run replay evaluation on one fixture or a directory of fixtures with:

```bash
python bloom_evaluation.py tests/fixtures/peace_lily_full_day.json
python bloom_evaluation.py tests/fixtures --summary-only
```

`--summary-only` prints the scenario summaries, family summaries, and overall summary without the per-step replay traces.
