from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from bloom_controller import (
    BASE_DIR,
    BloomPotController,
    ControllerState,
    _parse_timestamp,
)


REPLAY_SCHEMA_PATH = BASE_DIR / "controller_replay.schema.json"
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
UNRESOLVED_SPECIES_SCHEMA_PATH = BASE_DIR / "unresolved_species.schema.json"


class ReplayValidationError(ValueError):
    """Raised when replay input is malformed."""


TRACE_SUMMARY_FIELDS = (
    "total_steps",
    "total_watering_events",
    "total_water_dispensed_ml",
    "steps_below_target",
    "steps_inside_target",
    "steps_above_target",
    "hard_dry_trigger_count",
    "hard_wet_block_count",
    "cooldown_block_count",
    "daily_budget_block_count",
    "reservoir_block_count",
    "manual_review_block_count",
    "confirmation_wait_count",
)
REJECTION_SUMMARY_FIELDS = (
    "unresolved_species_rejection_count",
    "unknown_plant_rejection_count",
)


def _validate_schema(payload: Any, *, schema_path: Path, data_path: str | Path) -> None:
    schema = json.loads(schema_path.read_text())
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    first_error = errors[0]
    location = ".".join(str(part) for part in first_error.absolute_path) or "<root>"
    raise ReplayValidationError(
        f"Schema validation failed for {data_path} at {location}: {first_error.message}"
    )


def load_unresolved_species(path: str | Path = UNRESOLVED_SPECIES_PATH) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text())
    _validate_schema(
        payload,
        schema_path=UNRESOLVED_SPECIES_SCHEMA_PATH,
        data_path=path,
    )

    unresolved_records: dict[str, dict[str, Any]] = {}
    for record in payload:
        plant_id = record["id"]
        if plant_id in unresolved_records:
            raise ReplayValidationError(
                f"Duplicate unresolved plant id found in {path}: {plant_id}"
            )
        unresolved_records[plant_id] = record
    return unresolved_records


def load_replay_scenario(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    _validate_schema(
        payload,
        schema_path=REPLAY_SCHEMA_PATH,
        data_path=path,
    )

    try:
        ControllerState.from_dict(payload["initial_state"], default_reservoir_ml=0.0)
    except ValueError as exc:
        raise ReplayValidationError(
            f"Replay fixture {path} has invalid initial_state: {exc}"
        ) from exc

    previous_time = None
    for index, observation in enumerate(payload["observations"]):
        current_time = _parse_timestamp(
            observation["timestamp"],
            field_name=f"observations[{index}].timestamp",
        )
        if previous_time is not None and current_time < previous_time:
            raise ReplayValidationError(
                f"Scenario {payload['scenario_id']} has observations out of order at index {index}."
            )
        previous_time = current_time

    return payload


def _empty_scenario_summary(*, final_reservoir_ml: float) -> dict[str, Any]:
    return {
        "total_steps": 0,
        "total_watering_events": 0,
        "total_water_dispensed_ml": 0.0,
        "steps_below_target": 0,
        "steps_inside_target": 0,
        "steps_above_target": 0,
        "hard_dry_trigger_count": 0,
        "hard_wet_block_count": 0,
        "cooldown_block_count": 0,
        "daily_budget_block_count": 0,
        "reservoir_block_count": 0,
        "manual_review_block_count": 0,
        "confirmation_wait_count": 0,
        "unresolved_species_rejection_count": 0,
        "unknown_plant_rejection_count": 0,
        "final_reservoir_ml": round(final_reservoir_ml, 3),
    }


def _empty_aggregate_summary() -> dict[str, Any]:
    return {
        "scenario_count": 0,
        "completed_scenario_count": 0,
        "rejected_scenario_count": 0,
        "total_steps": 0,
        "total_watering_events": 0,
        "total_water_dispensed_ml": 0.0,
        "steps_below_target": 0,
        "steps_inside_target": 0,
        "steps_above_target": 0,
        "hard_dry_trigger_count": 0,
        "hard_wet_block_count": 0,
        "cooldown_block_count": 0,
        "daily_budget_block_count": 0,
        "reservoir_block_count": 0,
        "manual_review_block_count": 0,
        "confirmation_wait_count": 0,
        "unresolved_species_rejection_count": 0,
        "unknown_plant_rejection_count": 0,
    }


def _classify_target_position(step: dict[str, Any]) -> str:
    lower_target, upper_target = step["target_band"]
    soil_moisture = step["soil_moisture"]
    if soil_moisture < lower_target:
        return "below"
    if soil_moisture <= upper_target:
        return "inside"
    return "above"


def _summarize_trace(
    controller: BloomPotController,
    *,
    trace: list[dict[str, Any]],
    final_reservoir_ml: float,
) -> dict[str, Any]:
    summary = _empty_scenario_summary(final_reservoir_ml=final_reservoir_ml)
    decision_counts = Counter(step["decision_code"] for step in trace)

    summary["total_steps"] = len(trace)
    summary["total_watering_events"] = sum(1 for step in trace if step["pump_on"])
    summary["total_water_dispensed_ml"] = round(sum(step["dose_ml"] for step in trace), 3)
    summary["steps_below_target"] = sum(
        1 for step in trace if _classify_target_position(step) == "below"
    )
    summary["steps_inside_target"] = sum(
        1 for step in trace if _classify_target_position(step) == "inside"
    )
    summary["steps_above_target"] = sum(
        1 for step in trace if _classify_target_position(step) == "above"
    )
    summary["hard_wet_block_count"] = decision_counts["wet_cutoff_block"]
    summary["cooldown_block_count"] = decision_counts["cooldown_block"]
    summary["daily_budget_block_count"] = decision_counts["daily_budget_block"]
    summary["reservoir_block_count"] = decision_counts["reservoir_block"]
    summary["manual_review_block_count"] = decision_counts["manual_review_block"]
    summary["confirmation_wait_count"] = decision_counts["confirmation_wait"]
    summary["hard_dry_trigger_count"] = sum(
        1
        for step in trace
        if step["soil_moisture"]
        <= controller.controller_profiles[step["controller_family"]]["hard_dry_cutoff"]
    )
    return summary


def _accumulate_summary(
    target: dict[str, Any],
    source: dict[str, Any],
    *,
    include_rejections: bool,
) -> None:
    for field in TRACE_SUMMARY_FIELDS:
        target[field] += source[field]

    if include_rejections:
        for field in REJECTION_SUMMARY_FIELDS:
            target[field] += source[field]

    target["total_water_dispensed_ml"] = round(target["total_water_dispensed_ml"], 3)


def _build_scenario_summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": result["scenario_id"],
            "plant_id": result["plant_id"],
            "controller_family": result["controller_family"],
            "status": result["status"],
            "observation_count": result["observation_count"],
            "summary": result["summary"],
            "rejection": result["rejection"],
        }
        for result in results
    ]


def _build_family_summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    families: dict[str, dict[str, Any]] = {}

    for result in results:
        family = result["controller_family"]
        if family is None:
            continue

        summary = families.setdefault(
            family,
            {
                "controller_family": family,
                "scenario_count": 0,
                "completed_scenario_count": 0,
                "rejected_scenario_count": 0,
                "scenario_ids": [],
                "summary": _empty_aggregate_summary(),
            },
        )
        summary["scenario_count"] += 1
        summary["scenario_ids"].append(result["scenario_id"])
        summary["summary"]["scenario_count"] += 1
        if result["status"] == "completed":
            summary["completed_scenario_count"] += 1
            summary["summary"]["completed_scenario_count"] += 1
        else:
            summary["rejected_scenario_count"] += 1
            summary["summary"]["rejected_scenario_count"] += 1
        _accumulate_summary(
            summary["summary"],
            result["summary"],
            include_rejections=True,
        )

    ordered_families = []
    for family in sorted(families):
        summary = families[family]
        summary["scenario_ids"] = sorted(summary["scenario_ids"])
        ordered_families.append(summary)
    return ordered_families


def _build_overall_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _empty_aggregate_summary()
    summary["scenario_count"] = len(results)

    for result in results:
        if result["status"] == "completed":
            summary["completed_scenario_count"] += 1
        else:
            summary["rejected_scenario_count"] += 1
        _accumulate_summary(summary, result["summary"], include_rejections=True)

    return summary


def _build_evaluation_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scenario_summaries": _build_scenario_summaries(results),
        "family_summaries": _build_family_summaries(results),
        "overall_summary": _build_overall_summary(results),
        "scenarios": results,
    }


def evaluate_replay_scenario(
    scenario: dict[str, Any],
    *,
    controller: BloomPotController | None = None,
    unresolved_species: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evaluation_controller = controller or BloomPotController()
    unresolved_catalog = unresolved_species
    if unresolved_catalog is None:
        unresolved_catalog = evaluation_controller.unresolved_species

    initial_state = ControllerState.from_dict(
        scenario["initial_state"],
        default_reservoir_ml=evaluation_controller.default_reservoir_ml,
    )
    controller_family = None
    if scenario["plant_id"] in evaluation_controller.plant_facts:
        controller_family = evaluation_controller.plant_facts[scenario["plant_id"]][
            "controller_family"
        ]

    if scenario["plant_id"] not in evaluation_controller.plant_facts:
        summary = _empty_scenario_summary(final_reservoir_ml=initial_state.reservoir_ml)

        if scenario["plant_id"] in unresolved_catalog:
            summary["unresolved_species_rejection_count"] = 1
            rejection = {
                "code": "unresolved_species_id",
                "reason": "Plant id is present in unresolved_species.json but not in plant_facts.json.",
            }
        else:
            summary["unknown_plant_rejection_count"] = 1
            rejection = {
                "code": "unknown_plant_id",
                "reason": "Plant id is not present in plant_facts.json or unresolved_species.json.",
            }

        return {
            "scenario_id": scenario["scenario_id"],
            "plant_id": scenario["plant_id"],
            "controller_family": controller_family,
            "observation_count": len(scenario["observations"]),
            "status": "rejected",
            "trace": [],
            "summary": summary,
            "final_state": initial_state.to_dict(),
            "rejection": rejection,
        }

    result = evaluation_controller.simulate_scenario(
        scenario["plant_id"],
        scenario["observations"],
        initial_state=scenario["initial_state"],
    )
    summary = _summarize_trace(
        evaluation_controller,
        trace=result["trace"],
        final_reservoir_ml=result["final_state"]["reservoir_ml"],
    )
    return {
        "scenario_id": scenario["scenario_id"],
        "plant_id": scenario["plant_id"],
        "controller_family": controller_family,
        "observation_count": len(scenario["observations"]),
        "status": "completed",
        "trace": result["trace"],
        "summary": summary,
        "final_state": result["final_state"],
        "rejection": None,
    }


def evaluate_replay_path(
    path: str | Path,
    *,
    controller: BloomPotController | None = None,
    unresolved_species: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    target = Path(path)
    paths = [target] if target.is_file() else sorted(target.glob("*.json"))
    if not paths:
        raise ReplayValidationError(f"No replay fixtures found at {target}.")

    evaluation_controller = controller or BloomPotController()
    unresolved_catalog = unresolved_species
    if unresolved_catalog is None:
        unresolved_catalog = evaluation_controller.unresolved_species

    results = [
        evaluate_replay_scenario(
            load_replay_scenario(scenario_path),
            controller=evaluation_controller,
            unresolved_species=unresolved_catalog,
        )
        for scenario_path in paths
    ]
    return _build_evaluation_report(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay Bloom Pot controller fixtures.")
    parser.add_argument("path", help="Replay fixture file or directory.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only scenario ids and summary metrics.",
    )
    args = parser.parse_args(argv)

    report = evaluate_replay_path(args.path)
    if args.summary_only:
        payload = {
            "scenario_summaries": report["scenario_summaries"],
            "family_summaries": report["family_summaries"],
            "overall_summary": report["overall_summary"],
        }
    else:
        payload = report

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
