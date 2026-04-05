from __future__ import annotations

import argparse
import json
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

    previous_time = None
    for index, observation in enumerate(payload["observations"]):
        current_time = _parse_timestamp(observation["timestamp"])
        if previous_time is not None and current_time < previous_time:
            raise ReplayValidationError(
                f"Scenario {payload['scenario_id']} has observations out of order at index {index}."
            )
        previous_time = current_time

    return payload


def _empty_summary(*, total_observations: int, final_reservoir_ml: float) -> dict[str, Any]:
    return {
        "total_observations": total_observations,
        "total_watering_events": 0,
        "total_dispensed_ml": 0.0,
        "blocked_by_cooldown": 0,
        "blocked_by_daily_budget": 0,
        "blocked_by_manual_review": 0,
        "blocked_by_reservoir": 0,
        "wet_cutoff_blocks": 0,
        "hard_dry_events": 0,
        "confirmation_wait_events": 0,
        "unresolved_rejections": 0,
        "final_reservoir_ml": round(final_reservoir_ml, 3),
    }


def _summarize_trace(
    controller: BloomPotController,
    *,
    trace: list[dict[str, Any]],
    total_observations: int,
    final_reservoir_ml: float,
) -> dict[str, Any]:
    summary = _empty_summary(
        total_observations=total_observations,
        final_reservoir_ml=final_reservoir_ml,
    )
    summary["total_watering_events"] = sum(1 for step in trace if step["pump_on"])
    summary["total_dispensed_ml"] = round(sum(step["dose_ml"] for step in trace), 3)
    summary["blocked_by_cooldown"] = sum(
        1 for step in trace if step["reason_code"] == "cooldown_block"
    )
    summary["blocked_by_daily_budget"] = sum(
        1 for step in trace if step["reason_code"] == "daily_budget_block"
    )
    summary["blocked_by_manual_review"] = sum(
        1 for step in trace if step["reason_code"] == "manual_review_required"
    )
    summary["blocked_by_reservoir"] = sum(
        1 for step in trace if step["reason_code"] == "reservoir_block"
    )
    summary["wet_cutoff_blocks"] = sum(
        1 for step in trace if step["reason_code"] == "wet_cutoff_block"
    )
    summary["confirmation_wait_events"] = sum(
        1 for step in trace if step["reason_code"] == "confirmation_wait"
    )
    summary["hard_dry_events"] = sum(
        1
        for step in trace
        if step["normalized_soil_moisture"]
        <= controller.controller_profiles[step["controller_family"]]["hard_dry_cutoff"]
    )
    return summary


def evaluate_replay_scenario(
    scenario: dict[str, Any],
    *,
    controller: BloomPotController | None = None,
    unresolved_species: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evaluation_controller = controller or BloomPotController()
    unresolved_catalog = unresolved_species
    if unresolved_catalog is None:
        unresolved_catalog = load_unresolved_species()

    initial_state = ControllerState.from_dict(
        scenario["initial_state"],
        default_reservoir_ml=evaluation_controller.default_reservoir_ml,
    )

    if scenario["plant_id"] not in evaluation_controller.plant_facts:
        if scenario["plant_id"] in unresolved_catalog:
            rejection_code = "known_unresolved_plant_id"
            rejection_reason = "Plant id is present in unresolved_species.json."
            unresolved_rejections = 1
        else:
            rejection_code = "unknown_plant_id"
            rejection_reason = "Plant id is not present in the curated plant_facts.json catalog."
            unresolved_rejections = 0

        summary = _empty_summary(
            total_observations=len(scenario["observations"]),
            final_reservoir_ml=initial_state.reservoir_ml,
        )
        summary["unresolved_rejections"] = unresolved_rejections
        return {
            "scenario_id": scenario["scenario_id"],
            "plant_id": scenario["plant_id"],
            "status": "rejected",
            "trace": [],
            "summary": summary,
            "final_state": initial_state.to_dict(),
            "rejection": {
                "code": rejection_code,
                "reason": rejection_reason,
            },
        }

    result = evaluation_controller.simulate_scenario(
        scenario["plant_id"],
        scenario["observations"],
        initial_state=scenario["initial_state"],
    )
    summary = _summarize_trace(
        evaluation_controller,
        trace=result["trace"],
        total_observations=len(scenario["observations"]),
        final_reservoir_ml=result["final_state"]["reservoir_ml"],
    )
    return {
        "scenario_id": scenario["scenario_id"],
        "plant_id": scenario["plant_id"],
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
) -> list[dict[str, Any]]:
    target = Path(path)
    paths = [target] if target.is_file() else sorted(target.glob("*.json"))
    if not paths:
        raise ReplayValidationError(f"No replay fixtures found at {target}.")

    evaluation_controller = controller or BloomPotController()
    unresolved_catalog = unresolved_species
    if unresolved_catalog is None:
        unresolved_catalog = load_unresolved_species()

    return [
        evaluate_replay_scenario(
            load_replay_scenario(scenario_path),
            controller=evaluation_controller,
            unresolved_species=unresolved_catalog,
        )
        for scenario_path in paths
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay Bloom Pot controller fixtures.")
    parser.add_argument("path", help="Replay fixture file or directory.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only scenario ids and summary metrics.",
    )
    args = parser.parse_args(argv)

    results = evaluate_replay_path(args.path)
    if args.summary_only:
        payload = [
            {
                "scenario_id": result["scenario_id"],
                "status": result["status"],
                "summary": result["summary"],
            }
            for result in results
        ]
    elif len(results) == 1:
        payload = results[0]
    else:
        payload = {"results": results}

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
