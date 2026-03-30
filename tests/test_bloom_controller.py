import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bloom_controller.py"
SPEC = importlib.util.spec_from_file_location("bloom_model_for_plants", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


BloomPotController = MODULE.BloomPotController
ModelValidationError = MODULE.ModelValidationError


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


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
                "controller_family_confidence": "curated",
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
                "controller_family_confidence": "curated",
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
                "controller_family_confidence": "curated",
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
