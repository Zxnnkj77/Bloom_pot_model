from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from bloom_controller import BASE_DIR


PLANT_FACTS_PATH = BASE_DIR / "plant_facts.json"
PLANT_FACTS_SCHEMA_PATH = BASE_DIR / "plant_facts.schema.json"
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
UNRESOLVED_SPECIES_SCHEMA_PATH = BASE_DIR / "unresolved_species.schema.json"
EVIDENCE_RECORDS_PATH = BASE_DIR / "evidence_records.json"
EVIDENCE_RECORDS_SCHEMA_PATH = BASE_DIR / "evidence_records.schema.json"
PLANT_ATTRIBUTES_PATH = BASE_DIR / "plant_attributes.json"
PLANT_ATTRIBUTES_SCHEMA_PATH = BASE_DIR / "plant_attributes.schema.json"
DEFAULT_DERIVATION_METHOD = "latest_record_wins"


class EvidenceValidationError(ValueError):
    """Raised when the evidence ingestion model is malformed."""


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def _write_json(path: str | Path, payload: Any) -> None:
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def _validate_schema(payload: Any, *, schema_path: str | Path, data_path: str | Path) -> None:
    schema = _load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    first_error = errors[0]
    location = ".".join(str(part) for part in first_error.absolute_path) or "<root>"
    raise EvidenceValidationError(
        f"Schema validation failed for {data_path} at {location}: {first_error.message}"
    )


def _parse_timestamp(
    value: str | None,
    *,
    field_name: str,
    allow_none: bool = False,
) -> datetime | None:
    if value is None:
        if allow_none:
            return None
        raise EvidenceValidationError(f"{field_name} is required.")
    if not isinstance(value, str):
        raise EvidenceValidationError(f"{field_name} must be an ISO 8601 timestamp string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceValidationError(f"{field_name} must be a valid ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceValidationError(f"{field_name} must include timezone information.")
    return parsed


def _load_catalog_ids(
    path: str | Path,
    *,
    schema_path: str | Path,
    label: str,
) -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    _validate_schema(payload, schema_path=schema_path, data_path=path)

    records: dict[str, dict[str, Any]] = {}
    for record in payload:
        record_id = record["id"]
        if record_id in records:
            raise EvidenceValidationError(f"Duplicate {label} id found in {path}: {record_id}")
        records[record_id] = record
    return records


def _load_plant_facts(path: str | Path = PLANT_FACTS_PATH) -> dict[str, dict[str, Any]]:
    return _load_catalog_ids(
        path,
        schema_path=PLANT_FACTS_SCHEMA_PATH,
        label="plant fact",
    )


def _load_unresolved_species(
    path: str | Path = UNRESOLVED_SPECIES_PATH,
) -> dict[str, dict[str, Any]]:
    return _load_catalog_ids(
        path,
        schema_path=UNRESOLVED_SPECIES_SCHEMA_PATH,
        label="unresolved species",
    )


def _require_known_plant_id(
    plant_id: str,
    *,
    plant_facts: dict[str, dict[str, Any]],
    unresolved_species: dict[str, dict[str, Any]],
    data_path: str | Path,
    field_name: str,
) -> None:
    if plant_id in plant_facts or plant_id in unresolved_species:
        return
    raise EvidenceValidationError(
        f"{field_name} in {data_path} references unknown plant id: {plant_id}"
    )


def _validate_evidence_record(
    record: dict[str, Any],
    *,
    plant_facts: dict[str, dict[str, Any]],
    unresolved_species: dict[str, dict[str, Any]],
    data_path: str | Path,
    record_index: int | None = None,
) -> None:
    record_label = (
        f"{data_path}[{record_index}]"
        if record_index is not None
        else f"{data_path}:{record.get('id', '<unknown>')}"
    )
    _require_known_plant_id(
        record["plant_id"],
        plant_facts=plant_facts,
        unresolved_species=unresolved_species,
        data_path=data_path,
        field_name=f"{record_label}.plant_id",
    )
    observed_at = _parse_timestamp(
        record.get("observed_at"),
        field_name=f"{record_label}.observed_at",
        allow_none=True,
    )
    recorded_at = _parse_timestamp(
        record["recorded_at"],
        field_name=f"{record_label}.recorded_at",
    )
    if observed_at is not None and observed_at > recorded_at:
        raise EvidenceValidationError(
            f"{record_label}.observed_at cannot be later than recorded_at."
        )


def _record_effective_sort_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    observed_at = record.get("observed_at") or ""
    return (
        observed_at,
        record["recorded_at"],
        record["plant_id"],
        record["id"],
    )


def sort_evidence_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (
            record["plant_id"],
            record["attribute_name"],
            *_record_effective_sort_key(record),
        ),
    )


def load_evidence_records(
    path: str | Path = EVIDENCE_RECORDS_PATH,
    *,
    plant_facts_path: str | Path = PLANT_FACTS_PATH,
    unresolved_species_path: str | Path = UNRESOLVED_SPECIES_PATH,
) -> list[dict[str, Any]]:
    payload = _load_json(path)
    _validate_schema(payload, schema_path=EVIDENCE_RECORDS_SCHEMA_PATH, data_path=path)

    plant_facts = _load_plant_facts(plant_facts_path)
    unresolved_species = _load_unresolved_species(unresolved_species_path)

    record_ids: set[str] = set()
    for index, record in enumerate(payload):
        record_id = record["id"]
        if record_id in record_ids:
            raise EvidenceValidationError(f"Duplicate evidence record id found in {path}: {record_id}")
        record_ids.add(record_id)
        _validate_evidence_record(
            record,
            plant_facts=plant_facts,
            unresolved_species=unresolved_species,
            data_path=path,
            record_index=index,
        )
    return sort_evidence_records(payload)


def _build_controller_blocked_reasons(
    record: dict[str, Any],
    *,
    unresolved_species: dict[str, dict[str, Any]],
) -> list[str]:
    reasons = ["not_used_by_controller_in_round_12"]
    if record["plant_id"] in unresolved_species:
        reasons.append("plant_id_unresolved")
    if record["evidence_class"] == "unresolved":
        reasons.append("attribute_value_unresolved")
    return reasons


def build_plant_attributes(
    evidence_records: list[dict[str, Any]],
    *,
    plant_facts: dict[str, dict[str, Any]] | None = None,
    unresolved_species: dict[str, dict[str, Any]] | None = None,
    derived_at: str | None = None,
) -> list[dict[str, Any]]:
    plant_facts = plant_facts or _load_plant_facts()
    unresolved_species = unresolved_species or _load_unresolved_species()

    latest_by_attribute: dict[tuple[str, str], dict[str, Any]] = {}
    for record in sort_evidence_records(evidence_records):
        _validate_evidence_record(
            record,
            plant_facts=plant_facts,
            unresolved_species=unresolved_species,
            data_path="evidence_records",
        )
        key = (record["plant_id"], record["attribute_name"])
        current = latest_by_attribute.get(key)
        if current is None or _record_effective_sort_key(record) >= _record_effective_sort_key(current):
            latest_by_attribute[key] = record

    derived_timestamp = _parse_timestamp(
        derived_at or datetime.now(timezone.utc).isoformat(),
        field_name="derived_at",
    ).isoformat()

    attributes = []
    for plant_id, attribute_name in sorted(latest_by_attribute):
        record = latest_by_attribute[(plant_id, attribute_name)]
        attributes.append(
            {
                "plant_id": plant_id,
                "attribute_name": attribute_name,
                "value": record["value"],
                "unit": record["unit"],
                "evidence_class": record["evidence_class"],
                "source_record_id": record["id"],
                "source_kind": record["source_kind"],
                "source_reference": record["source_reference"],
                "observed_at": record["observed_at"],
                "recorded_at": record["recorded_at"],
                "notes": record["notes"],
                "controller_ready": False,
                "controller_blocked_reasons": _build_controller_blocked_reasons(
                    record,
                    unresolved_species=unresolved_species,
                ),
                "provenance": {
                    "derived_from_record_id": record["id"],
                    "derived_at": derived_timestamp,
                    "derivation_method": DEFAULT_DERIVATION_METHOD,
                },
            }
        )
    return attributes


def _normalize_attribute_for_compare(attribute: dict[str, Any]) -> dict[str, Any]:
    return {
        "plant_id": attribute["plant_id"],
        "attribute_name": attribute["attribute_name"],
        "value": attribute["value"],
        "unit": attribute["unit"],
        "evidence_class": attribute["evidence_class"],
        "source_record_id": attribute["source_record_id"],
        "source_kind": attribute["source_kind"],
        "source_reference": attribute["source_reference"],
        "observed_at": attribute["observed_at"],
        "recorded_at": attribute["recorded_at"],
        "notes": attribute["notes"],
        "controller_ready": attribute["controller_ready"],
        "controller_blocked_reasons": attribute["controller_blocked_reasons"],
        "provenance": {
            "derived_from_record_id": attribute["provenance"]["derived_from_record_id"],
            "derivation_method": attribute["provenance"]["derivation_method"],
        },
    }


def load_plant_attributes(
    path: str | Path = PLANT_ATTRIBUTES_PATH,
    *,
    evidence_records: list[dict[str, Any]] | None = None,
    evidence_records_path: str | Path = EVIDENCE_RECORDS_PATH,
    plant_facts_path: str | Path = PLANT_FACTS_PATH,
    unresolved_species_path: str | Path = UNRESOLVED_SPECIES_PATH,
) -> list[dict[str, Any]]:
    payload = _load_json(path)
    _validate_schema(payload, schema_path=PLANT_ATTRIBUTES_SCHEMA_PATH, data_path=path)

    records = evidence_records or load_evidence_records(
        evidence_records_path,
        plant_facts_path=plant_facts_path,
        unresolved_species_path=unresolved_species_path,
    )
    evidence_by_id = {record["id"]: record for record in records}
    plant_facts = _load_plant_facts(plant_facts_path)
    unresolved_species = _load_unresolved_species(unresolved_species_path)

    seen_keys: set[tuple[str, str]] = set()
    for index, attribute in enumerate(payload):
        key = (attribute["plant_id"], attribute["attribute_name"])
        if key in seen_keys:
            raise EvidenceValidationError(
                f"Duplicate plant attribute entry found in {path}: {attribute['plant_id']}/{attribute['attribute_name']}"
            )
        seen_keys.add(key)
        _require_known_plant_id(
            attribute["plant_id"],
            plant_facts=plant_facts,
            unresolved_species=unresolved_species,
            data_path=path,
            field_name=f"{path}[{index}].plant_id",
        )
        source_record_id = attribute["source_record_id"]
        if source_record_id not in evidence_by_id:
            raise EvidenceValidationError(
                f"{path}[{index}] references missing evidence record id: {source_record_id}"
            )
        source_record = evidence_by_id[source_record_id]
        if attribute["plant_id"] != source_record["plant_id"]:
            raise EvidenceValidationError(
                f"{path}[{index}] plant_id does not match source evidence record {source_record_id}."
            )
        if attribute["attribute_name"] != source_record["attribute_name"]:
            raise EvidenceValidationError(
                f"{path}[{index}] attribute_name does not match source evidence record {source_record_id}."
            )
        if attribute["provenance"]["derived_from_record_id"] != source_record_id:
            raise EvidenceValidationError(
                f"{path}[{index}] provenance must point to source_record_id {source_record_id}."
            )
        if attribute["provenance"]["derivation_method"] != DEFAULT_DERIVATION_METHOD:
            raise EvidenceValidationError(
                f"{path}[{index}] must use derivation method {DEFAULT_DERIVATION_METHOD}."
            )

    canonical_attributes = build_plant_attributes(
        records,
        plant_facts=plant_facts,
        unresolved_species=unresolved_species,
        derived_at=payload[0]["provenance"]["derived_at"] if payload else None,
    )
    if [_normalize_attribute_for_compare(item) for item in payload] != [
        _normalize_attribute_for_compare(item) for item in canonical_attributes
    ]:
        raise EvidenceValidationError(
            f"{path} is out of sync with evidence_records.json; rebuild the derived attribute catalog."
        )

    return payload


def validate_evidence_store(
    *,
    plant_facts_path: str | Path = PLANT_FACTS_PATH,
    unresolved_species_path: str | Path = UNRESOLVED_SPECIES_PATH,
    evidence_records_path: str | Path = EVIDENCE_RECORDS_PATH,
    plant_attributes_path: str | Path = PLANT_ATTRIBUTES_PATH,
) -> dict[str, Any]:
    records = load_evidence_records(
        evidence_records_path,
        plant_facts_path=plant_facts_path,
        unresolved_species_path=unresolved_species_path,
    )
    attributes = load_plant_attributes(
        plant_attributes_path,
        evidence_records=records,
        evidence_records_path=evidence_records_path,
        plant_facts_path=plant_facts_path,
        unresolved_species_path=unresolved_species_path,
    )
    return {
        "plant_fact_count": len(_load_plant_facts(plant_facts_path)),
        "unresolved_species_count": len(_load_unresolved_species(unresolved_species_path)),
        "evidence_record_count": len(records),
        "plant_attribute_count": len(attributes),
    }


def rebuild_plant_attributes(
    *,
    plant_facts_path: str | Path = PLANT_FACTS_PATH,
    unresolved_species_path: str | Path = UNRESOLVED_SPECIES_PATH,
    evidence_records_path: str | Path = EVIDENCE_RECORDS_PATH,
    plant_attributes_path: str | Path = PLANT_ATTRIBUTES_PATH,
    derived_at: str | None = None,
) -> list[dict[str, Any]]:
    plant_facts = _load_plant_facts(plant_facts_path)
    unresolved_species = _load_unresolved_species(unresolved_species_path)
    records = load_evidence_records(
        evidence_records_path,
        plant_facts_path=plant_facts_path,
        unresolved_species_path=unresolved_species_path,
    )
    attributes = build_plant_attributes(
        records,
        plant_facts=plant_facts,
        unresolved_species=unresolved_species,
        derived_at=derived_at,
    )
    _write_json(plant_attributes_path, attributes)
    return attributes


def _coerce_single_record(payload: Any, *, data_path: str | Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EvidenceValidationError(
            f"Registration input {data_path} must contain exactly one JSON object."
        )
    _validate_schema([payload], schema_path=EVIDENCE_RECORDS_SCHEMA_PATH, data_path=data_path)
    return payload


def register_evidence_record(
    record_payload: dict[str, Any] | str | Path,
    *,
    plant_facts_path: str | Path = PLANT_FACTS_PATH,
    unresolved_species_path: str | Path = UNRESOLVED_SPECIES_PATH,
    evidence_records_path: str | Path = EVIDENCE_RECORDS_PATH,
    plant_attributes_path: str | Path = PLANT_ATTRIBUTES_PATH,
    derived_at: str | None = None,
) -> dict[str, Any]:
    incoming_payload = (
        _load_json(record_payload)
        if isinstance(record_payload, (str, Path))
        else record_payload
    )
    new_record = _coerce_single_record(
        incoming_payload,
        data_path=record_payload if isinstance(record_payload, (str, Path)) else "<memory>",
    )
    plant_facts = _load_plant_facts(plant_facts_path)
    unresolved_species = _load_unresolved_species(unresolved_species_path)

    current_records = []
    evidence_path = Path(evidence_records_path)
    if evidence_path.exists():
        current_records = load_evidence_records(
            evidence_records_path,
            plant_facts_path=plant_facts_path,
            unresolved_species_path=unresolved_species_path,
        )
    if any(record["id"] == new_record["id"] for record in current_records):
        raise EvidenceValidationError(
            f"Evidence record id already exists in {evidence_records_path}: {new_record['id']}"
        )

    _validate_evidence_record(
        new_record,
        plant_facts=plant_facts,
        unresolved_species=unresolved_species,
        data_path=record_payload if isinstance(record_payload, (str, Path)) else "<memory>",
    )
    all_records = sort_evidence_records([*current_records, new_record])
    _write_json(evidence_records_path, all_records)

    attributes = build_plant_attributes(
        all_records,
        plant_facts=plant_facts,
        unresolved_species=unresolved_species,
        derived_at=derived_at,
    )
    _write_json(plant_attributes_path, attributes)

    validation_summary = validate_evidence_store(
        plant_facts_path=plant_facts_path,
        unresolved_species_path=unresolved_species_path,
        evidence_records_path=evidence_records_path,
        plant_attributes_path=plant_attributes_path,
    )
    return {
        "registered_record_id": new_record["id"],
        "updated_attribute_key": f"{new_record['plant_id']}/{new_record['attribute_name']}",
        **validation_summary,
    }


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and register plant evidence records.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate evidence files.")
    validate_parser.add_argument(
        "--evidence-records",
        type=Path,
        default=EVIDENCE_RECORDS_PATH,
        help="Path to evidence_records.json.",
    )
    validate_parser.add_argument(
        "--plant-attributes",
        type=Path,
        default=PLANT_ATTRIBUTES_PATH,
        help="Path to plant_attributes.json.",
    )

    register_parser = subparsers.add_parser(
        "register",
        help="Validate and append one evidence record, then rebuild plant_attributes.json.",
    )
    register_parser.add_argument("record_path", type=Path, help="Path to one evidence record JSON object.")
    register_parser.add_argument(
        "--evidence-records",
        type=Path,
        default=EVIDENCE_RECORDS_PATH,
        help="Path to evidence_records.json.",
    )
    register_parser.add_argument(
        "--plant-attributes",
        type=Path,
        default=PLANT_ATTRIBUTES_PATH,
        help="Path to plant_attributes.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        summary = validate_evidence_store(
            evidence_records_path=args.evidence_records,
            plant_attributes_path=args.plant_attributes,
        )
    else:
        summary = register_evidence_record(
            args.record_path,
            evidence_records_path=args.evidence_records,
            plant_attributes_path=args.plant_attributes,
        )

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
