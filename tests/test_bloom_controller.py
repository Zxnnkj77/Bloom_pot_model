import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


BASE_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = BASE_DIR / "bloom_controller.py"
PLANT_FACTS_PATH = BASE_DIR / "plant_facts.json"
PLANT_FACTS_SCHEMA_PATH = BASE_DIR / "plant_facts.schema.json"
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
UNRESOLVED_SPECIES_SCHEMA_PATH = BASE_DIR / "unresolved_species.schema.json"
LEGACY_PATH = BASE_DIR / "bloom_plant_schema.json.legacy-20260329-2145.bak"

SPEC = importlib.util.spec_from_file_location("bloom_model_for_plants", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


BloomPotController = MODULE.BloomPotController
ModelValidationError = MODULE.ModelValidationError


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_json(path):
    return json.loads(path.read_text())


def validate_schema(payload, schema_path):
    schema = load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    return errors


def test_state_persistence(tmp_path):
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    state_path = tmp_path / "controller_state.json"
    controller.save_state(state, state_path)
    restored = controller.load_state(state_path)

    assert result["pump_on"] is True
    assert restored.to_dict() == state.to_dict()
    assert restored.last_watered_at == "2026-03-29T08:00:00+00:00"


def test_dry_trigger():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is True
    assert result["dose_ml"] == 60.0
    assert "hard dry cutoff" in result["reason"]


def test_wet_no_trigger():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    result = controller.step(
        "peace_lily",
        0.5,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is False
    assert result["dose_ml"] == 0.0
    assert "wet cutoff" in result["reason"]


def test_reservoir_decrement():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert state.reservoir_ml == 140.0


def test_cooldown_behavior():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    first = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )
    second = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T09:00:00+00:00",
        state=state,
    )

    assert first["pump_on"] is True
    assert second["pump_on"] is False
    assert "Cooldown active" in second["reason"]


def test_confirm_low_readings_behavior():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    first = controller.step(
        "golden_pothos",
        0.1,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )
    second = controller.step(
        "golden_pothos",
        0.1,
        timestamp="2026-03-29T21:00:00+00:00",
        state=state,
    )

    assert first["pump_on"] is False
    assert "waiting for 2 confirmations" in first["reason"]
    assert second["pump_on"] is True
    assert "Confirmed 2 consecutive low readings" in second["reason"]


def test_manual_no_autowater_families():
    controller = BloomPotController(default_reservoir_ml=200.0)

    for plant_id in ("moth_orchid", "venus_fly_trap"):
        state = controller.initialize_state(reservoir_ml=200.0)
        result = controller.step(
            plant_id,
            0.01,
            timestamp="2026-03-29T08:00:00+00:00",
            state=state,
        )

        assert result["pump_on"] is False
        assert result["dose_ml"] == 0.0
        assert "manual review required" in result["reason"]


def test_invalid_plant_schema_fails_loudly(tmp_path):
    plant_path = tmp_path / "plant_facts.json"
    controller_path = tmp_path / "controller_profiles.json"

    write_json(
        plant_path,
        [
            {
                "id": "test_plant",
                "common_name": "Test Plant",
                "scientific_name": "Testus plantus",
                "legacy_category": "tropical",
                "legacy_light_preference_lux": 1000,
                "legacy_water_preference": "evenly_moist",
                "controller_family": "soil_even_moist",
                "controller_family_confidence": "legacy_rule_based",
                "migration_status": "accepted_auto",
                "special_handling": "manual_review_required",
                "manual_review_reasons": [],
                "provenance": {
                    "source_file": "legacy.json",
                    "source_type": "legacy_backup_record",
                    "match_type": "exact_common_name_and_scientific_name",
                },
            }
        ],
    )
    write_json(
        controller_path,
        {
            "soil_even_moist": {
                "moisture_target": {"minimum": 0.18, "maximum": 0.28},
                "hard_dry_cutoff": 0.05,
                "hard_wet_cutoff": 0.38,
                "watering_dose_ml": 60,
                "cooldown_minutes": 360,
                "confirm_low_readings": 1,
                "max_daily_dose_ml": 240,
                "sensor_model": "capacitive_soil_v1",
                "substrate_type": "potting_mix",
                "autowater_enabled": True,
                "manual_review_reasons": [],
            }
        },
    )

    with pytest.raises(ModelValidationError, match="Schema validation failed"):
        BloomPotController(
            plant_facts_path=plant_path,
            controller_profiles_path=controller_path,
        )


def test_unknown_controller_family_reference_fails_loudly(tmp_path):
    plant_path = tmp_path / "plant_facts.json"
    controller_path = tmp_path / "controller_profiles.json"

    write_json(
        plant_path,
        [
            {
                "id": "test_plant",
                "common_name": "Test Plant",
                "scientific_name": "Testus plantus",
                "legacy_category": "tropical",
                "legacy_light_preference_lux": 1000,
                "legacy_water_preference": "evenly_moist",
                "controller_family": "missing_family",
                "controller_family_confidence": "legacy_rule_based",
                "migration_status": "accepted_auto",
                "special_handling": [],
                "manual_review_reasons": [],
                "provenance": {
                    "source_file": "legacy.json",
                    "source_type": "legacy_backup_record",
                    "match_type": "exact_common_name_and_scientific_name",
                },
            }
        ],
    )
    write_json(
        controller_path,
        {
            "soil_even_moist": {
                "moisture_target": {"minimum": 0.18, "maximum": 0.28},
                "hard_dry_cutoff": 0.05,
                "hard_wet_cutoff": 0.38,
                "watering_dose_ml": 60,
                "cooldown_minutes": 360,
                "confirm_low_readings": 1,
                "max_daily_dose_ml": 240,
                "sensor_model": "capacitive_soil_v1",
                "substrate_type": "potting_mix",
                "autowater_enabled": True,
                "manual_review_reasons": [],
            }
        },
    )

    with pytest.raises(ModelValidationError, match="unknown controller family"):
        BloomPotController(
            plant_facts_path=plant_path,
            controller_profiles_path=controller_path,
        )


def test_invalid_controller_profile_thresholds_fail_loudly(tmp_path):
    plant_path = tmp_path / "plant_facts.json"
    controller_path = tmp_path / "controller_profiles.json"

    write_json(
        plant_path,
        [
            {
                "id": "test_plant",
                "common_name": "Test Plant",
                "scientific_name": "Testus plantus",
                "legacy_category": "tropical",
                "legacy_light_preference_lux": 1000,
                "legacy_water_preference": "evenly_moist",
                "controller_family": "soil_even_moist",
                "controller_family_confidence": "legacy_rule_based",
                "migration_status": "accepted_auto",
                "special_handling": [],
                "manual_review_reasons": [],
                "provenance": {
                    "source_file": "legacy.json",
                    "source_type": "legacy_backup_record",
                    "match_type": "exact_common_name_and_scientific_name",
                },
            }
        ],
    )
    write_json(
        controller_path,
        {
            "soil_even_moist": {
                "moisture_target": {"minimum": 0.04, "maximum": 0.28},
                "hard_dry_cutoff": 0.05,
                "hard_wet_cutoff": 0.38,
                "watering_dose_ml": 60,
                "cooldown_minutes": 360,
                "confirm_low_readings": 1,
                "max_daily_dose_ml": 240,
                "sensor_model": "capacitive_soil_v1",
                "substrate_type": "potting_mix",
                "autowater_enabled": True,
                "manual_review_reasons": [],
            }
        },
    )

    with pytest.raises(ModelValidationError, match="inconsistent moisture thresholds"):
        BloomPotController(
            plant_facts_path=plant_path,
            controller_profiles_path=controller_path,
        )


def test_expanded_plant_facts_validate_against_schema():
    plant_facts = load_json(PLANT_FACTS_PATH)
    errors = validate_schema(plant_facts, PLANT_FACTS_SCHEMA_PATH)

    assert errors == []
    assert len(plant_facts) > 10


def test_unresolved_species_file_shape():
    unresolved_species = load_json(UNRESOLVED_SPECIES_PATH)
    errors = validate_schema(unresolved_species, UNRESOLVED_SPECIES_SCHEMA_PATH)

    assert errors == []
    assert all(record["unresolved_reasons"] for record in unresolved_species)


def test_controller_family_confidence_enum_handling():
    base_record = {
        "id": "test_plant",
        "common_name": "Test Plant",
        "scientific_name": "Testus plantus",
        "legacy_category": "tropical",
        "legacy_light_preference_lux": 1000,
        "legacy_water_preference": "evenly_moist",
        "controller_family": "soil_even_moist",
        "migration_status": "accepted_auto",
        "special_handling": [],
        "manual_review_reasons": [],
        "provenance": {
            "source_file": "legacy.json",
            "source_type": "legacy_backup_record",
            "match_type": "exact_common_name_and_scientific_name",
        },
    }

    for confidence in ("legacy_direct", "legacy_rule_based", "manual_review"):
        payload = [{**base_record, "controller_family_confidence": confidence}]
        assert validate_schema(payload, PLANT_FACTS_SCHEMA_PATH) == []

    invalid_payload = [{**base_record, "controller_family_confidence": "curated"}]
    assert validate_schema(invalid_payload, PLANT_FACTS_SCHEMA_PATH)


def test_migration_status_enum_handling():
    base_record = {
        "id": "test_plant",
        "common_name": "Test Plant",
        "scientific_name": "Testus plantus",
        "legacy_category": "tropical",
        "legacy_light_preference_lux": 1000,
        "legacy_water_preference": "evenly_moist",
        "controller_family": "soil_even_moist",
        "controller_family_confidence": "legacy_rule_based",
        "special_handling": [],
        "manual_review_reasons": [],
        "provenance": {
            "source_file": "legacy.json",
            "source_type": "legacy_backup_record",
            "match_type": "exact_common_name_and_scientific_name",
        },
    }

    for status in ("accepted_auto", "accepted_manual"):
        payload = [{**base_record, "migration_status": status}]
        assert validate_schema(payload, PLANT_FACTS_SCHEMA_PATH) == []

    invalid_payload = [{**base_record, "migration_status": "unresolved"}]
    assert validate_schema(invalid_payload, PLANT_FACTS_SCHEMA_PATH)


def test_no_accepted_plant_record_missing_controller_family():
    plant_facts = load_json(PLANT_FACTS_PATH)
    missing_controller_family = [
        record["id"] for record in plant_facts if not record.get("controller_family")
    ]

    assert missing_controller_family == []


def test_no_unresolved_species_accidentally_included_in_plant_facts():
    plant_ids = {record["id"] for record in load_json(PLANT_FACTS_PATH)}
    unresolved_ids = {record["id"] for record in load_json(UNRESOLVED_SPECIES_PATH)}

    assert plant_ids.isdisjoint(unresolved_ids)


def test_migration_outputs_cover_every_legacy_species_once():
    plant_facts = load_json(PLANT_FACTS_PATH)
    unresolved_species = load_json(UNRESOLVED_SPECIES_PATH)
    legacy_records = load_json(LEGACY_PATH)

    assert len(plant_facts) + len(unresolved_species) == len(legacy_records)
