import importlib.util
import json
import sys
from pathlib import Path

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

CONTROLLER_SPEC = importlib.util.spec_from_file_location(
    "bloom_controller_for_workflow_tests",
    BASE_DIR / "bloom_controller.py",
)
CONTROLLER_MODULE = importlib.util.module_from_spec(CONTROLLER_SPEC)
sys.modules[CONTROLLER_SPEC.name] = CONTROLLER_MODULE
sys.modules["bloom_controller"] = CONTROLLER_MODULE
CONTROLLER_SPEC.loader.exec_module(CONTROLLER_MODULE)

EVALUATION_SPEC = importlib.util.spec_from_file_location(
    "bloom_evaluation_for_workflow_tests",
    BASE_DIR / "bloom_evaluation.py",
)
EVALUATION_MODULE = importlib.util.module_from_spec(EVALUATION_SPEC)
sys.modules[EVALUATION_SPEC.name] = EVALUATION_MODULE
sys.modules["bloom_evaluation"] = EVALUATION_MODULE
EVALUATION_SPEC.loader.exec_module(EVALUATION_MODULE)

CALIBRATION_SPEC = importlib.util.spec_from_file_location(
    "bloom_calibration_for_workflow_tests",
    BASE_DIR / "bloom_calibration.py",
)
CALIBRATION_MODULE = importlib.util.module_from_spec(CALIBRATION_SPEC)
sys.modules[CALIBRATION_SPEC.name] = CALIBRATION_MODULE
sys.modules["bloom_calibration"] = CALIBRATION_MODULE
CALIBRATION_SPEC.loader.exec_module(CALIBRATION_MODULE)

REPORT_SPEC = importlib.util.spec_from_file_location(
    "bloom_calibration_report_for_workflow_tests",
    BASE_DIR / "bloom_calibration_report.py",
)
REPORT_MODULE = importlib.util.module_from_spec(REPORT_SPEC)
sys.modules[REPORT_SPEC.name] = REPORT_MODULE
sys.modules["bloom_calibration_report"] = REPORT_MODULE
REPORT_SPEC.loader.exec_module(REPORT_MODULE)

WORKFLOW_SPEC = importlib.util.spec_from_file_location(
    "bloom_calibration_workflow_for_tests",
    BASE_DIR / "bloom_calibration_workflow.py",
)
WORKFLOW_MODULE = importlib.util.module_from_spec(WORKFLOW_SPEC)
sys.modules[WORKFLOW_SPEC.name] = WORKFLOW_MODULE
WORKFLOW_SPEC.loader.exec_module(WORKFLOW_MODULE)


CalibrationWorkflowError = WORKFLOW_MODULE.CalibrationWorkflowError
run_calibration_workflow = WORKFLOW_MODULE.run_calibration_workflow
write_workflow_artifacts = WORKFLOW_MODULE.write_workflow_artifacts


def test_successful_end_to_end_workflow():
    payload = run_calibration_workflow(
        "soil_even_moist",
        {"watering_dose_ml": [40, 60, 80]},
        fixture_path=FIXTURES_DIR,
    )

    assert payload["workflow_status"] == "recommended"
    assert payload["recommendation_summary"] == {
        "status": "recommended",
        "recommended_candidate": "soil_even_moist:watering_dose_ml=40",
        "winning_candidate": "soil_even_moist:watering_dose_ml=40",
        "reason": (
            "soil_even_moist:watering_dose_ml=40 ranked first because it "
            "used less total water."
        ),
    }
    assert payload["baseline_metrics"]["water_events_count"] == 5
    assert payload["winning_candidate"]["parameter_overrides"] == {"watering_dose_ml": 40.0}
    assert payload["comparison_vs_baseline"]["total_dose_delta_ml"] == -100.0
    assert payload["failure_reasons"] == []


def test_no_valid_candidate_case_is_explicit():
    payload = run_calibration_workflow(
        "soil_even_moist",
        {"watering_dose_ml": [60]},
        fixture_path=FIXTURES_DIR,
    )

    assert payload["workflow_status"] == "failed"
    assert payload["candidate_search_results"] == []
    assert payload["winning_candidate"] is None
    assert payload["recommendation_summary"]["recommended_candidate"] is None
    assert payload["failure_reasons"] == [
        {
            "code": "no_valid_candidate_found",
            "message": "The calibration search produced no non-baseline candidates to compare.",
        }
    ]


def test_baseline_vs_candidate_comparison_is_preserved_in_workflow_output():
    payload = run_calibration_workflow(
        "soil_even_moist",
        {"watering_dose_ml": [40, 80]},
        fixture_path=FIXTURES_DIR,
    )

    assert [candidate["candidate_id"] for candidate in payload["candidate_results"]] == [
        "soil_even_moist:watering_dose_ml=40",
        "soil_even_moist:watering_dose_ml=80",
    ]
    assert payload["winning_candidate"]["candidate_id"] == "soil_even_moist:watering_dose_ml=40"
    assert payload["winning_candidate"]["comparison_to_baseline"]["total_dose_delta_ml"] == -100.0
    assert payload["winning_candidate"]["comparison_to_baseline"]["wet_side_block_delta"] == 0


def test_workflow_is_deterministic():
    first = run_calibration_workflow(
        "soil_even_moist",
        {"watering_dose_ml": [40, 60, 80]},
        fixture_path=FIXTURES_DIR,
    )
    second = run_calibration_workflow(
        "soil_even_moist",
        {"watering_dose_ml": [40, 60, 80]},
        fixture_path=FIXTURES_DIR,
    )

    assert first == second


def test_export_shape_validation_and_report_generation(tmp_path):
    payload = run_calibration_workflow(
        "soil_even_moist",
        {"watering_dose_ml": [40, 60, 80]},
        fixture_path=FIXTURES_DIR,
    )
    output_paths = write_workflow_artifacts(
        payload,
        workflow_output_path=tmp_path / "calibration_workflow.json",
        report_output_path=tmp_path / "calibration_workflow.md",
    )

    exported_payload = json.loads(Path(output_paths["workflow_output_path"]).read_text())
    report_text = Path(output_paths["report_output_path"]).read_text()

    assert set(exported_payload) == {
        "workflow_status",
        "controller_family",
        "parameter_grid",
        "generated_from_fixture_or_dataset",
        "replay_summary",
        "baseline_profile",
        "baseline_metrics",
        "baseline_search_result",
        "candidate_search_results",
        "candidate_results",
        "winning_candidate",
        "comparison_vs_baseline",
        "recommendation_summary",
        "rejected_candidates",
        "failure_reasons",
        "notes",
    }
    assert "Workflow status: `recommended`" in report_text
    assert "## Recommendation Summary" in report_text
    assert "## Search Results" in report_text
    assert "## Winning Candidate" in report_text
    assert "## Failure Reasons" in report_text


def test_workflow_marks_candidate_that_does_not_beat_baseline_as_failed():
    payload = run_calibration_workflow(
        "soil_even_moist",
        {"hard_dry_cutoff": [0.04, 0.045]},
        fixture_path=FIXTURES_DIR,
    )

    assert payload["workflow_status"] == "failed"
    assert payload["recommendation_summary"]["recommended_candidate"] is None
    assert payload["winning_candidate"]["candidate_id"] == "soil_even_moist:hard_dry_cutoff=0.04"
    assert payload["failure_reasons"] == [
        {
            "code": "candidate_not_better_than_baseline",
            "message": (
                "The best candidate did not beat the baseline on the deterministic replay comparison."
            ),
        }
    ]


def test_bad_export_path_handling_is_loud(tmp_path):
    payload = run_calibration_workflow(
        "soil_even_moist",
        {"watering_dose_ml": [40, 60, 80]},
        fixture_path=FIXTURES_DIR,
    )

    with pytest.raises(CalibrationWorkflowError, match="parent directory does not exist"):
        write_workflow_artifacts(
            payload,
            workflow_output_path=tmp_path / "missing" / "calibration_workflow.json",
            report_output_path=tmp_path / "calibration_workflow.md",
        )
