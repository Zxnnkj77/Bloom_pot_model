import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CONTROLLER_PROFILES_PATH = BASE_DIR / "controller_profiles.json"

CONTROLLER_SPEC = importlib.util.spec_from_file_location(
    "bloom_controller_for_report_tests",
    BASE_DIR / "bloom_controller.py",
)
CONTROLLER_MODULE = importlib.util.module_from_spec(CONTROLLER_SPEC)
sys.modules[CONTROLLER_SPEC.name] = CONTROLLER_MODULE
sys.modules["bloom_controller"] = CONTROLLER_MODULE
CONTROLLER_SPEC.loader.exec_module(CONTROLLER_MODULE)

EVALUATION_SPEC = importlib.util.spec_from_file_location(
    "bloom_evaluation_for_report_tests",
    BASE_DIR / "bloom_evaluation.py",
)
EVALUATION_MODULE = importlib.util.module_from_spec(EVALUATION_SPEC)
sys.modules[EVALUATION_SPEC.name] = EVALUATION_MODULE
sys.modules["bloom_evaluation"] = EVALUATION_MODULE
EVALUATION_SPEC.loader.exec_module(EVALUATION_MODULE)

CALIBRATION_SPEC = importlib.util.spec_from_file_location(
    "bloom_calibration_for_report_tests",
    BASE_DIR / "bloom_calibration.py",
)
CALIBRATION_MODULE = importlib.util.module_from_spec(CALIBRATION_SPEC)
sys.modules[CALIBRATION_SPEC.name] = CALIBRATION_MODULE
sys.modules["bloom_calibration"] = CALIBRATION_MODULE
CALIBRATION_SPEC.loader.exec_module(CALIBRATION_MODULE)

REPORT_SPEC = importlib.util.spec_from_file_location(
    "bloom_calibration_report_for_tests",
    BASE_DIR / "bloom_calibration_report.py",
)
REPORT_MODULE = importlib.util.module_from_spec(REPORT_SPEC)
sys.modules[REPORT_SPEC.name] = REPORT_MODULE
REPORT_SPEC.loader.exec_module(REPORT_MODULE)


BloomPotController = CONTROLLER_MODULE.BloomPotController
CalibrationReportError = REPORT_MODULE.CalibrationReportError
compare_controller_family = REPORT_MODULE.compare_controller_family
render_markdown_report = REPORT_MODULE.render_markdown_report
write_recommendation_artifacts = REPORT_MODULE.write_recommendation_artifacts


def test_baseline_vs_candidate_comparison_prefers_lower_water_when_response_is_unchanged():
    payload = compare_controller_family(
        "soil_even_moist",
        [
            {"watering_dose_ml": 40},
            {"watering_dose_ml": 80},
        ],
        fixture_path=FIXTURES_DIR,
    )

    assert payload["baseline_metrics"] == {
        "replay_count_processed": 2,
        "accepted_replay_count": 2,
        "rejected_replay_count": 0,
        "water_events_count": 5,
        "total_dose_ml": 300.0,
        "mean_dose_per_accepted_replay": 150.0,
        "dry_side_trigger_count": 5,
        "wet_side_block_count": 2,
        "cooldown_block_count": 3,
        "budget_block_count": 0,
        "manual_review_block_count": 0,
        "reservoir_block_count": 0,
        "unknown_or_unresolved_rejection_count": 0,
        "unknown_plant_rejection_count": 0,
        "unresolved_species_rejection_count": 0,
        "below_target_step_count": 8,
        "below_target_without_watering_count": 3,
        "baseline_hard_dry_miss_count": 2,
        "scenario_ids": [
            "peace_lily_cooldown_boundary",
            "peace_lily_full_day",
        ],
    }
    assert [candidate["candidate_id"] for candidate in payload["candidate_results"]] == [
        "soil_even_moist:watering_dose_ml=40",
        "soil_even_moist:watering_dose_ml=80",
    ]
    assert payload["recommended_candidate"] == "soil_even_moist:watering_dose_ml=40"
    assert payload["candidate_results"][0]["comparison_to_baseline"]["total_dose_delta_ml"] == -100.0
    assert payload["candidate_results"][1]["score"]["components"]["reservoir_block_increase"] == 1


def test_ranking_is_deterministic():
    first = compare_controller_family(
        "soil_even_moist",
        [
            {"watering_dose_ml": 80},
            {"watering_dose_ml": 40},
            {"hard_dry_cutoff": 0.04},
        ],
        fixture_path=FIXTURES_DIR,
    )
    second = compare_controller_family(
        "soil_even_moist",
        [
            {"watering_dose_ml": 80},
            {"watering_dose_ml": 40},
            {"hard_dry_cutoff": 0.04},
        ],
        fixture_path=FIXTURES_DIR,
    )

    assert first == second


def test_invalid_candidates_are_rejected_without_stopping_the_report():
    payload = compare_controller_family(
        "soil_even_moist",
        [
            {"watering_dose_ml": 40},
            {"watering_dose_ml": 300},
            {"sensor_model": 1},
        ],
        fixture_path=FIXTURES_DIR,
    )

    assert [candidate["candidate_id"] for candidate in payload["candidate_results"]] == [
        "soil_even_moist:watering_dose_ml=40"
    ]
    assert len(payload["rejected_candidates"]) == 2
    assert payload["rejected_candidates"][0]["status"] == "rejected"
    assert "watering_dose_ml greater than max_daily_dose_ml" in payload["rejected_candidates"][0]["rejection_reason"]
    assert "not allowed for offline search" in payload["rejected_candidates"][1]["rejection_reason"]


def test_report_flow_does_not_mutate_controller_profiles_json(tmp_path):
    baseline_controller = BloomPotController()
    baseline_profiles_before = copy.deepcopy(baseline_controller.controller_profiles)
    baseline_file_before = CONTROLLER_PROFILES_PATH.read_text()

    payload = compare_controller_family(
        "soil_even_moist",
        [{"watering_dose_ml": 40}],
        fixture_path=FIXTURES_DIR,
        baseline_controller=baseline_controller,
    )
    write_recommendation_artifacts(
        payload,
        recommendation_output_path=tmp_path / "calibration_recommendations.json",
        report_output_path=tmp_path / "calibration_report.md",
    )

    assert baseline_controller.controller_profiles == baseline_profiles_before
    assert CONTROLLER_PROFILES_PATH.read_text() == baseline_file_before


def test_recommendation_export_shape_and_report_generation(tmp_path):
    payload = compare_controller_family(
        "soil_even_moist",
        [{"watering_dose_ml": 40}],
        fixture_path=FIXTURES_DIR,
    )
    output_paths = write_recommendation_artifacts(
        payload,
        recommendation_output_path=tmp_path / "calibration_recommendations.json",
        report_output_path=tmp_path / "calibration_report.md",
    )

    exported_payload = json.loads(Path(output_paths["recommendation_output_path"]).read_text())
    report_text = Path(output_paths["report_output_path"]).read_text()

    assert set(exported_payload) == {
        "controller_family",
        "baseline_profile",
        "baseline_metrics",
        "candidate_results",
        "recommended_candidate",
        "recommendation_reason",
        "rejected_candidates",
        "generated_from_fixture_or_dataset",
        "notes",
    }
    assert "Controller family analyzed: `soil_even_moist`" in report_text
    assert "## Baseline Summary" in report_text
    assert "## Candidate Summaries" in report_text
    assert "## Recommendation" in report_text
    assert "## Rejected Candidates" in report_text
    assert "No controller profile was changed automatically." in report_text


def test_tie_handling_uses_shared_rank_and_no_recommendation_when_baseline_is_unchanged():
    payload = compare_controller_family(
        "soil_even_moist",
        [
            {"hard_dry_cutoff": 0.04},
            {"hard_dry_cutoff": 0.045},
        ],
        fixture_path=FIXTURES_DIR,
    )

    assert [candidate["rank"] for candidate in payload["candidate_results"]] == [1, 1]
    assert [candidate["candidate_id"] for candidate in payload["candidate_results"]] == [
        "soil_even_moist:hard_dry_cutoff=0.04",
        "soil_even_moist:hard_dry_cutoff=0.045",
    ]
    assert payload["candidate_results"][1]["tied_rank"] is True
    assert payload["recommended_candidate"] is None
    assert "keep the current profile unchanged" in payload["recommendation_reason"]


def test_manual_review_families_require_explicit_opt_in():
    with pytest.raises(CalibrationReportError, match="manual-review-only"):
        compare_controller_family(
            "orchid_bark",
            [{"watering_dose_ml": 35}],
            fixture_path=FIXTURES_DIR,
        )

    payload = compare_controller_family(
        "orchid_bark",
        [{"watering_dose_ml": 35}],
        fixture_path=FIXTURES_DIR,
        allow_manual_review=True,
    )

    assert payload["baseline_metrics"]["manual_review_block_count"] == 2
    assert payload["recommended_candidate"] is None


def test_render_markdown_report_mentions_rejected_candidates():
    payload = compare_controller_family(
        "soil_even_moist",
        [
            {"watering_dose_ml": 40},
            {"watering_dose_ml": 300},
        ],
        fixture_path=FIXTURES_DIR,
    )

    report_text = render_markdown_report(payload)

    assert "`soil_even_moist:watering_dose_ml=300`" in report_text
    assert "Why rejected candidates were rejected" not in report_text
    assert "watering_dose_ml greater than max_daily_dose_ml" in report_text
