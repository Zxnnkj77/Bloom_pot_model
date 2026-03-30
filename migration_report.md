# Migration Report

## Summary

- Total legacy species count: 168
- accepted_auto count: 155
- accepted_manual count: 10
- unresolved count: 3

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

- `category = succulent AND waterPreference in {drought_tolerant,dry_between}` -> `controller_family = succulent_fast_drain, controller_family_confidence = legacy_rule_based, migration_status = accepted_auto`
- `category = fern AND waterPreference in {evenly_moist,constantly_moist}` -> `controller_family = fern_high_moisture, controller_family_confidence = legacy_rule_based, migration_status = accepted_auto`
- `category in {tropical,bulb,herb,edible} AND waterPreference = evenly_moist` -> `controller_family = soil_even_moist, controller_family_confidence = legacy_rule_based, migration_status = accepted_auto`
- `category in {tropical,bulb,herb,edible} AND waterPreference = dry_between` -> `controller_family = soil_dry_between, controller_family_confidence = legacy_rule_based, migration_status = accepted_auto`
- `category = orchid` -> `controller_family = orchid_bark, controller_family_confidence = manual_review, migration_status = accepted_manual`
- `category = carnivorous` -> `controller_family = bog_carnivorous, controller_family_confidence = manual_review, migration_status = accepted_manual`
- `anything else unresolved` -> `write to unresolved_species.json with explicit reasons and no controller_family`

## Unresolved Species

- Christmas Cactus (`christmas_cactus`): unsupported_legacy_category_water_preference_combination
- Easter Cactus (`easter_cactus`): unsupported_legacy_category_water_preference_combination
- Orchid Cactus (`orchid_cactus`): unsupported_legacy_category_water_preference_combination
