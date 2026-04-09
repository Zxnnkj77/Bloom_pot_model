from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from bloom_calibration import DEFAULT_FIXTURES_PATH, search_candidate_grid
from bloom_calibration_report import compare_controller_family, evaluate_baseline_profile
from bloom_controller import BASE_DIR, BloomPotController
from bloom_evaluation import evaluate_replay_path, summarize_replay_results


DEFAULT_WORKFLOW_OUTPUT_PATH = BASE_DIR / "calibration_workflow.json"
DEFAULT_REPORT_PATH = BASE_DIR / "calibration_workflow.md"


class CalibrationWorkflowError(ValueError):
    """Raised when the offline calibration workflow cannot complete safely."""


def _failure(code: str, message: str) -> dict[str, str]:
    return {
        "code": code,
        "message": message,
    }


def _condense_search_result(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": report["candidate_id"],
        "controller_family": report["controller_family"],
        "parameter_overrides": copy.deepcopy(report["parameter_overrides"]),
        "scenario_count": report["scenario_count"],
        "family_summary": copy.deepcopy(report["family_summary"]),
        "overall_summary": copy.deepcopy(report["overall_summary"]),
        "score": report["score"],
        "rank": report["rank"],
        "validity_status": report["validity_status"],
    }


def _workflow_comparison_payload(
    controller_family: str,
    *,
    fixture_path: str | Path,
    baseline_controller: BloomPotController,
    allow_manual_review: bool,
) -> dict[str, Any]:
    baseline_report = evaluate_baseline_profile(
        controller_family,
        fixture_path=fixture_path,
        baseline_controller=baseline_controller,
        allow_manual_review=allow_manual_review,
    )
    return {
        "controller_family": controller_family,
        "baseline_profile": baseline_report["baseline_profile"],
        "baseline_metrics": baseline_report["baseline_metrics"],
        "candidate_results": [],
        "recommended_candidate": None,
        "recommendation_reason": (
            "No valid candidates were available after the workflow search; "
            "keep the current profile unchanged."
        ),
        "rejected_candidates": [],
        "generated_from_fixture_or_dataset": str(Path(fixture_path)),
        "notes": [
            f"Replay data came from {Path(fixture_path)}.",
            "No controller profile was changed automatically in Round 10.",
        ],
    }


def run_calibration_workflow(
    controller_family: str,
    parameter_grid: dict[str, Any],
    *,
    fixture_path: str | Path = DEFAULT_FIXTURES_PATH,
    baseline_controller: BloomPotController | None = None,
    allow_manual_review: bool = False,
) -> dict[str, Any]:
    controller = baseline_controller or BloomPotController()
    replay_results = evaluate_replay_path(
        fixture_path,
        controller=controller,
        unresolved_species=controller.unresolved_species,
    )
    if not replay_results:
        raise CalibrationWorkflowError("Replay set is empty.")

    replay_summary = summarize_replay_results(replay_results)
    search_results = search_candidate_grid(
        controller_family,
        parameter_grid,
        fixture_path=fixture_path,
        baseline_controller=controller,
        include_baseline=True,
    )

    baseline_search_result: dict[str, Any] | None = None
    candidate_search_results: list[dict[str, Any]] = []
    for report in search_results:
        condensed_report = _condense_search_result(report)
        if report["parameter_overrides"]:
            candidate_search_results.append(condensed_report)
        else:
            baseline_search_result = condensed_report

    failure_reasons: list[dict[str, str]] = []

    if candidate_search_results:
        comparison_payload = compare_controller_family(
            controller_family,
            [
                {
                    "candidate_label": candidate["candidate_id"],
                    "parameter_overrides": candidate["parameter_overrides"],
                }
                for candidate in candidate_search_results
            ],
            fixture_path=fixture_path,
            baseline_controller=controller,
            allow_manual_review=allow_manual_review,
        )
    else:
        comparison_payload = _workflow_comparison_payload(
            controller_family,
            fixture_path=fixture_path,
            baseline_controller=controller,
            allow_manual_review=allow_manual_review,
        )
        failure_reasons.append(
            _failure(
                "no_valid_candidate_found",
                "The calibration search produced no non-baseline candidates to compare.",
            )
        )

    winning_candidate: dict[str, Any] | None = None
    search_result_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidate_search_results
    }
    if comparison_payload["candidate_results"]:
        winning_candidate = copy.deepcopy(comparison_payload["candidate_results"][0])
        winning_search_result = search_result_by_id.get(winning_candidate["candidate_id"])
        if winning_search_result is not None:
            winning_candidate["search_rank"] = winning_search_result["rank"]
            winning_candidate["search_score"] = winning_search_result["score"]

    if comparison_payload["candidate_results"] and comparison_payload["recommended_candidate"] is None:
        failure_reasons.append(
            _failure(
                "candidate_not_better_than_baseline",
                "The best candidate did not beat the baseline on the deterministic replay comparison.",
            )
        )

    workflow_status = "recommended" if not failure_reasons else "failed"
    recommendation_summary = {
        "status": workflow_status,
        "recommended_candidate": comparison_payload["recommended_candidate"],
        "winning_candidate": None if winning_candidate is None else winning_candidate["candidate_id"],
        "reason": comparison_payload["recommendation_reason"],
    }

    notes = list(comparison_payload["notes"])
    notes.append("Calibration search and comparison were replay-only and offline.")

    return {
        "workflow_status": workflow_status,
        "controller_family": controller_family,
        "parameter_grid": copy.deepcopy(parameter_grid),
        "generated_from_fixture_or_dataset": str(Path(fixture_path)),
        "replay_summary": replay_summary,
        "baseline_profile": comparison_payload["baseline_profile"],
        "baseline_metrics": comparison_payload["baseline_metrics"],
        "baseline_search_result": baseline_search_result,
        "candidate_search_results": candidate_search_results,
        "candidate_results": comparison_payload["candidate_results"],
        "winning_candidate": winning_candidate,
        "comparison_vs_baseline": (
            None
            if winning_candidate is None
            else copy.deepcopy(winning_candidate["comparison_to_baseline"])
        ),
        "recommendation_summary": recommendation_summary,
        "rejected_candidates": comparison_payload["rejected_candidates"],
        "failure_reasons": failure_reasons,
        "notes": notes,
    }


def render_workflow_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Calibration Workflow Report",
        "",
        f"Workflow status: `{payload['workflow_status']}`",
        f"Controller family analyzed: `{payload['controller_family']}`",
        f"Generated from fixture or dataset: `{payload['generated_from_fixture_or_dataset']}`",
        "",
        "## Recommendation Summary",
        "",
        f"- Recommended candidate: `{payload['recommendation_summary']['recommended_candidate']}`",
        f"- Winning candidate: `{payload['recommendation_summary']['winning_candidate']}`",
        f"- Reason: {payload['recommendation_summary']['reason']}",
        "",
        "## Baseline Metrics",
        "",
        f"- Replays processed: {payload['baseline_metrics']['replay_count_processed']}",
        f"- Accepted replays: {payload['baseline_metrics']['accepted_replay_count']}",
        f"- Rejected replays: {payload['baseline_metrics']['rejected_replay_count']}",
        f"- Water events: {payload['baseline_metrics']['water_events_count']}",
        f"- Total dose ml: {payload['baseline_metrics']['total_dose_ml']}",
        f"- Wet-side blocks: {payload['baseline_metrics']['wet_side_block_count']}",
        f"- Cooldown blocks: {payload['baseline_metrics']['cooldown_block_count']}",
        f"- Budget blocks: {payload['baseline_metrics']['budget_block_count']}",
        f"- Below-target without watering: {payload['baseline_metrics']['below_target_without_watering_count']}",
        "",
        "## Search Results",
        "",
    ]

    if payload["candidate_search_results"]:
        for candidate in payload["candidate_search_results"]:
            lines.extend(
                [
                    f"### Search Rank {candidate['rank']}: `{candidate['candidate_id']}`",
                    "",
                    f"- Overrides: `{json.dumps(candidate['parameter_overrides'], sort_keys=True)}`",
                    f"- Search score: {candidate['score']}",
                    f"- Replay count: {candidate['scenario_count']}",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "No non-baseline candidates were produced by the search grid.",
                "",
            ]
        )

    lines.extend(
        [
            "## Winning Candidate",
            "",
        ]
    )

    if payload["winning_candidate"] is None:
        lines.extend(
            [
                "No winning candidate was available.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"- Candidate id: `{payload['winning_candidate']['candidate_id']}`",
                f"- Overrides: `{json.dumps(payload['winning_candidate']['parameter_overrides'], sort_keys=True)}`",
                f"- Comparison rank: {payload['winning_candidate']['rank']}",
                f"- Search rank: {payload['winning_candidate'].get('search_rank')}",
                f"- Search score: {payload['winning_candidate'].get('search_score')}",
                f"- Total dose delta ml: {payload['winning_candidate']['comparison_to_baseline']['total_dose_delta_ml']}",
                f"- Wet-side block delta: {payload['winning_candidate']['comparison_to_baseline']['wet_side_block_delta']}",
                f"- Below-target without watering delta: {payload['winning_candidate']['comparison_to_baseline']['below_target_without_watering_delta']}",
                "",
            ]
        )

    lines.extend(
        [
            "## Failure Reasons",
            "",
        ]
    )

    if payload["failure_reasons"]:
        for failure_reason in payload["failure_reasons"]:
            lines.append(f"- `{failure_reason['code']}`: {failure_reason['message']}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Notes",
            "",
        ]
    )
    for note in payload["notes"]:
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def _validate_output_path(path: str | Path, *, label: str) -> Path:
    target = Path(path)
    if target.exists() and target.is_dir():
        raise CalibrationWorkflowError(f"{label} points to a directory, not a file: {target}")

    parent = target.parent
    if not parent.exists():
        raise CalibrationWorkflowError(
            f"{label} parent directory does not exist: {parent}"
        )
    if not parent.is_dir():
        raise CalibrationWorkflowError(
            f"{label} parent path is not a directory: {parent}"
        )
    return target


def write_workflow_artifacts(
    payload: dict[str, Any],
    *,
    workflow_output_path: str | Path = DEFAULT_WORKFLOW_OUTPUT_PATH,
    report_output_path: str | Path = DEFAULT_REPORT_PATH,
) -> dict[str, str]:
    workflow_path = _validate_output_path(
        workflow_output_path,
        label="workflow_output_path",
    )
    report_path = _validate_output_path(
        report_output_path,
        label="report_output_path",
    )
    workflow_path.write_text(json.dumps(payload, indent=2) + "\n")
    report_path.write_text(render_workflow_report(payload))
    return {
        "workflow_output_path": str(workflow_path),
        "report_output_path": str(report_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline calibration workflow end-to-end."
    )
    parser.add_argument(
        "--family",
        required=True,
        help="Existing controller family to calibrate.",
    )
    parser.add_argument(
        "--grid",
        required=True,
        help="JSON object mapping allowed parameter paths to finite candidate value lists.",
    )
    parser.add_argument(
        "--allow-manual-review",
        action="store_true",
        help="Allow workflow output for manual-review-only controller families.",
    )
    parser.add_argument(
        "--workflow-output",
        default=str(DEFAULT_WORKFLOW_OUTPUT_PATH),
        help="Path for the final workflow JSON payload.",
    )
    parser.add_argument(
        "--report-output",
        default=str(DEFAULT_REPORT_PATH),
        help="Path for the workflow Markdown report.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_FIXTURES_PATH),
        help="Replay fixture file or directory. Defaults to tests/fixtures.",
    )
    args = parser.parse_args(argv)

    try:
        parameter_grid = json.loads(args.grid)
    except json.JSONDecodeError as exc:
        raise CalibrationWorkflowError("--grid must be valid JSON.") from exc

    payload = run_calibration_workflow(
        args.family,
        parameter_grid,
        fixture_path=args.path,
        allow_manual_review=args.allow_manual_review,
    )
    output_paths = write_workflow_artifacts(
        payload,
        workflow_output_path=args.workflow_output,
        report_output_path=args.report_output,
    )
    print(
        json.dumps(
            {
                "workflow_status": payload["workflow_status"],
                "recommended_candidate": payload["recommendation_summary"]["recommended_candidate"],
                "failure_reasons": payload["failure_reasons"],
                **output_paths,
            },
            indent=2,
        )
    )
    return 0 if payload["workflow_status"] == "recommended" else 1


if __name__ == "__main__":
    raise SystemExit(main())
