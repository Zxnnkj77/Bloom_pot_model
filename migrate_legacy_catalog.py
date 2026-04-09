from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


BASE_DIR = Path(__file__).resolve().parent
LEGACY_PATH = BASE_DIR / "bloom_plant_schema.json.legacy-20260329-2145.bak"
PLANT_FACTS_PATH = BASE_DIR / "plant_facts.json"
PLANT_FACTS_SCHEMA_PATH = BASE_DIR / "plant_facts.schema.json"
PLANT_ATTRIBUTES_PATH = BASE_DIR / "plant_attributes.json"
PLANT_ATTRIBUTES_SCHEMA_PATH = BASE_DIR / "plant_attributes.schema.json"
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
UNRESOLVED_SPECIES_SCHEMA_PATH = BASE_DIR / "unresolved_species.schema.json"
MIGRATION_REPORT_PATH = BASE_DIR / "migration_report.md"

MANUAL_REVIEW_TAG = "manual_review_required"
EVIDENCE_LEVELS = (
    "legacy_backed",
    "experimentally_observed",
    "literature_backed",
    "inferred",
    "unresolved",
)
KNOWN_ATTRIBUTE_NAMES = (
    "legacy_category",
    "legacy_light_preference_lux",
    "legacy_water_preference",
)
KNOWN_LEGACY_SOURCE_FIELDS = {
    "legacy_category": "category",
    "legacy_light_preference_lux": "lightPreference",
    "legacy_water_preference": "waterPreference",
}
IDENTITY_SOURCE_FIELDS = {"commonName", "scientificName"}
STABLE_LEGACY_FIELDS = set(KNOWN_LEGACY_SOURCE_FIELDS.values()) | IDENTITY_SOURCE_FIELDS
MANUAL_REVIEW_REASON_BY_CATEGORY = {
    "orchid": "legacy_category_orchid_requires_manual_review",
    "carnivorous": "legacy_category_carnivorous_requires_manual_review",
}
RULE_IDS = {
    "succulent_fast_drain": "succulent_fast_drain_from_legacy_category_and_water_preference",
    "fern_high_moisture": "fern_high_moisture_from_legacy_category_and_water_preference",
    "soil_even_moist": "soil_even_moist_from_legacy_category_and_water_preference",
    "soil_dry_between": "soil_dry_between_from_legacy_category_and_water_preference",
    "orchid_bark": "orchid_manual_review_gate_from_legacy_category",
    "bog_carnivorous": "carnivorous_manual_review_gate_from_legacy_category",
    "unresolved": "unresolved_after_legacy_rule_evaluation",
}

MAPPING_RULES = [
    (
        "category = succulent AND waterPreference in {drought_tolerant,dry_between}",
        "controller_family = succulent_fast_drain, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred",
    ),
    (
        "category = fern AND waterPreference in {evenly_moist,constantly_moist}",
        "controller_family = fern_high_moisture, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred",
    ),
    (
        "category in {tropical,bulb,herb,edible} AND waterPreference = evenly_moist",
        "controller_family = soil_even_moist, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred",
    ),
    (
        "category in {tropical,bulb,herb,edible} AND waterPreference = dry_between",
        "controller_family = soil_dry_between, controller_assignment.review_status = accepted_auto, controller_assignment.evidence_level = inferred",
    ),
    (
        "category = orchid",
        "controller_family = orchid_bark, controller_assignment.review_status = accepted_manual, controller_assignment.evidence_level = inferred",
    ),
    (
        "category = carnivorous",
        "controller_family = bog_carnivorous, controller_assignment.review_status = accepted_manual, controller_assignment.evidence_level = inferred",
    ),
    (
        "anything else unresolved",
        "write to unresolved_species.json with resolution_status.evidence_level = unresolved and explicit supporting_attributes",
    ),
]


class MigrationError(ValueError):
    """Raised when the migration output violates the declared contract."""


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "unnamed_species"


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def make_identity_provenance() -> dict[str, str]:
    return {
        "source_file": LEGACY_PATH.name,
        "source_type": "legacy_backup_record",
        "match_type": "exact_common_name_and_scientific_name",
    }


def make_attribute_provenance(attribute_name: str) -> dict[str, str]:
    return {
        "source_file": LEGACY_PATH.name,
        "source_type": "legacy_backup_record",
        "source_field": KNOWN_LEGACY_SOURCE_FIELDS[attribute_name],
    }


def make_assignment_provenance(rule_id: str) -> dict[str, str]:
    provenance = make_identity_provenance()
    provenance["rule_id"] = rule_id
    return provenance


def make_resolution_provenance() -> dict[str, str]:
    provenance = make_identity_provenance()
    provenance["rule_id"] = RULE_IDS["unresolved"]
    return provenance


def _base_identity(legacy: dict[str, Any]) -> tuple[str, str, str]:
    common_name = normalize_optional_string(legacy.get("commonName")) or "Unnamed Species"
    scientific_name = normalize_optional_string(legacy.get("scientificName")) or common_name
    return slugify(common_name), common_name, scientific_name


def build_attribute_bundle(legacy: dict[str, Any], plant_id: str) -> dict[str, Any]:
    attribute_specs = [
        (
            "legacy_category",
            normalize_optional_string(legacy.get("category")),
            bool(normalize_optional_string(legacy.get("category"))),
        ),
        (
            "legacy_light_preference_lux",
            normalize_optional_int(legacy.get("lightPreference")),
            False,
        ),
        (
            "legacy_water_preference",
            normalize_optional_string(legacy.get("waterPreference")),
            bool(normalize_optional_string(legacy.get("waterPreference"))),
        ),
    ]

    return {
        "plant_id": plant_id,
        "attributes": [
            {
                "name": attribute_name,
                "value": attribute_value,
                "evidence_level": "legacy_backed",
                "used_for_controller_mapping": used_for_controller_mapping,
                "provenance": make_attribute_provenance(attribute_name),
            }
            for attribute_name, attribute_value, used_for_controller_mapping in attribute_specs
        ],
    }


def accepted_record(
    legacy: dict[str, Any],
    *,
    controller_family: str,
    review_status: str,
    derived_from_attributes: list[str],
    manual_review_reasons: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plant_id, common_name, scientific_name = _base_identity(legacy)
    special_handling = [MANUAL_REVIEW_TAG] if review_status == "accepted_manual" else []
    record = {
        "id": plant_id,
        "common_name": common_name,
        "scientific_name": scientific_name,
        "identity_provenance": make_identity_provenance(),
        "controller_family": controller_family,
        "controller_assignment": {
            "review_status": review_status,
            "evidence_level": "inferred",
            "derived_from_attributes": sorted(set(derived_from_attributes)),
            "special_handling": sorted(set(special_handling)),
            "manual_review_reasons": sorted(set(manual_review_reasons)),
            "provenance": make_assignment_provenance(RULE_IDS[controller_family]),
        },
    }
    return record, build_attribute_bundle(legacy, plant_id)


def unresolved_record(legacy: dict[str, Any], *, reasons: list[str]) -> dict[str, Any]:
    plant_id, common_name, scientific_name = _base_identity(legacy)
    attribute_bundle = build_attribute_bundle(legacy, plant_id)
    attribute_values = {
        attribute["name"]: attribute["value"] for attribute in attribute_bundle["attributes"]
    }
    derived_from_attributes: list[str] = []
    if attribute_values["legacy_category"] is not None or "missing_legacy_category" in reasons:
        derived_from_attributes.append("legacy_category")
    if (
        attribute_values["legacy_water_preference"] is not None
        or "missing_legacy_water_preference" in reasons
    ):
        derived_from_attributes.append("legacy_water_preference")
    return {
        "id": plant_id,
        "common_name": common_name,
        "scientific_name": scientific_name,
        "identity_provenance": make_identity_provenance(),
        "supporting_attributes": attribute_bundle["attributes"],
        "resolution_status": {
            "evidence_level": "unresolved",
            "unresolved_reasons": sorted(set(reasons)),
            "derived_from_attributes": derived_from_attributes,
            "provenance": make_resolution_provenance(),
        },
    }


def classify_legacy_record(legacy: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    category = normalize_optional_string(legacy.get("category"))
    water_preference = normalize_optional_string(legacy.get("waterPreference"))

    if category == "succulent" and water_preference in {"drought_tolerant", "dry_between"}:
        record, attributes = accepted_record(
            legacy,
            controller_family="succulent_fast_drain",
            review_status="accepted_auto",
            derived_from_attributes=["legacy_category", "legacy_water_preference"],
            manual_review_reasons=[],
        )
        return "accepted", record, attributes

    if category == "fern" and water_preference in {"evenly_moist", "constantly_moist"}:
        record, attributes = accepted_record(
            legacy,
            controller_family="fern_high_moisture",
            review_status="accepted_auto",
            derived_from_attributes=["legacy_category", "legacy_water_preference"],
            manual_review_reasons=[],
        )
        return "accepted", record, attributes

    if category in {"tropical", "bulb", "herb", "edible"} and water_preference == "evenly_moist":
        record, attributes = accepted_record(
            legacy,
            controller_family="soil_even_moist",
            review_status="accepted_auto",
            derived_from_attributes=["legacy_category", "legacy_water_preference"],
            manual_review_reasons=[],
        )
        return "accepted", record, attributes

    if category in {"tropical", "bulb", "herb", "edible"} and water_preference == "dry_between":
        record, attributes = accepted_record(
            legacy,
            controller_family="soil_dry_between",
            review_status="accepted_auto",
            derived_from_attributes=["legacy_category", "legacy_water_preference"],
            manual_review_reasons=[],
        )
        return "accepted", record, attributes

    if category == "orchid":
        record, attributes = accepted_record(
            legacy,
            controller_family="orchid_bark",
            review_status="accepted_manual",
            derived_from_attributes=["legacy_category"],
            manual_review_reasons=[MANUAL_REVIEW_REASON_BY_CATEGORY["orchid"]],
        )
        return "accepted", record, attributes

    if category == "carnivorous":
        record, attributes = accepted_record(
            legacy,
            controller_family="bog_carnivorous",
            review_status="accepted_manual",
            derived_from_attributes=["legacy_category"],
            manual_review_reasons=[MANUAL_REVIEW_REASON_BY_CATEGORY["carnivorous"]],
        )
        return "accepted", record, attributes

    reasons: list[str] = []
    if category is None:
        reasons.append("missing_legacy_category")
    if water_preference is None:
        reasons.append("missing_legacy_water_preference")
    if not reasons:
        reasons.append("unsupported_legacy_category_water_preference_combination")
    return "unresolved", unresolved_record(legacy, reasons=reasons), None


def validate_against_schema(payload: Any, schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text())
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise MigrationError(
            f"Schema validation failed for {schema_path.name} at {location}: {error.message}"
        )


def _attribute_names(attribute_bundle: dict[str, Any]) -> list[str]:
    return [attribute["name"] for attribute in attribute_bundle["attributes"]]


def validate_relationships(
    accepted: list[dict[str, Any]],
    attributes: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    total_legacy_count: int,
) -> None:
    accepted_ids = [record["id"] for record in accepted]
    attribute_ids = [record["plant_id"] for record in attributes]
    unresolved_ids = [record["id"] for record in unresolved]

    duplicate_accepted = [plant_id for plant_id, count in Counter(accepted_ids).items() if count > 1]
    duplicate_attributes = [
        plant_id for plant_id, count in Counter(attribute_ids).items() if count > 1
    ]
    duplicate_unresolved = [
        plant_id for plant_id, count in Counter(unresolved_ids).items() if count > 1
    ]

    if duplicate_accepted:
        raise MigrationError(f"Duplicate accepted plant ids: {duplicate_accepted}")
    if duplicate_attributes:
        raise MigrationError(f"Duplicate plant attribute ids: {duplicate_attributes}")
    if duplicate_unresolved:
        raise MigrationError(f"Duplicate unresolved plant ids: {duplicate_unresolved}")

    overlap = sorted(set(accepted_ids) & set(unresolved_ids))
    if overlap:
        raise MigrationError(f"Accepted and unresolved species overlap: {overlap}")

    if set(attribute_ids) != set(accepted_ids):
        raise MigrationError(
            "Accepted plant ids do not match plant attribute ids after migration."
        )

    if len(accepted) + len(unresolved) != total_legacy_count:
        raise MigrationError(
            "Legacy species count does not match accepted plus unresolved output counts."
        )

    missing_family = [record["id"] for record in accepted if not record.get("controller_family")]
    if missing_family:
        raise MigrationError(f"Accepted plant records missing controller_family: {missing_family}")

    for attribute_bundle in attributes:
        attribute_names = _attribute_names(attribute_bundle)
        if len(attribute_names) != len(set(attribute_names)):
            raise MigrationError(
                f"Plant attribute bundle {attribute_bundle['plant_id']} has duplicate attribute names."
            )

    for record in unresolved:
        attribute_names = [attribute["name"] for attribute in record["supporting_attributes"]]
        if len(attribute_names) != len(set(attribute_names)):
            raise MigrationError(
                f"Unresolved record {record['id']} has duplicate supporting attribute names."
            )


def _quarantined_legacy_fields(legacy_records: list[dict[str, Any]]) -> list[str]:
    observed_fields: set[str] = set()
    for legacy_record in legacy_records:
        observed_fields.update(legacy_record)
    return sorted(observed_fields - STABLE_LEGACY_FIELDS)


def build_report(
    *,
    legacy_records: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    attributes: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> str:
    accepted_auto = [
        record
        for record in accepted
        if record["controller_assignment"]["review_status"] == "accepted_auto"
    ]
    accepted_manual = [
        record
        for record in accepted
        if record["controller_assignment"]["review_status"] == "accepted_manual"
    ]
    category_counts = Counter(
        normalize_optional_string(record.get("category")) or "unknown"
        for record in legacy_records
    )
    family_counts = Counter(record["controller_family"] for record in accepted)
    evidence_counts = Counter(
        attribute["evidence_level"]
        for bundle in attributes
        for attribute in bundle["attributes"]
    )
    quarantined_fields = _quarantined_legacy_fields(legacy_records)

    lines = [
        "# Migration Report",
        "",
        "## Summary",
        "",
        f"- Total legacy species count: {len(legacy_records)}",
        f"- accepted_auto count: {len(accepted_auto)}",
        f"- accepted_manual count: {len(accepted_manual)}",
        f"- unresolved count: {len(unresolved)}",
        f"- accepted plant attribute bundles: {len(attributes)}",
        f"- accepted attribute evidence records: {sum(len(bundle['attributes']) for bundle in attributes)}",
        "",
        "## Counts By Legacy Category",
        "",
    ]

    for category, count in sorted(category_counts.items()):
        lines.append(f"- {category}: {count}")

    lines.extend(
        [
            "",
            "## Counts By Controller Family",
            "",
        ]
    )
    for family, count in sorted(family_counts.items()):
        lines.append(f"- {family}: {count}")

    lines.extend(
        [
            "",
            "## Evidence Levels Used",
            "",
        ]
    )
    for evidence_level in EVIDENCE_LEVELS:
        if evidence_level in evidence_counts:
            lines.append(f"- {evidence_level}: {evidence_counts[evidence_level]}")

    lines.extend(
        [
            "",
            "## Quarantined Legacy Fields",
            "",
            "- The following legacy fields remain only in the backup and are not loaded into the active controller data model because this round does not claim biological validity for them:",
        ]
    )
    for field_name in quarantined_fields:
        lines.append(f"- `{field_name}`")

    lines.extend(
        [
            "",
            "## Manual-Review Species",
            "",
        ]
    )
    for record in accepted_manual:
        assignment = record["controller_assignment"]
        reason_text = ", ".join(assignment["manual_review_reasons"])
        lines.append(
            f"- {record['common_name']} (`{record['id']}`): "
            f"{record['controller_family']} [{reason_text}]"
        )

    lines.extend(
        [
            "",
            "## Mapping Rules Used",
            "",
        ]
    )
    for condition, outcome in MAPPING_RULES:
        lines.append(f"- `{condition}` -> `{outcome}`")

    lines.extend(
        [
            "",
            "## Unresolved Species",
            "",
        ]
    )
    for record in unresolved:
        reason_text = ", ".join(record["resolution_status"]["unresolved_reasons"])
        lines.append(f"- {record['common_name']} (`{record['id']}`): {reason_text}")

    return "\n".join(lines) + "\n"


def main() -> None:
    legacy_records = json.loads(LEGACY_PATH.read_text())
    accepted: list[dict[str, Any]] = []
    attributes: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for legacy_record in legacy_records:
        disposition, normalized_record, attribute_bundle = classify_legacy_record(legacy_record)
        if disposition == "accepted":
            accepted.append(normalized_record)
            assert attribute_bundle is not None
            attributes.append(attribute_bundle)
        else:
            unresolved.append(normalized_record)

    accepted.sort(key=lambda record: record["id"])
    attributes.sort(key=lambda record: record["plant_id"])
    unresolved.sort(key=lambda record: record["id"])

    validate_against_schema(accepted, PLANT_FACTS_SCHEMA_PATH)
    validate_against_schema(attributes, PLANT_ATTRIBUTES_SCHEMA_PATH)
    validate_against_schema(unresolved, UNRESOLVED_SPECIES_SCHEMA_PATH)
    validate_relationships(accepted, attributes, unresolved, len(legacy_records))

    PLANT_FACTS_PATH.write_text(json.dumps(accepted, indent=2) + "\n")
    PLANT_ATTRIBUTES_PATH.write_text(json.dumps(attributes, indent=2) + "\n")
    UNRESOLVED_SPECIES_PATH.write_text(json.dumps(unresolved, indent=2) + "\n")
    MIGRATION_REPORT_PATH.write_text(
        build_report(
            legacy_records=legacy_records,
            accepted=accepted,
            attributes=attributes,
            unresolved=unresolved,
        )
    )


if __name__ == "__main__":
    main()
