import json
import importlib.util
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BASE_DIR = Path(__file__).resolve().parents[1]

CONTROLLER_SPEC = importlib.util.spec_from_file_location(
    "bloom_controller_for_replay_tests",
    BASE_DIR / "bloom_controller.py",
)
CONTROLLER_MODULE = importlib.util.module_from_spec(CONTROLLER_SPEC)
sys.modules[CONTROLLER_SPEC.name] = CONTROLLER_MODULE
sys.modules["bloom_controller"] = CONTROLLER_MODULE
CONTROLLER_SPEC.loader.exec_module(CONTROLLER_MODULE)

EVALUATION_SPEC = importlib.util.spec_from_file_location(
    "bloom_evaluation_for_replay_tests",
    BASE_DIR / "bloom_evaluation.py",
)
EVALUATION_MODULE = importlib.util.module_from_spec(EVALUATION_SPEC)
sys.modules[EVALUATION_SPEC.name] = EVALUATION_MODULE
EVALUATION_SPEC.loader.exec_module(EVALUATION_MODULE)


BloomPotController = CONTROLLER_MODULE.BloomPotController
ReplayValidationError = EVALUATION_MODULE.ReplayValidationError
evaluate_replay_path = EVALUATION_MODULE.evaluate_replay_path
load_replay_scenario = EVALUATION_MODULE.load_replay_scenario


@pytest.mark.parametrize(
    "fixture_name",
    [
        "peace_lily_full_day.json",
        "golden_pothos_confirm_low_readings.json",
        "peace_lily_cooldown_boundary.json",
        "boston_fern_daily_budget_block.json",
        "moth_orchid_manual_review.json",
        "unresolved_species_rejection.json",
        "unknown_plant_rejection.json",
    ],
)
def test_replay_fixture_schema_validity(fixture_name):
    scenario = load_replay_scenario(FIXTURES_DIR / fixture_name)
    assert scenario["scenario_id"]


def test_replay_output_is_deterministic():
    controller = BloomPotController(default_reservoir_ml=240.0)
    fixture_path = FIXTURES_DIR / "peace_lily_full_day.json"

    first = evaluate_replay_path(fixture_path, controller=controller)
    second = evaluate_replay_path(fixture_path, controller=controller)

    assert first == second


@pytest.mark.parametrize(
    ("fixture_name", "expected_summary"),
    [
        (
            "peace_lily_full_day.json",
            {
                "total_observations": 7,
                "total_watering_events": 3,
                "total_dispensed_ml": 180.0,
                "blocked_by_cooldown": 2,
                "blocked_by_daily_budget": 0,
                "blocked_by_manual_review": 0,
                "blocked_by_reservoir": 0,
                "wet_cutoff_blocks": 2,
                "hard_dry_events": 3,
                "confirmation_wait_events": 0,
                "unresolved_species_rejections": 0,
                "unknown_plant_rejections": 0,
                "final_reservoir_ml": 60.0,
            },
        ),
        (
            "golden_pothos_confirm_low_readings.json",
            {
                "total_observations": 2,
                "total_watering_events": 1,
                "total_dispensed_ml": 50.0,
                "blocked_by_cooldown": 0,
                "blocked_by_daily_budget": 0,
                "blocked_by_manual_review": 0,
                "blocked_by_reservoir": 0,
                "wet_cutoff_blocks": 0,
                "hard_dry_events": 0,
                "confirmation_wait_events": 1,
                "unresolved_species_rejections": 0,
                "unknown_plant_rejections": 0,
                "final_reservoir_ml": 150.0,
            },
        ),
        (
            "peace_lily_cooldown_boundary.json",
            {
                "total_observations": 3,
                "total_watering_events": 2,
                "total_dispensed_ml": 120.0,
                "blocked_by_cooldown": 1,
                "blocked_by_daily_budget": 0,
                "blocked_by_manual_review": 0,
                "blocked_by_reservoir": 0,
                "wet_cutoff_blocks": 0,
                "hard_dry_events": 3,
                "confirmation_wait_events": 0,
                "unresolved_species_rejections": 0,
                "unknown_plant_rejections": 0,
                "final_reservoir_ml": 80.0,
            },
        ),
        (
            "boston_fern_daily_budget_block.json",
            {
                "total_observations": 5,
                "total_watering_events": 4,
                "total_dispensed_ml": 280.0,
                "blocked_by_cooldown": 0,
                "blocked_by_daily_budget": 1,
                "blocked_by_manual_review": 0,
                "blocked_by_reservoir": 0,
                "wet_cutoff_blocks": 0,
                "hard_dry_events": 5,
                "confirmation_wait_events": 0,
                "unresolved_species_rejections": 0,
                "unknown_plant_rejections": 0,
                "final_reservoir_ml": 220.0,
            },
        ),
    ],
)
def test_summary_metrics_correctness_on_fixtures(fixture_name, expected_summary):
    result = evaluate_replay_path(FIXTURES_DIR / fixture_name)[0]

    assert result["status"] == "completed"
    assert result["summary"] == expected_summary


def test_accepted_manual_review_plant_scenario_behavior():
    result = evaluate_replay_path(FIXTURES_DIR / "moth_orchid_manual_review.json")[0]

    assert result["status"] == "completed"
    assert [step["pump_on"] for step in result["trace"]] == [False, False]
    assert [step["decision_code"] for step in result["trace"]] == [
        "manual_review_block",
        "manual_review_block",
    ]
    assert result["summary"]["blocked_by_manual_review"] == 2
    assert result["summary"]["total_watering_events"] == 0
    assert result["summary"]["final_reservoir_ml"] == 200.0


def test_unresolved_species_rejection_during_replay():
    result = evaluate_replay_path(FIXTURES_DIR / "unresolved_species_rejection.json")[0]

    assert result["status"] == "rejected"
    assert result["trace"] == []
    assert result["summary"]["total_observations"] == 1
    assert result["summary"]["unresolved_species_rejections"] == 1
    assert result["summary"]["unknown_plant_rejections"] == 0
    assert result["summary"]["final_reservoir_ml"] == 200.0
    assert result["rejection"]["code"] == "unresolved_species_id"


def test_unknown_plant_id_rejection_during_replay():
    result = evaluate_replay_path(FIXTURES_DIR / "unknown_plant_rejection.json")[0]

    assert result["status"] == "rejected"
    assert result["trace"] == []
    assert result["summary"]["total_observations"] == 1
    assert result["summary"]["unresolved_species_rejections"] == 0
    assert result["summary"]["unknown_plant_rejections"] == 1
    assert result["summary"]["final_reservoir_ml"] == 200.0
    assert result["rejection"]["code"] == "unknown_plant_id"


def test_evaluation_runner_fails_loudly_on_malformed_scenarios(tmp_path):
    scenario_path = tmp_path / "out_of_order.json"
    scenario_path.write_text(
        json.dumps(
            {
                "scenario_id": "out_of_order",
                "plant_id": "peace_lily",
                "initial_state": {"reservoir_ml": 200.0},
                "observations": [
                    {
                        "timestamp": "2026-03-29T10:00:00+00:00",
                        "soil_moisture": 0.1,
                    },
                    {
                        "timestamp": "2026-03-29T09:00:00+00:00",
                        "soil_moisture": 0.1,
                    },
                ],
            },
            indent=2,
        )
        + "\n"
    )

    with pytest.raises(ReplayValidationError, match="out of order"):
        evaluate_replay_path(scenario_path)
