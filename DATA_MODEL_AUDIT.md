# Data Model Audit

## Ambiguous fields in the previous model

- `growth_form`: freeform morphology guesses without an evidence source.
- `light_class`: coarse label derived from unknown rules while the legacy file stores numeric light preference.
- `humidity_class`: unsupported by the legacy backup and unused by the controller.
- `watering_style`: overlapped with both `controller_family` and the legacy water preference.
- `substrate_type` in `plant_facts.json`: duplicated controller-profile data and could drift from the active controller family.
- `temperature_band_c`: always `null`, with no defined structure for min/max semantics.
- `notes`: mixed provenance, justification, and operational warnings in free text.
- `target_band`: positional list with implicit ordering.
- `modifiers`: open-ended bag that mixed operational flags with review state.

## Renamed, removed, or split

- Removed unsupported plant fields: `growth_form`, `light_class`, `humidity_class`, `watering_style`, `substrate_type`, `temperature_band_c`, `notes`.
- Added explicit legacy evidence fields: `legacy_category`, `legacy_light_preference_lux`, `legacy_water_preference`.
- Added explicit provenance metadata under `provenance`.
- Kept `controller_family`, but added `controller_family_confidence` so the mapping is marked as curated rather than pretending it is a raw biological fact.
- Split controller `target_band` into `moisture_target.minimum` and `moisture_target.maximum`.
- Replaced controller `modifiers` with explicit `autowater_enabled` and `manual_review_reasons`.

## What changed

- Added machine-readable schemas in `plant_facts.schema.json` and `controller_profiles.schema.json`.
- Added runtime schema validation and cross-file validation in `bloom_controller.py`.
- Normalized `special_handling` and `manual_review_reasons` to list-only snake_case tags.
- Preserved controller numeric behavior while tightening the data contract around it.

## Remaining unresolved

- Controller profile numeric thresholds and doses are still curated controller parameters, not direct legacy-record constants.
- `controller_family` assignment is explicit but still curated; the legacy file evidences identity, category, light preference, and water preference, not the newer controller-family taxonomy.
