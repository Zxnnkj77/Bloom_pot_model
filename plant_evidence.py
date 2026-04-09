from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from bloom_evaluation import evaluate_replay_path


BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_RECORDS_PATH = BASE_DIR / "evidence_records.json"
EVIDENCE_RECORDS_SCHEMA_PATH = BASE_DIR / "evidence_records.schema.json"
PLANT_ATTRIBUTES_PATH = BASE_DIR / "plant_attributes.json"
PLANT_ATTRIBUTES_SCHEMA_PATH = BASE_DIR / "plant_attributes.schema.json"
PLANT_FACTS_PATH = BASE_DIR / "plant_facts.json"
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
CONTROLLER_PROFILES_PATH = BASE_DIR / "controller_profiles.json"
FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"
EVIDENCE_COVERAGE_SUMMARY_PATH = BASE_DIR / "evidence_coverage_summary.json"


LEGACY_SOURCE_FILES = {
    "plant_facts.json": PLANT_FACTS_PATH,
    "unresolved_species.json": UNRESOLVED_SPECIES_PATH,
}
REPLAY_ATTRIBUTE_TO_SUMMARY_KEY = {
    "replay_total_watering_events": "total_watering_events",
    "replay_blocked_by_cooldown": "blocked_by_cooldown",
    "replay_blocked_by_manual_review": "blocked_by_manual_review",
    "replay_unresolved_species_rejections": "unresolved_species_rejections",
}
CONTROLLER_PROFILE_ATTRIBUTE_MAP = {
    "controller_autowater_enabled": "autowater_enabled",
}


class EvidenceValidationError(ValueError):
    """Raised when the evidence bundle or derived outputs are inconsistent."""


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def _validate_schema(payload: Any, *, schema_path: Path, data_path: str | Path) -> None:
    schema = _load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    first_error = errors[0]
    location = ".".join(str(part) for part in first_error.absolute_path) or "<root>"
    raise EvidenceValidationError(
        f"Schema validation failed for {data_path} at {location}: {first_error.message}"
    )


def _load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    return {record["id"]: record for record in _load_json(path)}


def _load_replay_index() -> dict[str, dict[str, Any]]:
    results = evaluate_replay_path(FIXTURES_DIR)
    return {result["scenario_id"]: result for result in results}


def _records_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = record["id"]
        if record_id in indexed:
            raise EvidenceValidationError(f"Duplicate evidence record id: {record_id}")
        indexed[record_id] = record
    return indexed


def _group_records_by_plant(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for record in sorted(records, key=lambda item: (item["plant_id"], item["attribute_name"], item["id"])):
        plant_group = grouped.setdefault(record["plant_id"], {})
        attribute_name = record["attribute_name"]
        if attribute_name in plant_group:
            raise EvidenceValidationError(
                f"Plant {record['plant_id']} has duplicate evidence for attribute {attribute_name}."
            )
        plant_group[attribute_name] = record
    return grouped


def _validate_legacy_record(
    record: dict[str, Any],
    *,
    plant_facts: dict[str, dict[str, Any]],
    unresolved_species: dict[str, dict[str, Any]],
) -> None:
    provenance = record["provenance"]
    source_file = provenance["source_file"]
    if source_file not in LEGACY_SOURCE_FILES:
        raise EvidenceValidationError(
            f"Legacy evidence record {record['id']} references unsupported source_file {source_file}."
        )
    if provenance["source_record_id"] != record["plant_id"]:
        raise EvidenceValidationError(
            f"Legacy evidence record {record['id']} must reference its own plant_id."
        )

    source_catalog = plant_facts if source_file == "plant_facts.json" else unresolved_species
    source_record = source_catalog.get(record["plant_id"])
    if source_record is None:
        raise EvidenceValidationError(
            f"Legacy evidence record {record['id']} points to missing source record {record['plant_id']}."
        )

    attribute_name = record["attribute_name"]
    if attribute_name not in source_record:
        raise EvidenceValidationError(
            f"Legacy evidence record {record['id']} references missing attribute {attribute_name}."
        )
    if source_record[attribute_name] != record["value"]:
        raise EvidenceValidationError(
            f"Legacy evidence record {record['id']} does not match {source_file}:{record['plant_id']}.{attribute_name}."
        )


def _validate_controller_profile_record(
    record: dict[str, Any],
    *,
    controller_profiles: dict[str, dict[str, Any]],
) -> None:
    provenance = record["provenance"]
    attribute_name = record["attribute_name"]
    if attribute_name not in CONTROLLER_PROFILE_ATTRIBUTE_MAP:
        raise EvidenceValidationError(
            f"Controller profile evidence record {record['id']} uses unsupported attribute {attribute_name}."
        )
    controller_family = provenance["source_controller_family"]
    profile = controller_profiles.get(controller_family)
    if profile is None:
        raise EvidenceValidationError(
            f"Controller profile evidence record {record['id']} points to missing family {controller_family}."
        )
    profile_attribute = CONTROLLER_PROFILE_ATTRIBUTE_MAP[attribute_name]
    if profile[profile_attribute] != record["value"]:
        raise EvidenceValidationError(
            f"Controller profile evidence record {record['id']} does not match controller_profiles.json."
        )


def _validate_replay_record(
    record: dict[str, Any],
    *,
    replay_index: dict[str, dict[str, Any]],
) -> None:
    provenance = record["provenance"]
    scenario_id = provenance["source_scenario_id"]
    if record["attribute_name"] not in REPLAY_ATTRIBUTE_TO_SUMMARY_KEY:
        raise EvidenceValidationError(
            f"Replay evidence record {record['id']} uses unsupported attribute {record['attribute_name']}."
        )
    if scenario_id not in replay_index:
        raise EvidenceValidationError(
            f"Replay evidence record {record['id']} references missing scenario {scenario_id}."
        )
    result = replay_index[scenario_id]
    if result["plant_id"] != record["plant_id"]:
        raise EvidenceValidationError(
            f"Replay evidence record {record['id']} plant_id does not match scenario {scenario_id}."
        )
    summary_key = REPLAY_ATTRIBUTE_TO_SUMMARY_KEY[record["attribute_name"]]
    if result["summary"][summary_key] != record["value"]:
        raise EvidenceValidationError(
            f"Replay evidence record {record['id']} does not match replay summary {summary_key}."
        )


def _derive_controller_status(
    plant_records: dict[str, dict[str, Any]],
) -> tuple[str, list[str]]:
    if "unresolved_reasons" in plant_records:
        return "blocked", list(plant_records["unresolved_reasons"]["value"])

    migration_status = plant_records.get("migration_status", {}).get("value")
    manual_review_reasons = plant_records.get("manual_review_reasons", {}).get("value", [])
    autowater_enabled = plant_records.get("controller_autowater_enabled", {}).get("value")
    controller_family = plant_records.get("controller_family", {}).get("value")

    if migration_status == "accepted_manual":
        reasons = list(manual_review_reasons) or ["manual_review_required"]
        return "blocked", reasons
    if migration_status == "accepted_auto" and controller_family and autowater_enabled is True:
        return "controller_ready", []
    return "blocked", ["controller_assignment_incomplete"]


def _validate_derived_record(
    record: dict[str, Any],
    *,
    indexed_records: dict[str, dict[str, Any]],
    grouped_records: dict[str, dict[str, dict[str, Any]]],
) -> None:
    provenance = record["provenance"]
    supporting_ids = provenance["supporting_evidence_ids"]
    for supporting_id in supporting_ids:
        supporting_record = indexed_records.get(supporting_id)
        if supporting_record is None:
            raise EvidenceValidationError(
                f"Derived evidence record {record['id']} references missing support {supporting_id}."
            )
        if supporting_record["plant_id"] != record["plant_id"]:
            raise EvidenceValidationError(
                f"Derived evidence record {record['id']} references support from another plant."
            )

    if record["attribute_name"] != "controller_readiness":
        raise EvidenceValidationError(
            f"Derived evidence record {record['id']} uses unsupported attribute {record['attribute_name']}."
        )
    expected_value = _derive_controller_status(grouped_records[record["plant_id"]])[0]
    if record["value"] != expected_value:
        raise EvidenceValidationError(
            f"Derived evidence record {record['id']} should be {expected_value}, found {record['value']}."
        )


def load_evidence_records(path: str | Path = EVIDENCE_RECORDS_PATH) -> list[dict[str, Any]]:
    payload = _load_json(path)
    _validate_schema(
        payload,
        schema_path=EVIDENCE_RECORDS_SCHEMA_PATH,
        data_path=path,
    )

    plant_facts = _load_catalog(PLANT_FACTS_PATH)
    unresolved_species = _load_catalog(UNRESOLVED_SPECIES_PATH)
    controller_profiles = _load_json(CONTROLLER_PROFILES_PATH)
    replay_index = _load_replay_index()
    indexed_records = _records_by_id(payload)
    grouped_records = _group_records_by_plant(payload)

    for record in payload:
        evidence_class = record["evidence_class"]
        if evidence_class == "legacy_migrated_value":
            _validate_legacy_record(
                record,
                plant_facts=plant_facts,
                unresolved_species=unresolved_species,
            )
        elif evidence_class == "controller_profile_value":
            _validate_controller_profile_record(
                record,
                controller_profiles=controller_profiles,
            )
        elif evidence_class == "replay_derived_observation":
            _validate_replay_record(
                record,
                replay_index=replay_index,
            )
        elif evidence_class == "derived_inference":
            _validate_derived_record(
                record,
                indexed_records=indexed_records,
                grouped_records=grouped_records,
            )
        else:
            raise EvidenceValidationError(
                f"Unsupported evidence_class {evidence_class} in record {record['id']}."
            )

    return payload


def _make_attribute_entry(
    *,
    value: Any,
    evidence_ids: list[str],
    status: str,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "value": value,
        "evidence_ids": evidence_ids,
        "blocked_reasons": blocked_reasons or [],
    }


def build_plant_attributes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_records = _group_records_by_plant(records)
    plants: list[dict[str, Any]] = []

    for plant_id in sorted(grouped_records):
        plant_records = grouped_records[plant_id]
        controller_status, block_reasons = _derive_controller_status(plant_records)
        attributes: dict[str, dict[str, Any]] = {}

        for attribute_name in sorted(plant_records):
            record = plant_records[attribute_name]
            is_blocked_readiness = (
                attribute_name == "controller_readiness" and record["value"] == "blocked"
            )
            attributes[attribute_name] = _make_attribute_entry(
                value=record["value"],
                evidence_ids=[record["id"]],
                status="blocked" if is_blocked_readiness else "supported",
                blocked_reasons=block_reasons if is_blocked_readiness else [],
            )

        if controller_status == "controller_ready":
            evidence_ids = [
                record_id
                for record_id in (
                    plant_records.get("migration_status", {}).get("id"),
                    plant_records.get("controller_family", {}).get("id"),
                    plant_records.get("controller_autowater_enabled", {}).get("id"),
                    plant_records.get("controller_readiness", {}).get("id"),
                )
                if record_id is not None
            ]
            attributes["autowater_controller_access"] = _make_attribute_entry(
                value=True,
                evidence_ids=evidence_ids,
                status="supported",
            )
        elif "unresolved_reasons" in plant_records:
            attributes["controller_family_assignment"] = _make_attribute_entry(
                value=None,
                evidence_ids=[plant_records["unresolved_reasons"]["id"]],
                status="blocked",
                blocked_reasons=block_reasons,
            )
        else:
            evidence_ids = [
                record_id
                for record_id in (
                    plant_records.get("migration_status", {}).get("id"),
                    plant_records.get("manual_review_reasons", {}).get("id"),
                    plant_records.get("controller_autowater_enabled", {}).get("id"),
                    plant_records.get("controller_readiness", {}).get("id"),
                )
                if record_id is not None
            ]
            attributes["autowater_controller_access"] = _make_attribute_entry(
                value=False,
                evidence_ids=evidence_ids,
                status="blocked",
                blocked_reasons=block_reasons,
            )

        ordered_attributes = {
            attribute_name: attributes[attribute_name] for attribute_name in sorted(attributes)
        }
        plants.append(
            {
                "plant_id": plant_id,
                "evidence_record_count": len(plant_records),
                "controller_status": controller_status,
                "controller_block_reasons": block_reasons,
                "attributes": ordered_attributes,
            }
        )

    return plants


def build_evidence_summary(
    records: list[dict[str, Any]],
    plant_attributes: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_class_counts: dict[str, int] = {}
    for record in records:
        evidence_class = record["evidence_class"]
        evidence_class_counts[evidence_class] = evidence_class_counts.get(evidence_class, 0) + 1

    controller_status_counts = {"controller_ready": 0, "blocked": 0}
    attribute_status_counts = {"supported": 0, "blocked": 0}
    plants_summary: dict[str, Any] = {}

    for plant in plant_attributes:
        controller_status_counts[plant["controller_status"]] += 1
        supported_count = 0
        blocked_count = 0
        for attribute in plant["attributes"].values():
            attribute_status_counts[attribute["status"]] += 1
            if attribute["status"] == "supported":
                supported_count += 1
            else:
                blocked_count += 1
        plants_summary[plant["plant_id"]] = {
            "evidence_record_count": plant["evidence_record_count"],
            "controller_status": plant["controller_status"],
            "supported_attribute_count": supported_count,
            "blocked_attribute_count": blocked_count,
        }

    return {
        "evidence_record_count": len(records),
        "plant_count": len(plant_attributes),
        "evidence_class_counts": dict(sorted(evidence_class_counts.items())),
        "controller_status_counts": controller_status_counts,
        "attribute_status_counts": attribute_status_counts,
        "plant_summary": dict(sorted(plants_summary.items())),
    }


def rebuild_outputs(
    evidence_path: str | Path = EVIDENCE_RECORDS_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    records = load_evidence_records(evidence_path)
    plant_attributes = build_plant_attributes(records)
    summary = build_evidence_summary(records, plant_attributes)
    return records, plant_attributes, summary


def write_outputs(
    evidence_path: str | Path = EVIDENCE_RECORDS_PATH,
    *,
    plant_attributes_path: str | Path = PLANT_ATTRIBUTES_PATH,
    summary_path: str | Path = EVIDENCE_COVERAGE_SUMMARY_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    records, plant_attributes, summary = rebuild_outputs(evidence_path)
    Path(plant_attributes_path).write_text(json.dumps(plant_attributes, indent=2) + "\n")
    Path(summary_path).write_text(json.dumps(summary, indent=2) + "\n")
    return records, plant_attributes, summary


def validate_evidence_bundle(
    evidence_path: str | Path = EVIDENCE_RECORDS_PATH,
    *,
    plant_attributes_path: str | Path = PLANT_ATTRIBUTES_PATH,
    summary_path: str | Path = EVIDENCE_COVERAGE_SUMMARY_PATH,
    check_generated_outputs: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    records, rebuilt_attributes, rebuilt_summary = rebuild_outputs(evidence_path)
    _validate_schema(
        rebuilt_attributes,
        schema_path=PLANT_ATTRIBUTES_SCHEMA_PATH,
        data_path="rebuilt plant_attributes",
    )

    if check_generated_outputs:
        on_disk_attributes = _load_json(plant_attributes_path)
        _validate_schema(
            on_disk_attributes,
            schema_path=PLANT_ATTRIBUTES_SCHEMA_PATH,
            data_path=plant_attributes_path,
        )
        if on_disk_attributes != rebuilt_attributes:
            raise EvidenceValidationError(
                f"{plant_attributes_path} is out of date; run `python plant_evidence.py rebuild`."
            )

        on_disk_summary = _load_json(summary_path)
        if on_disk_summary != rebuilt_summary:
            raise EvidenceValidationError(
                f"{summary_path} is out of date; run `python plant_evidence.py rebuild`."
            )

    return records, rebuilt_attributes, rebuilt_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize plant evidence.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("validate", help="Validate evidence and checked-in derived outputs.")
    subparsers.add_parser("rebuild", help="Rebuild plant_attributes.json and summary output.")
    subparsers.add_parser("summary", help="Print the rebuilt evidence summary JSON.")

    args = parser.parse_args(argv)
    command = args.command or "validate"

    if command == "validate":
        records, plant_attributes, summary = validate_evidence_bundle()
        print(
            json.dumps(
                {
                    "status": "ok",
                    "evidence_record_count": len(records),
                    "plant_count": len(plant_attributes),
                    "controller_status_counts": summary["controller_status_counts"],
                },
                indent=2,
            )
        )
        return 0

    if command == "rebuild":
        records, plant_attributes, summary = write_outputs()
        print(
            json.dumps(
                {
                    "status": "rebuilt",
                    "evidence_record_count": len(records),
                    "plant_count": len(plant_attributes),
                    "summary_path": str(EVIDENCE_COVERAGE_SUMMARY_PATH.name),
                },
                indent=2,
            )
        )
        return 0

    if command == "summary":
        _, _, summary = rebuild_outputs()
        print(json.dumps(summary, indent=2))
        return 0

    raise EvidenceValidationError(f"Unsupported command {command}.")


if __name__ == "__main__":
    raise SystemExit(main())
