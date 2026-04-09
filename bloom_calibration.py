from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
from pathlib import Path
from typing import Any

from bloom_controller import BASE_DIR, BloomPotController, ModelValidationError
from bloom_evaluation import evaluate_replay_path, summarize_replay_results


DEFAULT_FIXTURES_PATH = BASE_DIR / "tests" / "fixtures"
ALLOWED_PARAMETER_PATHS = (
    "hard_dry_cutoff",
    "hard_wet_cutoff",
    "moisture_target.minimum",
    "moisture_target.maximum",
    "watering_dose_ml",
    "cooldown_minutes",
    "confirm_low_readings",
    "max_daily_dose_ml",
)
INTEGER_PARAMETER_PATHS = {"cooldown_minutes", "confirm_low_readings"}
PARAMETER_PATH_ORDER = {path: index for index, path in enumerate(ALLOWED_PARAMETER_PATHS)}


class CalibrationCandidateError(ValueError):
    """Raised when an offline calibration candidate is invalid."""


def _make_empty_family_summary() -> dict[str, Any]:
    return {
        "scenario_count": 0,
        "completed_scenario_count": 0,
        "rejected_scenario_count": 0,
        "total_observations": 0,
        "total_watering_events": 0,
        "total_dispensed_ml": 0.0,
        "blocked_by_cooldown": 0,
        "blocked_by_daily_budget": 0,
        "blocked_by_manual_review": 0,
        "blocked_by_reservoir": 0,
        "wet_cutoff_blocks": 0,
        "hard_dry_events": 0,
        "confirmation_wait_events": 0,
        "below_target_steps": 0,
        "below_target_without_watering": 0,
        "unresolved_species_rejections": 0,
        "unknown_plant_rejections": 0,
        "final_reservoir_ml_total": 0.0,
    }


def _parameter_sort_key(path: str) -> tuple[int, str]:
    return (PARAMETER_PATH_ORDER.get(path, len(ALLOWED_PARAMETER_PATHS)), path)


def _normalize_numeric_value(parameter_path: str, value: Any) -> int | float:
    if isinstance(value, bool):
        raise CalibrationCandidateError(
            f"Candidate override {parameter_path} must be numeric, not boolean."
        )

    if parameter_path in INTEGER_PARAMETER_PATHS:
        if isinstance(value, int):
            normalized_value = value
        elif isinstance(value, float) and value.is_integer():
            normalized_value = int(value)
        else:
            raise CalibrationCandidateError(
                f"Candidate override {parameter_path} must be an integer value."
            )
        if normalized_value < 0:
            raise CalibrationCandidateError(
                f"Candidate override {parameter_path} must be nonnegative."
            )
        return normalized_value

    try:
        normalized_value = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationCandidateError(
            f"Candidate override {parameter_path} must be numeric."
        ) from exc
    if not math.isfinite(normalized_value):
        raise CalibrationCandidateError(
            f"Candidate override {parameter_path} must be finite."
        )
    return normalized_value


def _read_nested_value(profile: dict[str, Any], parameter_path: str) -> Any:
    current_value: Any = profile
    for part in parameter_path.split("."):
        if not isinstance(current_value, dict) or part not in current_value:
            raise CalibrationCandidateError(
                f"Candidate override references unknown parameter path: {parameter_path}"
            )
        current_value = current_value[part]
    return current_value


def _write_nested_value(profile: dict[str, Any], parameter_path: str, value: Any) -> None:
    parts = parameter_path.split(".")
    target = profile
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            raise CalibrationCandidateError(
                f"Candidate override references unknown parameter path: {parameter_path}"
            )
        target = target[part]
    if parts[-1] not in target:
        raise CalibrationCandidateError(
            f"Candidate override references unknown parameter path: {parameter_path}"
        )
    target[parts[-1]] = value


def _normalize_candidate_overrides(
    baseline_profile: dict[str, Any],
    parameter_overrides: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(parameter_overrides, dict):
        raise CalibrationCandidateError("parameter_overrides must be a dictionary.")

    normalized_overrides: dict[str, Any] = {}
    for parameter_path, raw_value in sorted(parameter_overrides.items(), key=lambda item: _parameter_sort_key(item[0])):
        if parameter_path not in ALLOWED_PARAMETER_PATHS:
            raise CalibrationCandidateError(
                f"Candidate override is not allowed for offline search: {parameter_path}"
            )
        _read_nested_value(baseline_profile, parameter_path)
        normalized_value = _normalize_numeric_value(parameter_path, raw_value)
        if _read_nested_value(baseline_profile, parameter_path) != normalized_value:
            normalized_overrides[parameter_path] = normalized_value
    return normalized_overrides


def _format_candidate_value(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    return format(value, ".6g")


def build_candidate_id(controller_family: str, parameter_overrides: dict[str, Any]) -> str:
    if not parameter_overrides:
        return f"{controller_family}:baseline"
    override_parts = [
        f"{parameter_path}={_format_candidate_value(parameter_overrides[parameter_path])}"
        for parameter_path in sorted(parameter_overrides, key=_parameter_sort_key)
    ]
    return f"{controller_family}:" + ",".join(override_parts)


def _build_candidate_controller(
    baseline_controller: BloomPotController,
    controller_family: str,
    parameter_overrides: dict[str, Any],
) -> tuple[BloomPotController, dict[str, Any]]:
    if controller_family not in baseline_controller.controller_profiles:
        raise CalibrationCandidateError(
            f"Unknown controller family for offline calibration: {controller_family}"
        )

    baseline_profile = baseline_controller.controller_profiles[controller_family]
    normalized_overrides = _normalize_candidate_overrides(
        baseline_profile,
        parameter_overrides,
    )

    candidate_profiles = copy.deepcopy(baseline_controller.controller_profiles)
    candidate_profile = candidate_profiles[controller_family]
    for parameter_path, value in normalized_overrides.items():
        _write_nested_value(candidate_profile, parameter_path, value)

    try:
        BloomPotController._validate_controller_profile(
            controller_family,
            candidate_profile,
            data_path="offline_calibration_candidate",
        )
        BloomPotController._validate_model_relationships(
            baseline_controller.plant_facts,
            baseline_controller.plant_attributes,
            candidate_profiles,
            baseline_controller.unresolved_species,
        )
    except ModelValidationError as exc:
        raise CalibrationCandidateError(str(exc)) from exc

    candidate_controller = BloomPotController.__new__(BloomPotController)
    candidate_controller.controller_profiles = candidate_profiles
    candidate_controller.plant_facts = baseline_controller.plant_facts
    candidate_controller.plant_attributes = baseline_controller.plant_attributes
    candidate_controller.unresolved_species = baseline_controller.unresolved_species
    candidate_controller.default_reservoir_ml = baseline_controller.default_reservoir_ml
    return candidate_controller, normalized_overrides


def score_candidate_report(family_summary: dict[str, Any]) -> float:
    score = 0.0
    score -= family_summary["below_target_steps"] * 4.0
    score -= family_summary["below_target_without_watering"] * 8.0
    score -= family_summary["blocked_by_cooldown"] * 2.5
    score -= family_summary["blocked_by_daily_budget"] * 6.0
    score -= family_summary["blocked_by_reservoir"] * 6.0
    score -= family_summary["confirmation_wait_events"] * 1.5
    score -= family_summary["total_watering_events"] * 2.0
    score -= family_summary["total_dispensed_ml"] * 0.05
    return round(score, 3)


def evaluate_candidate(
    controller_family: str,
    parameter_overrides: dict[str, Any],
    *,
    fixture_path: str | Path = DEFAULT_FIXTURES_PATH,
    baseline_controller: BloomPotController | None = None,
) -> dict[str, Any]:
    evaluation_controller = baseline_controller or BloomPotController()
    candidate_controller, normalized_overrides = _build_candidate_controller(
        evaluation_controller,
        controller_family,
        parameter_overrides,
    )
    replay_results = evaluate_replay_path(
        fixture_path,
        controller=candidate_controller,
        unresolved_species=evaluation_controller.unresolved_species,
    )
    aggregate_summary = summarize_replay_results(replay_results)
    family_summary = copy.deepcopy(
        aggregate_summary["family_summary"].get(
            controller_family,
            _make_empty_family_summary(),
        )
    )
    overall_summary = copy.deepcopy(aggregate_summary["overall_summary"])
    candidate_id = build_candidate_id(controller_family, normalized_overrides)

    return {
        "candidate_id": candidate_id,
        "controller_family": controller_family,
        "parameter_overrides": normalized_overrides,
        "scenario_count": family_summary["scenario_count"],
        "family_summary": family_summary,
        "overall_summary": overall_summary,
        "score": score_candidate_report(family_summary),
        "rank": None,
        "validity_status": "valid",
        "replay_results": replay_results,
    }


def _normalize_parameter_grid(
    baseline_profile: dict[str, Any],
    parameter_grid: dict[str, Any],
) -> dict[str, tuple[int | float, ...]]:
    if not isinstance(parameter_grid, dict):
        raise CalibrationCandidateError("parameter_grid must be a dictionary.")

    normalized_grid: dict[str, tuple[int | float, ...]] = {}
    for parameter_path, raw_values in sorted(parameter_grid.items(), key=lambda item: _parameter_sort_key(item[0])):
        if isinstance(raw_values, (str, bytes)) or not hasattr(raw_values, "__iter__"):
            raise CalibrationCandidateError(
                f"Grid values for {parameter_path} must be a finite iterable of numbers."
            )
        normalized_values = sorted(
            {
                _normalize_candidate_overrides(baseline_profile, {parameter_path: value}).get(
                    parameter_path,
                    _read_nested_value(baseline_profile, parameter_path),
                )
                for value in raw_values
            },
            key=float,
        )
        if not normalized_values:
            raise CalibrationCandidateError(
                f"Grid values for {parameter_path} cannot be empty."
            )
        normalized_grid[parameter_path] = tuple(normalized_values)
    return normalized_grid


def search_candidate_grid(
    controller_family: str,
    parameter_grid: dict[str, Any],
    *,
    fixture_path: str | Path = DEFAULT_FIXTURES_PATH,
    baseline_controller: BloomPotController | None = None,
    include_baseline: bool = True,
) -> list[dict[str, Any]]:
    evaluation_controller = baseline_controller or BloomPotController()
    if controller_family not in evaluation_controller.controller_profiles:
        raise CalibrationCandidateError(
            f"Unknown controller family for offline calibration: {controller_family}"
        )

    baseline_profile = evaluation_controller.controller_profiles[controller_family]
    normalized_grid = _normalize_parameter_grid(baseline_profile, parameter_grid)
    parameter_paths = sorted(normalized_grid, key=_parameter_sort_key)

    candidate_reports: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()

    if include_baseline:
        baseline_report = evaluate_candidate(
            controller_family,
            {},
            fixture_path=fixture_path,
            baseline_controller=evaluation_controller,
        )
        candidate_reports.append(baseline_report)
        seen_candidate_ids.add(baseline_report["candidate_id"])

    grid_values = [normalized_grid[path] for path in parameter_paths]
    if not grid_values:
        ranked_reports = sorted(
            candidate_reports,
            key=lambda report: (-report["score"], report["candidate_id"]),
        )
    else:
        for combination in itertools.product(*grid_values):
            raw_overrides = dict(zip(parameter_paths, combination, strict=True))
            report = evaluate_candidate(
                controller_family,
                raw_overrides,
                fixture_path=fixture_path,
                baseline_controller=evaluation_controller,
            )
            if report["candidate_id"] in seen_candidate_ids:
                continue
            candidate_reports.append(report)
            seen_candidate_ids.add(report["candidate_id"])

        ranked_reports = sorted(
            candidate_reports,
            key=lambda report: (-report["score"], report["candidate_id"]),
        )

    for rank, report in enumerate(ranked_reports, start=1):
        report["rank"] = rank
    return ranked_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search offline controller calibration candidates.")
    parser.add_argument(
        "--family",
        required=True,
        help="Existing controller family to calibrate offline.",
    )
    parser.add_argument(
        "--grid",
        required=True,
        help="JSON object mapping allowed parameter paths to finite candidate value lists.",
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
        raise CalibrationCandidateError("--grid must be valid JSON.") from exc

    results = search_candidate_grid(
        args.family,
        parameter_grid,
        fixture_path=args.path,
    )
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
