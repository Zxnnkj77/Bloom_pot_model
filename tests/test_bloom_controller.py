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
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_fixture(name):
    return json.loads((FIXTURES_DIR / name).read_text())


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
    assert result["reason_code"] == "hard_dry_approved"
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
    assert result["reason_code"] == "wet_cutoff_block"
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
    assert first["reason_code"] == "confirmation_wait"
    assert "waiting for 2 confirmations" in first["reason"]
    assert second["pump_on"] is True
    assert second["reason_code"] == "confirmed_low_approved"
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


def test_simulate_scenario_hard_dry_immediate_watering():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "peace_lily",
        [{"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.03}],
        initial_reservoir_ml=200.0,
    )

    trace = scenario["trace"]
    assert len(trace) == 1
    assert trace[0]["pump_on"] is True
    assert trace[0]["dose_ml"] == 60.0
    assert trace[0]["reservoir_ml_before"] == 200.0
    assert trace[0]["reservoir_ml_after"] == 140.0
    assert trace[0]["low_reading_count_before"] == 0
    assert trace[0]["low_reading_count_after"] == 0
    assert trace[0]["daily_dose_ml_before"] == 0.0
    assert trace[0]["daily_dose_ml_after"] == 60.0
    assert trace[0]["daily_dose_day_after"] == "2026-03-29"
    assert "hard dry cutoff" in trace[0]["reason"]
    assert scenario["final_state"]["reservoir_ml"] == 140.0


def test_simulate_scenario_confirm_low_readings_across_multiple_readings():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-29T21:00:00+00:00", "soil_moisture": 0.10},
        ],
        initial_reservoir_ml=200.0,
    )

    first, second = scenario["trace"]
    assert first["pump_on"] is False
    assert first["low_reading_count_after"] == 1
    assert "waiting for 2 confirmations" in first["reason"]
    assert second["pump_on"] is True
    assert second["low_reading_count_before"] == 1
    assert second["low_reading_count_after"] == 0
    assert "Confirmed 2 consecutive low readings" in second["reason"]


def test_simulate_scenario_cooldown_blocked_low_reading_does_not_increment_confirmation_count():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.04},
            {"timestamp": "2026-03-29T09:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-29T21:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-30T10:00:00+00:00", "soil_moisture": 0.10},
        ],
        initial_reservoir_ml=200.0,
    )

    trace = scenario["trace"]
    assert [step["pump_on"] for step in trace] == [True, False, False, True]
    assert trace[1]["low_reading_count_before"] == 0
    assert trace[1]["low_reading_count_after"] == 0
    assert "Cooldown active" in trace[1]["reason"]
    assert trace[2]["low_reading_count_before"] == 0
    assert trace[2]["low_reading_count_after"] == 1
    assert "waiting for 2 confirmations" in trace[2]["reason"]
    assert trace[3]["low_reading_count_before"] == 1
    assert "Confirmed 2 consecutive low readings" in trace[3]["reason"]


def test_simulate_scenario_reservoir_blocked_low_reading_does_not_increment_confirmation_count():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [{"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.10}],
        initial_state={"reservoir_ml": 40.0, "low_reading_count": 1},
    )

    trace = scenario["trace"]
    assert trace[0]["pump_on"] is False
    assert trace[0]["low_reading_count_before"] == 1
    assert trace[0]["low_reading_count_after"] == 1
    assert "Reservoir does not contain the fixed watering dose" in trace[0]["reason"]


def test_simulate_scenario_max_daily_budget_blocked_low_reading_does_not_increment_confirmation_count():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [{"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.10}],
        initial_state={
            "reservoir_ml": 200.0,
            "low_reading_count": 1,
            "daily_dose_ml": 150.0,
            "daily_dose_day": "2026-03-29",
        },
    )

    trace = scenario["trace"]
    assert trace[0]["pump_on"] is False
    assert trace[0]["low_reading_count_before"] == 1
    assert trace[0]["low_reading_count_after"] == 1
    assert "Max daily fixed-dose budget reached" in trace[0]["reason"]


def test_simulate_scenario_eligible_low_readings_still_increment_and_confirm_correctly():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-29T21:00:00+00:00", "soil_moisture": 0.10},
        ],
        initial_reservoir_ml=200.0,
    )

    trace = scenario["trace"]
    assert trace[0]["pump_on"] is False
    assert trace[0]["low_reading_count_before"] == 0
    assert trace[0]["low_reading_count_after"] == 1
    assert trace[1]["pump_on"] is True
    assert trace[1]["low_reading_count_before"] == 1
    assert trace[1]["low_reading_count_after"] == 0
    assert "Confirmed 2 consecutive low readings" in trace[1]["reason"]


def test_simulate_scenario_low_reading_reset_after_recovery():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-29T09:00:00+00:00", "soil_moisture": 0.18},
            {"timestamp": "2026-03-29T10:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-29T11:00:00+00:00", "soil_moisture": 0.10},
        ],
        initial_reservoir_ml=200.0,
    )

    assert [step["pump_on"] for step in scenario["trace"]] == [False, False, False, True]
    assert scenario["trace"][1]["low_reading_count_after"] == 0
    assert scenario["trace"][2]["low_reading_count_before"] == 0
    assert scenario["trace"][3]["low_reading_count_before"] == 1


def test_simulate_scenario_hard_wet_suppression_resets_low_counter():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-29T09:00:00+00:00", "soil_moisture": 0.35},
            {"timestamp": "2026-03-29T10:00:00+00:00", "soil_moisture": 0.10},
        ],
        initial_reservoir_ml=200.0,
    )

    assert [step["pump_on"] for step in scenario["trace"]] == [False, False, False]
    assert scenario["trace"][0]["low_reading_count_after"] == 1
    assert scenario["trace"][1]["low_reading_count_after"] == 0
    assert "wet cutoff" in scenario["trace"][1]["reason"]
    assert scenario["trace"][2]["low_reading_count_before"] == 0


def test_simulate_scenario_cooldown_blocks_until_exact_boundary():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "peace_lily",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.03},
            {"timestamp": "2026-03-29T13:59:00+00:00", "soil_moisture": 0.03},
            {"timestamp": "2026-03-29T14:00:00+00:00", "soil_moisture": 0.03},
        ],
        initial_reservoir_ml=200.0,
    )

    assert [step["pump_on"] for step in scenario["trace"]] == [True, False, True]
    assert "Cooldown active" in scenario["trace"][1]["reason"]
    assert scenario["trace"][1]["reservoir_ml_after"] == 140.0
    assert scenario["trace"][2]["reservoir_ml_before"] == 140.0
    assert scenario["trace"][2]["reservoir_ml_after"] == 80.0


def test_simulate_scenario_max_daily_dose_reached():
    controller = BloomPotController(default_reservoir_ml=500.0)

    scenario = controller.simulate_scenario(
        "boston_fern",
        [
            {"timestamp": "2026-03-29T00:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-29T04:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-29T12:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-29T16:00:00+00:00", "soil_moisture": 0.05},
        ],
        initial_reservoir_ml=500.0,
    )

    assert [step["pump_on"] for step in scenario["trace"]] == [True, True, True, True, False]
    assert scenario["trace"][3]["daily_dose_ml_after"] == 280.0
    assert scenario["trace"][4]["daily_dose_ml_before"] == 280.0
    assert scenario["trace"][4]["daily_dose_ml_after"] == 280.0
    assert "Max daily fixed-dose budget reached" in scenario["trace"][4]["reason"]


def test_simulate_scenario_next_day_rollover_resets_daily_budget():
    controller = BloomPotController(default_reservoir_ml=500.0)

    scenario = controller.simulate_scenario(
        "boston_fern",
        [
            {"timestamp": "2026-03-29T00:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-29T04:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-29T12:00:00+00:00", "soil_moisture": 0.05},
            {"timestamp": "2026-03-30T00:00:00+00:00", "soil_moisture": 0.05},
        ],
        initial_reservoir_ml=500.0,
    )

    assert [step["pump_on"] for step in scenario["trace"]] == [True, True, True, True, True]
    assert scenario["trace"][4]["daily_dose_ml_before"] == 280.0
    assert scenario["trace"][4]["daily_dose_ml_after"] == 70.0
    assert scenario["trace"][4]["daily_dose_day_after"] == "2026-03-30"


def test_simulate_scenario_manual_review_families_always_blocked():
    controller = BloomPotController(default_reservoir_ml=200.0)

    for plant_id in ("moth_orchid", "venus_fly_trap"):
        scenario = controller.simulate_scenario(
            plant_id,
            [
                {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.01},
                {"timestamp": "2026-03-29T20:00:00+00:00", "soil_moisture": 0.01},
            ],
            initial_reservoir_ml=200.0,
        )

        assert [step["pump_on"] for step in scenario["trace"]] == [False, False]
        assert [step["dose_ml"] for step in scenario["trace"]] == [0.0, 0.0]
        assert [step["low_reading_count_after"] for step in scenario["trace"]] == [0, 0]
        assert all("manual review required" in step["reason"] for step in scenario["trace"])


def test_simulate_scenario_unknown_species_rejected():
    controller = BloomPotController(default_reservoir_ml=200.0)

    with pytest.raises(KeyError, match="Unknown plant id"):
        controller.simulate_scenario(
            "unknown_plant_id",
            [{"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.10}],
            initial_reservoir_ml=200.0,
        )


def test_simulate_scenario_reservoir_equal_to_dose_is_allowed():
    controller = BloomPotController(default_reservoir_ml=60.0)

    scenario = controller.simulate_scenario(
        "peace_lily",
        [{"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.03}],
        initial_reservoir_ml=60.0,
    )

    assert scenario["trace"][0]["pump_on"] is True
    assert scenario["trace"][0]["reservoir_ml_before"] == 60.0
    assert scenario["trace"][0]["reservoir_ml_after"] == 0.0


def test_simulate_scenario_percent_moisture_inputs_are_normalized_per_step():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "peace_lily",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 4},
            {"timestamp": "2026-03-29T15:00:00+00:00", "soil_moisture": 50},
            {"timestamp": "2026-03-29T21:00:00+00:00", "soil_moisture": 17},
        ],
        initial_reservoir_ml=200.0,
    )

    assert [step["normalized_soil_moisture"] for step in scenario["trace"]] == [0.04, 0.5, 0.17]
    assert [step["pump_on"] for step in scenario["trace"]] == [True, False, True]
    assert scenario["trace"][2]["daily_dose_ml_after"] == 120.0


def test_peace_lily_full_day_replay_fixture_with_controller():
    controller = BloomPotController(default_reservoir_ml=240.0)
    fixture = load_fixture("peace_lily_full_day.json")

    scenario = controller.simulate_scenario(
        fixture["plant_id"],
        fixture["observations"],
        initial_state=fixture["initial_state"],
    )

    trace = scenario["trace"]
    assert [step["pump_on"] for step in trace] == [False, True, False, True, False, True, False]
    assert [step["reason_code"] for step in trace] == [
        "wet_cutoff_block",
        "hard_dry_approved",
        "cooldown_block",
        "confirmed_low_approved",
        "wet_cutoff_block",
        "hard_dry_approved",
        "cooldown_block",
    ]
    assert [step["reservoir_ml_after"] for step in trace] == [240.0, 180.0, 180.0, 120.0, 120.0, 60.0, 60.0]
    assert [step["daily_dose_ml_after"] for step in trace] == [0.0, 60.0, 60.0, 120.0, 120.0, 180.0, 180.0]
    assert [step["low_reading_count_after"] for step in trace] == [0, 0, 0, 0, 0, 0, 0]
    assert scenario["final_state"] == {
        "reservoir_ml": 60.0,
        "low_reading_count": 0,
        "last_watered_at": "2026-03-29T21:00:00+00:00",
        "daily_dose_ml": 180.0,
        "daily_dose_day": "2026-03-29",
    }
