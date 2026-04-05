import copy
import importlib.util
import sys
from pathlib import Path

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CONTROLLER_PROFILES_PATH = BASE_DIR / "controller_profiles.json"

CONTROLLER_SPEC = importlib.util.spec_from_file_location(
    "bloom_controller_for_calibration_tests",
    BASE_DIR / "bloom_controller.py",
)
CONTROLLER_MODULE = importlib.util.module_from_spec(CONTROLLER_SPEC)
sys.modules[CONTROLLER_SPEC.name] = CONTROLLER_MODULE
sys.modules["bloom_controller"] = CONTROLLER_MODULE
CONTROLLER_SPEC.loader.exec_module(CONTROLLER_MODULE)

EVALUATION_SPEC = importlib.util.spec_from_file_location(
    "bloom_evaluation_for_calibration_tests",
    BASE_DIR / "bloom_evaluation.py",
)
EVALUATION_MODULE = importlib.util.module_from_spec(EVALUATION_SPEC)
sys.modules[EVALUATION_SPEC.name] = EVALUATION_MODULE
sys.modules["bloom_evaluation"] = EVALUATION_MODULE
EVALUATION_SPEC.loader.exec_module(EVALUATION_MODULE)

CALIBRATION_SPEC = importlib.util.spec_from_file_location(
    "bloom_calibration_for_tests",
    BASE_DIR / "bloom_calibration.py",
)
CALIBRATION_MODULE = importlib.util.module_from_spec(CALIBRATION_SPEC)
sys.modules[CALIBRATION_SPEC.name] = CALIBRATION_MODULE
CALIBRATION_SPEC.loader.exec_module(CALIBRATION_MODULE)


BloomPotController = CONTROLLER_MODULE.BloomPotController
CalibrationCandidateError = CALIBRATION_MODULE.CalibrationCandidateError
evaluate_candidate = CALIBRATION_MODULE.evaluate_candidate
search_candidate_grid = CALIBRATION_MODULE.search_candidate_grid
summarize_replay_results = EVALUATION_MODULE.summarize_replay_results


def test_offline_grid_search_is_deterministic():
    first = search_candidate_grid(
        "soil_even_moist",
        {"watering_dose_ml": [80, 60, 40]},
        fixture_path=FIXTURES_DIR,
    )
    second = search_candidate_grid(
        "soil_even_moist",
        {"watering_dose_ml": [80, 60, 40]},
        fixture_path=FIXTURES_DIR,
    )

    assert first == second


@pytest.mark.parametrize(
    ("parameter_overrides", "expected_error"),
    [
        (
            {"sensor_model": 1},
            "not allowed for offline search",
        ),
        (
            {"confirm_low_readings": 1.5},
            "must be an integer value",
        ),
    ],
)
def test_invalid_candidate_rejection(parameter_overrides, expected_error):
    with pytest.raises(CalibrationCandidateError, match=expected_error):
        evaluate_candidate(
            "soil_even_moist",
            parameter_overrides,
            fixture_path=FIXTURES_DIR,
        )


@pytest.mark.parametrize(
    ("parameter_overrides", "expected_error"),
    [
        (
            {
                "watering_dose_ml": 300,
                "max_daily_dose_ml": 240,
            },
            "watering_dose_ml greater than max_daily_dose_ml",
        ),
        (
            {
                "moisture_target.minimum": 0.4,
            },
            "inconsistent moisture thresholds",
        ),
        (
            {
                "confirm_low_readings": 0,
            },
            "positive confirm_low_readings",
        ),
    ],
)
def test_candidate_invariants_are_enforced(parameter_overrides, expected_error):
    with pytest.raises(CalibrationCandidateError, match=expected_error):
        evaluate_candidate(
            "soil_even_moist",
            parameter_overrides,
            fixture_path=FIXTURES_DIR,
        )


def test_offline_grid_search_returns_stable_ranking_order():
    results = search_candidate_grid(
        "soil_even_moist",
        {"watering_dose_ml": [80, 60, 40]},
        fixture_path=FIXTURES_DIR,
    )

    assert [report["candidate_id"] for report in results] == [
        "soil_even_moist:watering_dose_ml=40",
        "soil_even_moist:baseline",
        "soil_even_moist:watering_dose_ml=80",
    ]
    assert [report["rank"] for report in results] == [1, 2, 3]
    assert [report["score"] for report in results] == [-83.5, -88.5, -97.0]


def test_candidate_summaries_match_replay_outputs():
    report = evaluate_candidate(
        "soil_even_moist",
        {"watering_dose_ml": 40},
        fixture_path=FIXTURES_DIR,
    )

    aggregate = summarize_replay_results(report["replay_results"])

    assert report["scenario_count"] == 2
    assert report["family_summary"] == aggregate["family_summary"]["soil_even_moist"]
    assert report["overall_summary"] == aggregate["overall_summary"]


def test_offline_search_does_not_mutate_baseline_controller_profiles():
    baseline_controller = BloomPotController()
    baseline_profiles_before = copy.deepcopy(baseline_controller.controller_profiles)
    baseline_file_before = CONTROLLER_PROFILES_PATH.read_text()

    search_candidate_grid(
        "soil_even_moist",
        {"watering_dose_ml": [40, 60, 80]},
        fixture_path=FIXTURES_DIR,
        baseline_controller=baseline_controller,
    )

    assert baseline_controller.controller_profiles == baseline_profiles_before
    assert CONTROLLER_PROFILES_PATH.read_text() == baseline_file_before
