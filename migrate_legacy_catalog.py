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
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
UNRESOLVED_SPECIES_SCHEMA_PATH = BASE_DIR / "unresolved_species.schema.json"
MIGRATION_REPORT_PATH = BASE_DIR / "migration_report.md"

MANUAL_REVIEW_TAG = "manual_review_required"

MAPPING_RULES = [
    (
        "category = succulent AND waterPreference in {drought_tolerant,dry_between}",
        "controller_family = succulent_fast_drain, "
        "controller_family_confidence = legacy_rule_based, "
        "migration_status = accepted_auto",
    ),
    (
        "category = fern AND waterPreference in {evenly_moist,constantly_moist}",
        "controller_family = fern_high_moisture, "
        "controller_family_confidence = legacy_rule_based, "
        "migration_status = accepted_auto",
    ),
    (
        "category in {tropical,bulb,herb,edible} AND waterPreference = evenly_moist",
        "controller_family = soil_even_moist, "
        "controller_family_confidence = legacy_rule_based, "
        "migration_status = accepted_auto",
    ),
    (
        "category in {tropical,bulb,herb,edible} AND waterPreference = dry_between",
        "controller_family = soil_dry_between, "
        "controller_family_confidence = legacy_rule_based, "
        "migration_status = accepted_auto",
    ),
    (
        "category = orchid",
        "controller_family = orchid_bark, "
        "controller_family_confidence = manual_review, "
        "migration_status = accepted_manual",
    ),
    (
        "category = carnivorous",
        "controller_family = bog_carnivorous, "
        "controller_family_confidence = manual_review, "
        "migration_status = accepted_manual",
    ),
    (
        "anything else unresolved",
        "write to unresolved_species.json with explicit reasons and no controller_family",
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


def make_provenance() -> dict[str, str]:
    return {
        "source_file": LEGACY_PATH.name,
        "source_type": "legacy_backup_record",
        "match_type": "exact_common_name_and_scientific_name",
    }


def accepted_record(
    legacy: dict[str, Any],
    *,
    controller_family: str,
    controller_family_confidence: str,
    migration_status: str,
    special_handling: list[str],
    manual_review_reasons: list[str],
) -> dict[str, Any]:
    common_name = normalize_optional_string(legacy.get("commonName")) or "Unnamed Species"
    scientific_name = normalize_optional_string(legacy.get("scientificName")) or common_name
    return {
        "id": slugify(common_name),
        "common_name": common_name,
        "scientific_name": scientific_name,
        "legacy_category": normalize_optional_string(legacy.get("category")),
        "legacy_light_preference_lux": normalize_optional_int(legacy.get("lightPreference")),
        "legacy_water_preference": normalize_optional_string(legacy.get("waterPreference")),
        "controller_family": controller_family,
        "controller_family_confidence": controller_family_confidence,
        "migration_status": migration_status,
        "special_handling": sorted(set(special_handling)),
        "manual_review_reasons": sorted(set(manual_review_reasons)),
        "provenance": make_provenance(),
    }


def unresolved_record(legacy: dict[str, Any], *, reasons: list[str]) -> dict[str, Any]:
    common_name = normalize_optional_string(legacy.get("commonName")) or "Unnamed Species"
    scientific_name = normalize_optional_string(legacy.get("scientificName")) or common_name
    return {
        "id": slugify(common_name),
        "common_name": common_name,
        "scientific_name": scientific_name,
        "legacy_category": normalize_optional_string(legacy.get("category")),
        "legacy_light_preference_lux": normalize_optional_int(legacy.get("lightPreference")),
        "legacy_water_preference": normalize_optional_string(legacy.get("waterPreference")),
        "unresolved_reasons": sorted(set(reasons)),
        "provenance": make_provenance(),
    }


def classify_legacy_record(legacy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    category = normalize_optional_string(legacy.get("category"))
    water_preference = normalize_optional_string(legacy.get("waterPreference"))

    if category == "succulent" and water_preference in {"drought_tolerant", "dry_between"}:
        return (
            "accepted",
            accepted_record(
                legacy,
                controller_family="succulent_fast_drain",
                controller_family_confidence="legacy_rule_based",
                migration_status="accepted_auto",
                special_handling=[],
                manual_review_reasons=[],
            ),
        )
    if category == "fern" and water_preference in {"evenly_moist", "constantly_moist"}:
        return (
            "accepted",
            accepted_record(
                legacy,
                controller_family="fern_high_moisture",
                controller_family_confidence="legacy_rule_based",
                migration_status="accepted_auto",
                special_handling=[],
                manual_review_reasons=[],
            ),
        )
    if category in {"tropical", "bulb", "herb", "edible"} and water_preference == "evenly_moist":
        return (
            "accepted",
            accepted_record(
                legacy,
                controller_family="soil_even_moist",
                controller_family_confidence="legacy_rule_based",
                migration_status="accepted_auto",
                special_handling=[],
                manual_review_reasons=[],
            ),
        )
    if category in {"tropical", "bulb", "herb", "edible"} and water_preference == "dry_between":
        return (
            "accepted",
            accepted_record(
                legacy,
                controller_family="soil_dry_between",
                controller_family_confidence="legacy_rule_based",
                migration_status="accepted_auto",
                special_handling=[],
                manual_review_reasons=[],
            ),
        )
    if category == "orchid":
        return (
            "accepted",
            accepted_record(
                legacy,
                controller_family="orchid_bark",
                controller_family_confidence="manual_review",
                migration_status="accepted_manual",
                special_handling=[MANUAL_REVIEW_TAG],
                manual_review_reasons=["legacy_category_orchid_requires_manual_review"],
            ),
        )
    if category == "carnivorous":
        return (
            "accepted",
            accepted_record(
                legacy,
                controller_family="bog_carnivorous",
                controller_family_confidence="manual_review",
                migration_status="accepted_manual",
                special_handling=[MANUAL_REVIEW_TAG],
                manual_review_reasons=["legacy_category_carnivorous_requires_manual_review"],
            ),
        )

    reasons: list[str] = []
    if category is None:
        reasons.append("missing_legacy_category")
    if water_preference is None:
        reasons.append("missing_legacy_water_preference")
    if not reasons:
        reasons.append("unsupported_legacy_category_water_preference_combination")
    return "unresolved", unresolved_record(legacy, reasons=reasons)


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


def validate_relationships(
    accepted: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    total_legacy_count: int,
) -> None:
    accepted_ids = [record["id"] for record in accepted]
    unresolved_ids = [record["id"] for record in unresolved]

    duplicate_accepted = [plant_id for plant_id, count in Counter(accepted_ids).items() if count > 1]
    duplicate_unresolved = [
        plant_id for plant_id, count in Counter(unresolved_ids).items() if count > 1
    ]

    if duplicate_accepted:
        raise MigrationError(f"Duplicate accepted plant ids: {duplicate_accepted}")
    if duplicate_unresolved:
        raise MigrationError(f"Duplicate unresolved plant ids: {duplicate_unresolved}")

    overlap = sorted(set(accepted_ids) & set(unresolved_ids))
    if overlap:
        raise MigrationError(f"Accepted and unresolved species overlap: {overlap}")

    if len(accepted) + len(unresolved) != total_legacy_count:
        raise MigrationError(
            "Legacy species count does not match accepted plus unresolved output counts."
        )

    missing_family = [record["id"] for record in accepted if not record.get("controller_family")]
    if missing_family:
        raise MigrationError(f"Accepted plant records missing controller_family: {missing_family}")


def build_report(
    *,
    legacy_records: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> str:
    accepted_auto = [record for record in accepted if record["migration_status"] == "accepted_auto"]
    accepted_manual = [
        record for record in accepted if record["migration_status"] == "accepted_manual"
    ]
    category_counts = Counter(
        normalize_optional_string(record.get("category")) or "unknown"
        for record in legacy_records
    )
    family_counts = Counter(record["controller_family"] for record in accepted)

    lines = [
        "# Migration Report",
        "",
        "## Summary",
        "",
        f"- Total legacy species count: {len(legacy_records)}",
        f"- accepted_auto count: {len(accepted_auto)}",
        f"- accepted_manual count: {len(accepted_manual)}",
        f"- unresolved count: {len(unresolved)}",
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
            "## Manual-Review Species",
            "",
        ]
    )
    for record in accepted_manual:
        reason_text = ", ".join(record["manual_review_reasons"])
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
        reason_text = ", ".join(record["unresolved_reasons"])
        lines.append(f"- {record['common_name']} (`{record['id']}`): {reason_text}")

    return "\n".join(lines) + "\n"


def main() -> None:
    legacy_records = json.loads(LEGACY_PATH.read_text())
    accepted: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for legacy_record in legacy_records:
        disposition, normalized_record = classify_legacy_record(legacy_record)
        if disposition == "accepted":
            accepted.append(normalized_record)
        else:
            unresolved.append(normalized_record)

    accepted.sort(key=lambda record: record["id"])
    unresolved.sort(key=lambda record: record["id"])

    validate_against_schema(accepted, PLANT_FACTS_SCHEMA_PATH)
    validate_against_schema(unresolved, UNRESOLVED_SPECIES_SCHEMA_PATH)
    validate_relationships(accepted, unresolved, len(legacy_records))

    PLANT_FACTS_PATH.write_text(json.dumps(accepted, indent=2) + "\n")
    UNRESOLVED_SPECIES_PATH.write_text(json.dumps(unresolved, indent=2) + "\n")
    MIGRATION_REPORT_PATH.write_text(
        build_report(
            legacy_records=legacy_records,
            accepted=accepted,
            unresolved=unresolved,
        )
    )


if __name__ == "__main__":
    main()
