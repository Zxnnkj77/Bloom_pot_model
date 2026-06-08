# Bloom Pot Refactor

`plant_facts.json` contains the accepted migrated plant catalog. Each record stores only identity, legacy-backed evidence fields, migration metadata, normalized `special_handling` tags, explicit `manual_review_reasons`, and provenance.

`evidence_records.json` contains the Round 13 evidence bundle. This first real pack is intentionally small and only includes explicit legacy-backed migrated values, replay-derived observations from existing fixtures, controller-profile-backed readiness facts, and explicit derived readiness inferences with supporting evidence ids.

`plant_attributes.json` is the deterministic derived attribute catalog rebuilt from `evidence_records.json`. It groups the current evidence by plant, preserves explicit controller-ready vs blocked status, and keeps unresolved or manual-review-only controller attributes blocked. It is not used by the controller runtime in this round.

`evidence_coverage_summary.json` is the checked-in summary output for the current evidence bundle. It reports coverage by evidence class, plant id, and controller-ready vs blocked status.

`unresolved_species.json` contains legacy species that could not be mapped into an existing controller family by the explicit migration rules. These records preserve only the legacy-backed evidence fields, explicit unresolved reason tags, and provenance.

`controller_profiles.json` contains controller and hardware behavior only. Each profile now defines an explicit `moisture_target`, moisture cutoffs, fixed watering dose, cooldown, confirmation count, daily max dose, sensor model, substrate type, `autowater_enabled`, and any `manual_review_reasons`.

`plant_facts.schema.json`, `unresolved_species.schema.json`, `controller_profiles.schema.json`, and `controller_replay.schema.json` are machine-readable JSON Schemas for the data files and replay fixtures.

`bloom_controller.py` validates both JSON files against those schemas, enforces cross-file consistency, then runs the persistent watering controller. It keeps state such as reservoir level, low-reading confirmations, last watering time, and daily dose usage, then returns a pump decision with an explicit reason. It also exposes deterministic scenario replay with per-step decision traces so controller behavior can be inspected without mutating the caller's starting state.

`bloom_evaluation.py` validates replay fixtures against `controller_replay.schema.json`, replays ordered timestamped moisture observations through the current controller, records each trace step, and emits summary metrics for watering events, blocks, and rejections.

`bloom_calibration.py` adds Round 8 offline calibration search. It loads the current controller profiles as a baseline, applies bounded candidate overrides for existing numeric controller parameters on a copied profile set, replays the existing fixtures deterministically, and ranks candidates with an explicit heuristic score. It does not modify `controller_profiles.json`, and production thresholds remain unchanged in this round.

`bloom_calibration_report.py` adds Round 9 calibration comparison reporting. It evaluates the current baseline profile for one controller family, evaluates one or more candidate profiles for that same family on the same replay fixtures, ranks them with an explicit safety-first comparison rule, and writes `calibration_recommendations.json` plus `calibration_report.md`. It produces recommendations only and does not update `controller_profiles.json`.

`bloom_calibration_workflow.py` adds Round 10 end-to-end orchestration. It validates and loads replay data, evaluates the current baseline controller, runs the offline calibration search, compares the ranked candidates against baseline on the same deterministic replay set, and exports a final workflow JSON object plus Markdown report. It stays offline, does not apply controller changes automatically, and exits nonzero when no candidate survives or no candidate beats baseline.

`plant_evidence.py` adds Round 13 evidence validation and reporting. It validates `evidence_records.json` against the plant catalogs, controller profiles, and replay fixtures, rebuilds `plant_attributes.json` deterministically, and emits `evidence_coverage_summary.json`. Controller thresholds and runtime behavior remain unchanged in this round.

`DATA_MODEL_AUDIT.md` summarizes the schema audit, the field splits and removals, and the unresolved provenance limits that remain.

`migrate_legacy_catalog.py` is the deterministic migration pipeline. It reads `bloom_plant_schema.json.legacy-20260329-2145.bak`, applies the explicit category and water-preference mapping rules, writes `plant_facts.json`, `unresolved_species.json`, and `migration_report.md`, then validates the generated JSON against the schemas.

## Developer Workflow

Set up the local environment with:

```bash
python -m venv .venv
source .venv/bin/activate
make setup
```

Run the full local check workflow with:

```bash
make check
```

`make check` runs the test suite and validates that the checked-in evidence outputs still match the deterministic rebuild.

Run only the tests with:

```bash
make test
```

Validate the evidence layer with:

```bash
make validate-evidence
```

Rebuild the derived evidence outputs with:

```bash
python plant_evidence.py rebuild
```

Print the current evidence coverage summary with:

```bash
python plant_evidence.py summary
```

The evidence workflow validates the raw evidence records, cross-checks replay-derived observations against the existing fixtures, then rewrites `plant_attributes.json` and `evidence_coverage_summary.json` deterministically from the checked-in evidence pack.

Run replay evaluation on one fixture or a directory of fixtures with:

```bash
python bloom_evaluation.py tests/fixtures/peace_lily_full_day.json
python bloom_evaluation.py tests/fixtures --summary-only
```

Run offline calibration search against the replay fixtures with:

```bash
python bloom_calibration.py --family soil_even_moist --grid '{"watering_dose_ml":[40,60,80]}' tests/fixtures
```

Run the Round 9 comparison and recommendation export with:

```bash
python bloom_calibration_report.py \
  --family soil_even_moist \
  --candidates '[{"watering_dose_ml":40},{"watering_dose_ml":80}]' \
  --recommendation-output calibration_recommendations.json \
  --report-output calibration_report.md \
  tests/fixtures
```

Use `--allow-manual-review` only when you intentionally want to analyze a manual-review-only controller family. The Round 9 flow still produces recommendation artifacts only; it does not auto-apply controller profile changes.

Run the Round 10 workflow end-to-end with:

```bash
python bloom_calibration_workflow.py \
  --family soil_even_moist \
  --grid '{"watering_dose_ml":[40,60,80]}' \
  --workflow-output calibration_workflow.json \
  --report-output calibration_workflow.md \
  tests/fixtures
```

The workflow writes a structured JSON payload and Markdown report, then returns exit code `0` only when a candidate is actually recommended over baseline.
