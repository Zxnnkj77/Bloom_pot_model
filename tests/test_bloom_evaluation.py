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
evaluate_replay_scenario = EVALUATION_MODULE.evaluate_replay_scenario
load_replay_scenario = EVALUATION_MODULE.load_replay_scenario


def report_for_fixture(fixture_name):
    return evaluate_replay_path(FIXTURES_DIR / fixture_name)


def result_for_fixture(fixture_name):
    return report_for_fixture(fixture_name)["scenarios"][0]


def assert_summary_matches_trace(result, controller):
    trace = result["trace"]
    summary = result["summary"]

    assert summary["total_steps"] == len(trace)
    assert summary["total_watering_events"] == sum(1 for step in trace if step["pump_on"])
    assert summary["total_water_dispensed_ml"] == sum(step["dose_ml"] for step in trace)
    assert summary["steps_below_target"] == sum(
        1 for step in trace if step["soil_moisture"] < step["target_band"][0]
    )
    assert summary["steps_inside_target"] == sum(
        1
        for step in trace
        if step["target_band"][0] <= step["soil_moisture"] <= step["target_band"][1]
    )
    assert summary["steps_above_target"] == sum(
        1 for step in trace if step["soil_moisture"] > step["target_band"][1]
    )
    assert summary["hard_dry_trigger_count"] == sum(
        1
        for step in trace
        if step["soil_moisture"]
        <= controller.controller_profiles[step["controller_family"]]["hard_dry_cutoff"]
    )
    assert summary["hard_wet_block_count"] == sum(
        1 for step in trace if step["decision_code"] == "wet_cutoff_block"
    )
    assert summary["cooldown_block_count"] == sum(
        1 for step in trace if step["decision_code"] == "cooldown_block"
    )
    assert summary["daily_budget_block_count"] == sum(
        1 for step in trace if step["decision_code"] == "daily_budget_block"
    )
    assert summary["reservoir_block_count"] == sum(
        1 for step in trace if step["decision_code"] == "reservoir_block"
    )
    assert summary["manual_review_block_count"] == sum(
        1 for step in trace if step["decision_code"] == "manual_review_block"
    )
    assert summary["confirmation_wait_count"] == sum(
        1 for step in trace if step["decision_code"] == "confirmation_wait"
    )


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
                "total_steps": 7,
                "total_watering_events": 3,
                "total_water_dispensed_ml": 180.0,
                "steps_below_target": 5,
                "steps_inside_target": 0,
                "steps_above_target": 2,
                "hard_dry_trigger_count": 3,
                "hard_wet_block_count": 2,
                "cooldown_block_count": 2,
                "daily_budget_block_count": 0,
                "reservoir_block_count": 0,
                "manual_review_block_count": 0,
                "confirmation_wait_count": 0,
                "unresolved_species_rejection_count": 0,
                "unknown_plant_rejection_count": 0,
                "final_reservoir_ml": 60.0,
            },
        ),
        (
            "golden_pothos_confirm_low_readings.json",
            {
                "total_steps": 2,
                "total_watering_events": 1,
                "total_water_dispensed_ml": 50.0,
                "steps_below_target": 2,
                "steps_inside_target": 0,
                "steps_above_target": 0,
                "hard_dry_trigger_count": 0,
                "hard_wet_block_count": 0,
                "cooldown_block_count": 0,
                "daily_budget_block_count": 0,
                "reservoir_block_count": 0,
                "manual_review_block_count": 0,
                "confirmation_wait_count": 1,
                "unresolved_species_rejection_count": 0,
                "unknown_plant_rejection_count": 0,
                "final_reservoir_ml": 150.0,
            },
        ),
        (
            "peace_lily_cooldown_boundary.json",
            {
                "total_steps": 3,
                "total_watering_events": 2,
                "total_water_dispensed_ml": 120.0,
                "steps_below_target": 3,
                "steps_inside_target": 0,
                "steps_above_target": 0,
                "hard_dry_trigger_count": 3,
                "hard_wet_block_count": 0,
                "cooldown_block_count": 1,
                "daily_budget_block_count": 0,
                "reservoir_block_count": 0,
                "manual_review_block_count": 0,
                "confirmation_wait_count": 0,
                "unresolved_species_rejection_count": 0,
                "unknown_plant_rejection_count": 0,
                "final_reservoir_ml": 80.0,
            },
        ),
        (
            "boston_fern_daily_budget_block.json",
            {
                "total_steps": 5,
                "total_watering_events": 4,
                "total_water_dispensed_ml": 280.0,
                "steps_below_target": 5,
                "steps_inside_target": 0,
                "steps_above_target": 0,
                "hard_dry_trigger_count": 5,
                "hard_wet_block_count": 0,
                "cooldown_block_count": 0,
                "daily_budget_block_count": 1,
                "reservoir_block_count": 0,
                "manual_review_block_count": 0,
                "confirmation_wait_count": 0,
                "unresolved_species_rejection_count": 0,
                "unknown_plant_rejection_count": 0,
                "final_reservoir_ml": 220.0,
            },
        ),
        (
            "moth_orchid_manual_review.json",
            {
                "total_steps": 2,
                "total_watering_events": 0,
                "total_water_dispensed_ml": 0.0,
                "steps_below_target": 2,
                "steps_inside_target": 0,
                "steps_above_target": 0,
                "hard_dry_trigger_count": 2,
                "hard_wet_block_count": 0,
                "cooldown_block_count": 0,
                "daily_budget_block_count": 0,
                "reservoir_block_count": 0,
                "manual_review_block_count": 2,
                "confirmation_wait_count": 0,
                "unresolved_species_rejection_count": 0,
                "unknown_plant_rejection_count": 0,
                "final_reservoir_ml": 200.0,
            },
        ),
    ],
)
def test_summary_metrics_correctness_on_fixtures(fixture_name, expected_summary):
    result = result_for_fixture(fixture_name)

    assert result["status"] == "completed"
    assert result["summary"] == expected_summary


def test_accepted_manual_review_plant_scenario_behavior():
    result = result_for_fixture("moth_orchid_manual_review.json")

    assert result["status"] == "completed"
    assert [step["pump_on"] for step in result["trace"]] == [False, False]
    assert [step["decision_code"] for step in result["trace"]] == [
        "manual_review_block",
        "manual_review_block",
    ]
    assert result["summary"]["manual_review_block_count"] == 2
    assert result["summary"]["total_watering_events"] == 0
    assert result["summary"]["final_reservoir_ml"] == 200.0


def test_unresolved_species_rejection_during_replay():
    result = result_for_fixture("unresolved_species_rejection.json")

    assert result["status"] == "rejected"
    assert result["observation_count"] == 1
    assert result["trace"] == []
    assert result["summary"]["total_steps"] == 0
    assert result["summary"]["unresolved_species_rejection_count"] == 1
    assert result["summary"]["unknown_plant_rejection_count"] == 0
    assert result["summary"]["final_reservoir_ml"] == 200.0
    assert result["rejection"]["code"] == "unresolved_species_id"


def test_unknown_plant_id_rejection_during_replay():
    result = result_for_fixture("unknown_plant_rejection.json")

    assert result["status"] == "rejected"
    assert result["observation_count"] == 1
    assert result["trace"] == []
    assert result["summary"]["total_steps"] == 0
    assert result["summary"]["unresolved_species_rejection_count"] == 0
    assert result["summary"]["unknown_plant_rejection_count"] == 1
    assert result["summary"]["final_reservoir_ml"] == 200.0
    assert result["rejection"]["code"] == "unknown_plant_id"


def test_boundary_classification_uses_inclusive_target_edges():
    result = evaluate_replay_scenario(
        {
            "scenario_id": "peace_lily_target_boundaries",
            "plant_id": "peace_lily",
            "initial_state": {"reservoir_ml": 500.0},
            "observations": [
                {"timestamp": "2026-03-29T00:00:00+00:00", "soil_moisture": 0.17},
                {"timestamp": "2026-03-29T06:00:00+00:00", "soil_moisture": 0.18},
                {"timestamp": "2026-03-29T12:00:00+00:00", "soil_moisture": 0.28},
                {"timestamp": "2026-03-29T18:00:00+00:00", "soil_moisture": 0.29},
            ],
        }
    )

    assert result["summary"]["steps_below_target"] == 1
    assert result["summary"]["steps_inside_target"] == 2
    assert result["summary"]["steps_above_target"] == 1


def test_block_reason_counts_cover_replay_paths_and_reservoir_blocks():
    full_day = result_for_fixture("peace_lily_full_day.json")
    daily_budget = result_for_fixture("boston_fern_daily_budget_block.json")
    manual_review = result_for_fixture("moth_orchid_manual_review.json")
    reservoir_block = evaluate_replay_scenario(
        {
            "scenario_id": "peace_lily_reservoir_block",
            "plant_id": "peace_lily",
            "initial_state": {"reservoir_ml": 50.0},
            "observations": [
                {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.03},
            ],
        }
    )

    assert full_day["summary"]["hard_wet_block_count"] == 2
    assert full_day["summary"]["cooldown_block_count"] == 2
    assert daily_budget["summary"]["daily_budget_block_count"] == 1
    assert manual_review["summary"]["manual_review_block_count"] == 2
    assert reservoir_block["summary"]["reservoir_block_count"] == 1


def test_watering_event_counting_matches_trace():
    result = result_for_fixture("peace_lily_full_day.json")

    assert result["summary"]["total_watering_events"] == 3
    assert result["summary"]["total_water_dispensed_ml"] == 180.0
    assert result["summary"]["total_watering_events"] == sum(
        1 for step in result["trace"] if step["pump_on"]
    )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "peace_lily_full_day.json",
        "golden_pothos_confirm_low_readings.json",
        "peace_lily_cooldown_boundary.json",
        "boston_fern_daily_budget_block.json",
        "moth_orchid_manual_review.json",
    ],
)
def test_summary_metrics_are_consistent_with_replay_trace(fixture_name):
    controller = BloomPotController()
    result = result_for_fixture(fixture_name)

    assert_summary_matches_trace(result, controller)


def test_directory_report_includes_scenario_and_family_summaries():
    report = evaluate_replay_path(FIXTURES_DIR)

    assert [summary["scenario_id"] for summary in report["scenario_summaries"]] == [
        "boston_fern_daily_budget_block",
        "golden_pothos_confirm_low_readings",
        "moth_orchid_manual_review",
        "peace_lily_cooldown_boundary",
        "peace_lily_full_day",
        "unknown_plant_rejection",
        "unresolved_species_rejection",
    ]
    assert [summary["controller_family"] for summary in report["family_summaries"]] == [
        "fern_high_moisture",
        "orchid_bark",
        "soil_dry_between",
        "soil_even_moist",
    ]
    assert report["overall_summary"] == {
        "scenario_count": 7,
        "completed_scenario_count": 5,
        "rejected_scenario_count": 2,
        "total_steps": 19,
        "total_watering_events": 10,
        "total_water_dispensed_ml": 630.0,
        "steps_below_target": 17,
        "steps_inside_target": 0,
        "steps_above_target": 2,
        "hard_dry_trigger_count": 13,
        "hard_wet_block_count": 2,
        "cooldown_block_count": 3,
        "daily_budget_block_count": 1,
        "reservoir_block_count": 0,
        "manual_review_block_count": 2,
        "confirmation_wait_count": 1,
        "unresolved_species_rejection_count": 1,
        "unknown_plant_rejection_count": 1,
    }

    soil_even_moist = next(
        summary
        for summary in report["family_summaries"]
        if summary["controller_family"] == "soil_even_moist"
    )
    assert soil_even_moist["scenario_ids"] == [
        "peace_lily_cooldown_boundary",
        "peace_lily_full_day",
    ]
    assert soil_even_moist["summary"] == {
        "scenario_count": 2,
        "completed_scenario_count": 2,
        "rejected_scenario_count": 0,
        "total_steps": 10,
        "total_watering_events": 5,
        "total_water_dispensed_ml": 300.0,
        "steps_below_target": 8,
        "steps_inside_target": 0,
        "steps_above_target": 2,
        "hard_dry_trigger_count": 6,
        "hard_wet_block_count": 2,
        "cooldown_block_count": 3,
        "daily_budget_block_count": 0,
        "reservoir_block_count": 0,
        "manual_review_block_count": 0,
        "confirmation_wait_count": 0,
        "unresolved_species_rejection_count": 0,
        "unknown_plant_rejection_count": 0,
    }


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
