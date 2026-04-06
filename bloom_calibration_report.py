from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from bloom_calibration import (
    DEFAULT_FIXTURES_PATH,
    CalibrationCandidateError,
    _build_candidate_controller,
    build_candidate_id,
)
from bloom_controller import BASE_DIR, BloomPotController
from bloom_evaluation import evaluate_replay_path


DEFAULT_RECOMMENDATIONS_PATH = BASE_DIR / "calibration_recommendations.json"
DEFAULT_REPORT_PATH = BASE_DIR / "calibration_report.md"
APPROVED_DRY_SIDE_DECISIONS = {"hard_dry_approved", "confirmed_low_approved"}
SCORING_COMPONENT_ORDER = (
    "wet_side_block_regression",
    "manual_review_block_regression",
    "baseline_hard_dry_miss_increase",
    "below_target_without_watering_increase",
    "budget_block_increase",
    "cooldown_block_increase",
    "reservoir_block_increase",
    "dry_side_trigger_deficit",
    "wet_side_block_improvement_bonus",
    "baseline_hard_dry_miss_improvement_bonus",
    "below_target_without_watering_improvement_bonus",
    "total_dose_increase_ml",
    "water_events_increase",
    "total_dose_reduction_bonus_ml",
    "water_events_reduction_bonus",
)


class CalibrationReportError(ValueError):
    """Raised when a calibration comparison report request is invalid."""


def _round_ml(value: float) -> float:
    return round(float(value), 3)


def _empty_family_metrics() -> dict[str, Any]:
    return {
        "replay_count_processed": 0,
        "accepted_replay_count": 0,
        "rejected_replay_count": 0,
        "water_events_count": 0,
        "total_dose_ml": 0.0,
        "mean_dose_per_accepted_replay": 0.0,
        "dry_side_trigger_count": 0,
        "wet_side_block_count": 0,
        "cooldown_block_count": 0,
        "budget_block_count": 0,
        "manual_review_block_count": 0,
        "reservoir_block_count": 0,
        "unknown_or_unresolved_rejection_count": 0,
        "unknown_plant_rejection_count": 0,
        "unresolved_species_rejection_count": 0,
        "below_target_step_count": 0,
        "below_target_without_watering_count": 0,
        "baseline_hard_dry_miss_count": 0,
        "scenario_ids": [],
    }


def _normalize_candidate_spec(raw_candidate: Any) -> dict[str, Any]:
    if not isinstance(raw_candidate, dict):
        raise CalibrationReportError("Each candidate specification must be a JSON object.")

    if "parameter_overrides" in raw_candidate:
        parameter_overrides = raw_candidate["parameter_overrides"]
        candidate_label = raw_candidate.get("candidate_label", raw_candidate.get("name"))
    else:
        parameter_overrides = raw_candidate
        candidate_label = None

    if candidate_label is not None and not isinstance(candidate_label, str):
        raise CalibrationReportError("candidate_label must be a string when provided.")

    if not isinstance(parameter_overrides, dict):
        raise CalibrationReportError("parameter_overrides must be a JSON object.")

    return {
        "candidate_label": candidate_label,
        "parameter_overrides": parameter_overrides,
        "raw_candidate": raw_candidate,
    }


def normalize_candidate_specs(candidate_payload: Any) -> list[dict[str, Any]]:
    if isinstance(candidate_payload, list):
        candidates = [_normalize_candidate_spec(candidate) for candidate in candidate_payload]
    elif isinstance(candidate_payload, dict):
        candidates = [_normalize_candidate_spec(candidate_payload)]
    else:
        raise CalibrationReportError(
            "--candidates must be a JSON object or a JSON array of candidate objects."
        )

    if not candidates:
        raise CalibrationReportError("At least one candidate must be provided.")
    return candidates


def _require_calibration_target(
    controller: BloomPotController,
    controller_family: str,
    *,
    allow_manual_review: bool,
) -> dict[str, Any]:
    if controller_family not in controller.controller_profiles:
        raise CalibrationReportError(f"Unknown controller family: {controller_family}")

    profile = controller.controller_profiles[controller_family]
    if not profile["autowater_enabled"] and not allow_manual_review:
        raise CalibrationReportError(
            f"Controller family {controller_family} is manual-review-only and is not an "
            "autowater calibration target unless --allow-manual-review is set."
        )
    return profile


def _family_replay_results(
    path: str | Path,
    *,
    controller: BloomPotController,
    controller_family: str,
) -> list[dict[str, Any]]:
    replay_results = evaluate_replay_path(
        path,
        controller=controller,
        unresolved_species=controller.unresolved_species,
    )
    family_results = [
        result for result in replay_results if result["controller_family"] == controller_family
    ]
    if not family_results:
        raise CalibrationReportError(
            f"No replay fixtures for controller family {controller_family} were found at {path}."
        )
    return family_results


def _build_family_metrics(
    controller: BloomPotController,
    controller_family: str,
    replay_results: list[dict[str, Any]],
    *,
    baseline_hard_dry_cutoff: float,
) -> dict[str, Any]:
    metrics = _empty_family_metrics()

    for result in replay_results:
        metrics["replay_count_processed"] += 1
        metrics["scenario_ids"].append(result["scenario_id"])
        summary = result["summary"]

        if result["status"] == "completed":
            metrics["accepted_replay_count"] += 1
        else:
            metrics["rejected_replay_count"] += 1

        metrics["water_events_count"] += summary["total_watering_events"]
        metrics["total_dose_ml"] = _round_ml(
            metrics["total_dose_ml"] + summary["total_dispensed_ml"]
        )
        metrics["wet_side_block_count"] += summary["wet_cutoff_blocks"]
        metrics["cooldown_block_count"] += summary["blocked_by_cooldown"]
        metrics["budget_block_count"] += summary["blocked_by_daily_budget"]
        metrics["manual_review_block_count"] += summary["blocked_by_manual_review"]
        metrics["reservoir_block_count"] += summary["blocked_by_reservoir"]
        metrics["unknown_plant_rejection_count"] += summary["unknown_plant_rejections"]
        metrics["unresolved_species_rejection_count"] += summary["unresolved_species_rejections"]
        metrics["below_target_step_count"] += summary["below_target_steps"]
        metrics["below_target_without_watering_count"] += summary[
            "below_target_without_watering"
        ]

        for step in result["trace"]:
            if step["decision_code"] in APPROVED_DRY_SIDE_DECISIONS:
                metrics["dry_side_trigger_count"] += 1
            if step["soil_moisture"] <= baseline_hard_dry_cutoff and not step["pump_on"]:
                metrics["baseline_hard_dry_miss_count"] += 1

    accepted_replays = metrics["accepted_replay_count"]
    if accepted_replays:
        metrics["mean_dose_per_accepted_replay"] = _round_ml(
            metrics["total_dose_ml"] / accepted_replays
        )
    metrics["unknown_or_unresolved_rejection_count"] = (
        metrics["unknown_plant_rejection_count"] + metrics["unresolved_species_rejection_count"]
    )
    metrics["scenario_ids"].sort()
    return metrics


def evaluate_baseline_profile(
    controller_family: str,
    *,
    fixture_path: str | Path = DEFAULT_FIXTURES_PATH,
    baseline_controller: BloomPotController | None = None,
    allow_manual_review: bool = False,
) -> dict[str, Any]:
    controller = baseline_controller or BloomPotController()
    baseline_profile = copy.deepcopy(
        _require_calibration_target(
            controller,
            controller_family,
            allow_manual_review=allow_manual_review,
        )
    )
    family_results = _family_replay_results(
        fixture_path,
        controller=controller,
        controller_family=controller_family,
    )
    metrics = _build_family_metrics(
        controller,
        controller_family,
        family_results,
        baseline_hard_dry_cutoff=float(baseline_profile["hard_dry_cutoff"]),
    )
    return {
        "controller_family": controller_family,
        "baseline_profile": baseline_profile,
        "baseline_metrics": metrics,
        "replay_results": family_results,
    }


def _compare_candidate_metrics(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "replay_count_delta": candidate_metrics["replay_count_processed"]
        - baseline_metrics["replay_count_processed"],
        "accepted_replay_delta": candidate_metrics["accepted_replay_count"]
        - baseline_metrics["accepted_replay_count"],
        "rejected_replay_delta": candidate_metrics["rejected_replay_count"]
        - baseline_metrics["rejected_replay_count"],
        "water_events_delta": candidate_metrics["water_events_count"]
        - baseline_metrics["water_events_count"],
        "total_dose_delta_ml": _round_ml(
            candidate_metrics["total_dose_ml"] - baseline_metrics["total_dose_ml"]
        ),
        "mean_dose_delta_ml": _round_ml(
            candidate_metrics["mean_dose_per_accepted_replay"]
            - baseline_metrics["mean_dose_per_accepted_replay"]
        ),
        "dry_side_trigger_delta": candidate_metrics["dry_side_trigger_count"]
        - baseline_metrics["dry_side_trigger_count"],
        "wet_side_block_delta": candidate_metrics["wet_side_block_count"]
        - baseline_metrics["wet_side_block_count"],
        "cooldown_block_delta": candidate_metrics["cooldown_block_count"]
        - baseline_metrics["cooldown_block_count"],
        "budget_block_delta": candidate_metrics["budget_block_count"]
        - baseline_metrics["budget_block_count"],
        "manual_review_block_delta": candidate_metrics["manual_review_block_count"]
        - baseline_metrics["manual_review_block_count"],
        "reservoir_block_delta": candidate_metrics["reservoir_block_count"]
        - baseline_metrics["reservoir_block_count"],
        "below_target_without_watering_delta": candidate_metrics[
            "below_target_without_watering_count"
        ]
        - baseline_metrics["below_target_without_watering_count"],
        "baseline_hard_dry_miss_delta": candidate_metrics["baseline_hard_dry_miss_count"]
        - baseline_metrics["baseline_hard_dry_miss_count"],
        "unknown_or_unresolved_rejection_delta": candidate_metrics[
            "unknown_or_unresolved_rejection_count"
        ]
        - baseline_metrics["unknown_or_unresolved_rejection_count"],
    }


def score_candidate_against_baseline(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    components = {
        "wet_side_block_regression": max(
            0,
            baseline_metrics["wet_side_block_count"] - candidate_metrics["wet_side_block_count"],
        ),
        "manual_review_block_regression": max(
            0,
            baseline_metrics["manual_review_block_count"]
            - candidate_metrics["manual_review_block_count"],
        ),
        "baseline_hard_dry_miss_increase": max(
            0,
            candidate_metrics["baseline_hard_dry_miss_count"]
            - baseline_metrics["baseline_hard_dry_miss_count"],
        ),
        "below_target_without_watering_increase": max(
            0,
            candidate_metrics["below_target_without_watering_count"]
            - baseline_metrics["below_target_without_watering_count"],
        ),
        "budget_block_increase": max(
            0,
            candidate_metrics["budget_block_count"] - baseline_metrics["budget_block_count"],
        ),
        "cooldown_block_increase": max(
            0,
            candidate_metrics["cooldown_block_count"] - baseline_metrics["cooldown_block_count"],
        ),
        "reservoir_block_increase": max(
            0,
            candidate_metrics["reservoir_block_count"] - baseline_metrics["reservoir_block_count"],
        ),
        "dry_side_trigger_deficit": max(
            0,
            baseline_metrics["dry_side_trigger_count"] - candidate_metrics["dry_side_trigger_count"],
        ),
        "wet_side_block_improvement_bonus": -max(
            0,
            candidate_metrics["wet_side_block_count"] - baseline_metrics["wet_side_block_count"],
        ),
        "baseline_hard_dry_miss_improvement_bonus": -max(
            0,
            baseline_metrics["baseline_hard_dry_miss_count"]
            - candidate_metrics["baseline_hard_dry_miss_count"],
        ),
        "below_target_without_watering_improvement_bonus": -max(
            0,
            baseline_metrics["below_target_without_watering_count"]
            - candidate_metrics["below_target_without_watering_count"],
        ),
        "total_dose_increase_ml": _round_ml(
            max(0.0, candidate_metrics["total_dose_ml"] - baseline_metrics["total_dose_ml"])
        ),
        "water_events_increase": max(
            0,
            candidate_metrics["water_events_count"] - baseline_metrics["water_events_count"],
        ),
        "total_dose_reduction_bonus_ml": _round_ml(
            -max(0.0, baseline_metrics["total_dose_ml"] - candidate_metrics["total_dose_ml"])
        ),
        "water_events_reduction_bonus": -max(
            0,
            baseline_metrics["water_events_count"] - candidate_metrics["water_events_count"],
        ),
    }

    ranking_key = [
        components[name] for name in SCORING_COMPONENT_ORDER
    ]
    return {
        "rule": "round9_safety_first_v1",
        "components": components,
        "ranking_key": ranking_key,
    }


def _candidate_summary_reasons(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []

    if candidate_metrics["wet_side_block_count"] > baseline_metrics["wet_side_block_count"]:
        reasons.append("increased wet-side blocking on the replay set")
    elif candidate_metrics["wet_side_block_count"] < baseline_metrics["wet_side_block_count"]:
        reasons.append("reduced wet-side blocking relative to baseline")

    if candidate_metrics["baseline_hard_dry_miss_count"] < baseline_metrics["baseline_hard_dry_miss_count"]:
        reasons.append("reduced obvious hard-dry misses")
    elif candidate_metrics["baseline_hard_dry_miss_count"] > baseline_metrics["baseline_hard_dry_miss_count"]:
        reasons.append("missed more baseline hard-dry steps")

    if candidate_metrics["below_target_without_watering_count"] < baseline_metrics["below_target_without_watering_count"]:
        reasons.append("reduced below-target no-water steps")
    elif candidate_metrics["below_target_without_watering_count"] > baseline_metrics["below_target_without_watering_count"]:
        reasons.append("increased below-target no-water steps")

    if candidate_metrics["reservoir_block_count"] > baseline_metrics["reservoir_block_count"]:
        reasons.append("introduced more reservoir-side blocking")

    if candidate_metrics["total_dose_ml"] < baseline_metrics["total_dose_ml"]:
        reasons.append("used less total water")
    elif candidate_metrics["total_dose_ml"] > baseline_metrics["total_dose_ml"]:
        reasons.append("used more total water")

    if not reasons:
        reasons.append("matched the baseline metrics on this replay set")
    return reasons


def evaluate_candidate_profile(
    controller_family: str,
    parameter_overrides: dict[str, Any],
    *,
    fixture_path: str | Path = DEFAULT_FIXTURES_PATH,
    baseline_controller: BloomPotController | None = None,
    baseline_metrics: dict[str, Any] | None = None,
    allow_manual_review: bool = False,
    candidate_label: str | None = None,
) -> dict[str, Any]:
    controller = baseline_controller or BloomPotController()
    baseline_profile = _require_calibration_target(
        controller,
        controller_family,
        allow_manual_review=allow_manual_review,
    )
    current_baseline_metrics = baseline_metrics
    if current_baseline_metrics is None:
        current_baseline_metrics = evaluate_baseline_profile(
            controller_family,
            fixture_path=fixture_path,
            baseline_controller=controller,
            allow_manual_review=allow_manual_review,
        )["baseline_metrics"]

    candidate_controller, normalized_overrides = _build_candidate_controller(
        controller,
        controller_family,
        parameter_overrides,
    )
    candidate_id = build_candidate_id(controller_family, normalized_overrides)
    family_results = _family_replay_results(
        fixture_path,
        controller=candidate_controller,
        controller_family=controller_family,
    )
    metrics = _build_family_metrics(
        candidate_controller,
        controller_family,
        family_results,
        baseline_hard_dry_cutoff=float(baseline_profile["hard_dry_cutoff"]),
    )
    comparison = _compare_candidate_metrics(current_baseline_metrics, metrics)
    score = score_candidate_against_baseline(current_baseline_metrics, metrics)

    return {
        "candidate_id": candidate_id,
        "candidate_label": candidate_label,
        "parameter_overrides": normalized_overrides,
        "candidate_profile": copy.deepcopy(
            candidate_controller.controller_profiles[controller_family]
        ),
        "metrics": metrics,
        "comparison_to_baseline": comparison,
        "score": score,
        "status": "ranked",
        "rank": None,
        "tied_rank": False,
        "summary_reasons": _candidate_summary_reasons(current_baseline_metrics, metrics),
    }


def _ranked_candidates(candidate_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        candidate_results,
        key=lambda result: (
            *result["score"]["ranking_key"],
            result["candidate_id"],
        ),
    )

    previous_key: tuple[Any, ...] | None = None
    current_rank = 0
    for position, result in enumerate(ranked, start=1):
        tie_key = tuple(result["score"]["ranking_key"])
        if tie_key != previous_key:
            current_rank = position
            previous_key = tie_key
        else:
            result["tied_rank"] = True
        result["rank"] = current_rank
    return ranked


def _candidate_better_than_baseline(candidate_result: dict[str, Any]) -> bool:
    baseline_key = [
        0 if "ml" not in name else 0.0 for name in SCORING_COMPONENT_ORDER
    ]
    return tuple(candidate_result["score"]["ranking_key"]) < tuple(baseline_key)


def _recommendation_reason(
    ranked_candidates: list[dict[str, Any]],
    rejected_candidates: list[dict[str, Any]],
) -> str:
    if not ranked_candidates:
        return "No valid candidates were available after invariant checks; keep the baseline."

    best_candidate = ranked_candidates[0]
    tied_candidates = [
        candidate
        for candidate in ranked_candidates
        if candidate["rank"] == best_candidate["rank"]
    ]

    if not _candidate_better_than_baseline(best_candidate):
        return (
            "No candidate beat the baseline under the Round 9 safety-first ranking; "
            "keep the current profile unchanged."
        )

    reason = (
        f"{best_candidate['candidate_id']} ranked first because it "
        + ", ".join(best_candidate["summary_reasons"])
        + "."
    )
    if len(tied_candidates) > 1:
        tied_ids = ", ".join(candidate["candidate_id"] for candidate in tied_candidates)
        reason += (
            " It tied on score with "
            f"{tied_ids}; the recommendation uses candidate_id as the final deterministic "
            "tie-break."
        )
    if rejected_candidates:
        reason += f" {len(rejected_candidates)} invalid candidate(s) were excluded."
    return reason


def compare_controller_family(
    controller_family: str,
    candidate_payload: Any,
    *,
    fixture_path: str | Path = DEFAULT_FIXTURES_PATH,
    baseline_controller: BloomPotController | None = None,
    allow_manual_review: bool = False,
) -> dict[str, Any]:
    controller = baseline_controller or BloomPotController()
    baseline_report = evaluate_baseline_profile(
        controller_family,
        fixture_path=fixture_path,
        baseline_controller=controller,
        allow_manual_review=allow_manual_review,
    )
    candidate_specs = normalize_candidate_specs(candidate_payload)

    baseline_candidate_id = build_candidate_id(controller_family, {})
    seen_candidate_ids = {baseline_candidate_id}
    ranked_candidates: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []

    for candidate_spec in candidate_specs:
        try:
            preview_id = build_candidate_id(
                controller_family,
                candidate_spec["parameter_overrides"],
            )
        except CalibrationCandidateError:
            preview_id = None

        if preview_id is not None and preview_id in seen_candidate_ids:
            rejected_candidates.append(
                {
                    "candidate_id": preview_id,
                    "candidate_label": candidate_spec["candidate_label"],
                    "parameter_overrides": candidate_spec["parameter_overrides"],
                    "status": "rejected",
                    "rejection_reason": "Duplicate candidate or baseline-equivalent override set.",
                }
            )
            continue

        try:
            result = evaluate_candidate_profile(
                controller_family,
                candidate_spec["parameter_overrides"],
                fixture_path=fixture_path,
                baseline_controller=controller,
                baseline_metrics=baseline_report["baseline_metrics"],
                allow_manual_review=allow_manual_review,
                candidate_label=candidate_spec["candidate_label"],
            )
        except (CalibrationCandidateError, CalibrationReportError) as exc:
            rejected_candidates.append(
                {
                    "candidate_id": preview_id,
                    "candidate_label": candidate_spec["candidate_label"],
                    "parameter_overrides": candidate_spec["parameter_overrides"],
                    "status": "rejected",
                    "rejection_reason": str(exc),
                }
            )
            continue

        seen_candidate_ids.add(result["candidate_id"])
        ranked_candidates.append(result)

    ranked_candidates = _ranked_candidates(ranked_candidates)

    recommended_candidate = None
    if ranked_candidates and _candidate_better_than_baseline(ranked_candidates[0]):
        recommended_candidate = ranked_candidates[0]["candidate_id"]

    notes = [
        f"Replay data came from {Path(fixture_path)}.",
        "No controller profile was changed automatically in Round 9.",
    ]
    if not baseline_report["baseline_profile"]["autowater_enabled"]:
        notes.append(
            "This controller family remains manual-review-only; report output does not "
            "enable live autowatering."
        )

    return {
        "controller_family": controller_family,
        "baseline_profile": baseline_report["baseline_profile"],
        "baseline_metrics": baseline_report["baseline_metrics"],
        "candidate_results": ranked_candidates,
        "recommended_candidate": recommended_candidate,
        "recommendation_reason": _recommendation_reason(
            ranked_candidates,
            rejected_candidates,
        ),
        "rejected_candidates": rejected_candidates,
        "generated_from_fixture_or_dataset": str(Path(fixture_path)),
        "notes": notes,
    }


def render_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Calibration Report",
        "",
        f"Controller family analyzed: `{payload['controller_family']}`",
        f"Generated from fixture or dataset: `{payload['generated_from_fixture_or_dataset']}`",
        "",
        "No controller profile was changed automatically.",
        "",
        "## Ranking Rule",
        "",
        "Candidates are ranked deterministically by weaker wet-side blocking first, then by "
        "obvious dry-side misses, then by broader under-response and blocking regressions, "
        "then by extra water use, and finally by conservative lower-water tie-breakers.",
        "",
        "## Baseline Summary",
        "",
        f"- Replays processed: {payload['baseline_metrics']['replay_count_processed']}",
        f"- Accepted replays: {payload['baseline_metrics']['accepted_replay_count']}",
        f"- Rejected replays: {payload['baseline_metrics']['rejected_replay_count']}",
        f"- Water events: {payload['baseline_metrics']['water_events_count']}",
        f"- Total dose ml: {payload['baseline_metrics']['total_dose_ml']}",
        f"- Mean dose per accepted replay: {payload['baseline_metrics']['mean_dose_per_accepted_replay']}",
        f"- Dry-side triggers: {payload['baseline_metrics']['dry_side_trigger_count']}",
        f"- Wet-side blocks: {payload['baseline_metrics']['wet_side_block_count']}",
        f"- Cooldown blocks: {payload['baseline_metrics']['cooldown_block_count']}",
        f"- Budget blocks: {payload['baseline_metrics']['budget_block_count']}",
        f"- Manual-review blocks: {payload['baseline_metrics']['manual_review_block_count']}",
        "",
        "## Candidate Summaries",
        "",
    ]

    if payload["candidate_results"]:
        for candidate in payload["candidate_results"]:
            label = candidate["candidate_label"] or candidate["candidate_id"]
            lines.extend(
                [
                    f"### Rank {candidate['rank']}: `{label}`",
                    "",
                    f"- Candidate id: `{candidate['candidate_id']}`",
                    f"- Overrides: `{json.dumps(candidate['parameter_overrides'], sort_keys=True)}`",
                    f"- Water events: {candidate['metrics']['water_events_count']}",
                    f"- Total dose ml: {candidate['metrics']['total_dose_ml']}",
                    f"- Wet-side blocks: {candidate['metrics']['wet_side_block_count']}",
                    f"- Cooldown blocks: {candidate['metrics']['cooldown_block_count']}",
                    f"- Budget blocks: {candidate['metrics']['budget_block_count']}",
                    f"- Dry-side triggers: {candidate['metrics']['dry_side_trigger_count']}",
                    "- Summary: " + "; ".join(candidate["summary_reasons"]),
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "No valid candidates were ranked.",
                "",
            ]
        )

    lines.extend(
        [
            "## Recommendation",
            "",
            f"Recommended candidate: `{payload['recommended_candidate']}`",
            "",
            payload["recommendation_reason"],
            "",
            "## Rejected Candidates",
            "",
        ]
    )

    if payload["rejected_candidates"]:
        for candidate in payload["rejected_candidates"]:
            lines.append(
                f"- `{candidate['candidate_id']}`: {candidate['rejection_reason']}"
            )
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


def write_recommendation_artifacts(
    payload: dict[str, Any],
    *,
    recommendation_output_path: str | Path = DEFAULT_RECOMMENDATIONS_PATH,
    report_output_path: str | Path = DEFAULT_REPORT_PATH,
) -> dict[str, str]:
    recommendation_path = Path(recommendation_output_path)
    report_path = Path(report_output_path)
    recommendation_path.write_text(json.dumps(payload, indent=2) + "\n")
    report_path.write_text(render_markdown_report(payload))
    return {
        "recommendation_output_path": str(recommendation_path),
        "report_output_path": str(report_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare baseline and candidate controller profiles on replay fixtures."
    )
    parser.add_argument(
        "--family",
        required=True,
        help="Existing controller family to analyze.",
    )
    parser.add_argument(
        "--candidates",
        required=True,
        help=(
            "JSON object or JSON array of candidate override objects. "
            "Each element may optionally include candidate_label and parameter_overrides."
        ),
    )
    parser.add_argument(
        "--allow-manual-review",
        action="store_true",
        help="Allow report generation for manual-review-only controller families.",
    )
    parser.add_argument(
        "--recommendation-output",
        default=str(DEFAULT_RECOMMENDATIONS_PATH),
        help="Path for the JSON recommendation payload.",
    )
    parser.add_argument(
        "--report-output",
        default=str(DEFAULT_REPORT_PATH),
        help="Path for the Markdown summary report.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_FIXTURES_PATH),
        help="Replay fixture file or directory. Defaults to tests/fixtures.",
    )
    args = parser.parse_args(argv)

    try:
        candidate_payload = json.loads(args.candidates)
    except json.JSONDecodeError as exc:
        raise CalibrationReportError("--candidates must be valid JSON.") from exc

    payload = compare_controller_family(
        args.family,
        candidate_payload,
        fixture_path=args.path,
        allow_manual_review=args.allow_manual_review,
    )
    output_paths = write_recommendation_artifacts(
        payload,
        recommendation_output_path=args.recommendation_output,
        report_output_path=args.report_output,
    )
    print(
        json.dumps(
            {
                "controller_family": payload["controller_family"],
                "recommended_candidate": payload["recommended_candidate"],
                **output_paths,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
