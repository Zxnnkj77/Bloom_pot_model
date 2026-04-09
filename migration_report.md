# Migration Report

## Summary

- Total legacy species count: 168
- accepted_auto count: 155
- accepted_manual count: 10
- unresolved count: 3
- accepted plant attribute bundles: 165
- accepted attribute evidence records: 495

## Counts By Legacy Category

- bulb: 8
- carnivorous: 3
- edible: 4
- fern: 7
- herb: 16
- orchid: 7
- succulent: 35
- tropical: 88

## Counts By Controller Family

- bog_carnivorous: 3
- fern_high_moisture: 7
- orchid_bark: 7
- soil_dry_between: 33
- soil_even_moist: 83
- succulent_fast_drain: 32

## Evidence Levels Used

- legacy_backed: 495

## Quarantined Legacy Fields

- The following legacy fields remain only in the backup and are not loaded into the active controller data model because this round does not claim biological validity for them:
- `EMax`
- `LMin`
- `Q`
- `RInit`
- `SMax`
- `WInit`
- `WMin`
- `alphaEvap`
- `cH`
- `cT`
- `kL`
- `kRL`
- `kRT`
- `kRW`
- `kSat`
- `kT`
- `kW`
- `nRetention`
- `p`
- `tauSat`
- `thetaCrit`
- `thetaFc`
- `thetaWp`

## Manual-Review Species

- Cape Sundew (`cape_sundew`): bog_carnivorous [legacy_category_carnivorous_requires_manual_review]
- Corsage Orchid (`corsage_orchid`): orchid_bark [legacy_category_orchid_requires_manual_review]
- Dendrobium Orchid (`dendrobium_orchid`): orchid_bark [legacy_category_orchid_requires_manual_review]
- Lycaste Orchid (`lycaste_orchid`): orchid_bark [legacy_category_orchid_requires_manual_review]
- Moth Orchid (`moth_orchid`): orchid_bark [legacy_category_orchid_requires_manual_review]
- Oncidium Orchid (`oncidium_orchid`): orchid_bark [legacy_category_orchid_requires_manual_review]
- Paphiopedilum concolor (`paphiopedilum_concolor`): orchid_bark [legacy_category_orchid_requires_manual_review]
- Slipper Orchid (`slipper_orchid`): orchid_bark [legacy_category_orchid_requires_manual_review]
- Tropical Pitcher Plant (`tropical_pitcher_plant`): bog_carnivorous [legacy_category_carnivorous_requires_manual_review]
- Venus Fly Trap (`venus_fly_trap`): bog_carnivorous [legacy_category_carnivorous_requires_manual_review]

## Mapping Rules Used

- `category = succulent AND waterPreference in {drought_tolerant,dry_between}` -> `controller_family = succulent_fast_drain, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred`
- `category = fern AND waterPreference in {evenly_moist,constantly_moist}` -> `controller_family = fern_high_moisture, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred`
- `category in {tropical,bulb,herb,edible} AND waterPreference = evenly_moist` -> `controller_family = soil_even_moist, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred`
- `category in {tropical,bulb,herb,edible} AND waterPreference = dry_between` -> `controller_family = soil_dry_between, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred`
- `category = orchid` -> `controller_family = orchid_bark, controller_assignment.review_status = accepted_manual, controller_assignment.evidence_level = inferred`
- `category = carnivorous` -> `controller_family = bog_carnivorous, controller_assignment.review_status = accepted_manual, controller_assignment.evidence_level = inferred`
- `anything else unresolved` -> `write to unresolved_species.json with resolution_status.evidence_level = unresolved and explicit supporting_attributes`

## Unresolved Species

- Christmas Cactus (`christmas_cactus`): unsupported_legacy_category_water_preference_combination
- Easter Cactus (`easter_cactus`): unsupported_legacy_category_water_preference_combination
- Orchid Cactus (`orchid_cactus`): unsupported_legacy_category_water_preference_combination
