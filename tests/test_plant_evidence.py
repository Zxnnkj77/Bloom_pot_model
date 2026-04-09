import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "evidence"
EVIDENCE_RECORDS_PATH = BASE_DIR / "evidence_records.json"
PLANT_ATTRIBUTES_PATH = BASE_DIR / "plant_attributes.json"
EVIDENCE_RECORDS_SCHEMA_PATH = BASE_DIR / "evidence_records.schema.json"
PLANT_ATTRIBUTES_SCHEMA_PATH = BASE_DIR / "plant_attributes.schema.json"

MODULE_SPEC = importlib.util.spec_from_file_location(
    "plant_evidence_for_tests",
    BASE_DIR / "plant_evidence.py",
)
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = MODULE
MODULE_SPEC.loader.exec_module(MODULE)


EvidenceValidationError = MODULE.EvidenceValidationError
build_plant_attributes = MODULE.build_plant_attributes
load_evidence_records = MODULE.load_evidence_records
load_plant_attributes = MODULE.load_plant_attributes
register_evidence_record = MODULE.register_evidence_record
validate_evidence_store = MODULE.validate_evidence_store


def load_json(path):
    return json.loads(Path(path).read_text())


def test_seeded_evidence_files_validate_and_sync():
    summary = validate_evidence_store()

    assert summary["evidence_record_count"] == 4
    assert summary["plant_attribute_count"] == 4


def test_seeded_examples_cover_requested_evidence_classes():
    attributes = load_plant_attributes()
    attribute_index = {
        (record["plant_id"], record["attribute_name"]): record for record in attributes
    }

    assert attribute_index[("peace_lily", "legacy_light_preference_lux")]["evidence_class"] == "legacy_backed"
    assert attribute_index[("peace_lily", "observed_soil_moisture")]["evidence_class"] == "experimental_observation"
    assert attribute_index[("moth_orchid", "manual_review_required")]["evidence_class"] == "inferred"

    unresolved_attribute = attribute_index[("christmas_cactus", "legacy_water_preference")]
    assert unresolved_attribute["evidence_class"] == "unresolved"
    assert unresolved_attribute["controller_ready"] is False
    assert unresolved_attribute["controller_blocked_reasons"] == [
        "not_used_by_controller_in_round_12",
        "plant_id_unresolved",
        "attribute_value_unresolved",
    ]


def test_register_workflow_appends_evidence_and_rebuilds_attributes(tmp_path):
    evidence_copy = tmp_path / "evidence_records.json"
    attributes_copy = tmp_path / "plant_attributes.json"
    shutil.copyfile(EVIDENCE_RECORDS_PATH, evidence_copy)
    shutil.copyfile(PLANT_ATTRIBUTES_PATH, attributes_copy)

    summary = register_evidence_record(
        FIXTURES_DIR / "new_observed_soil_moisture.json",
        evidence_records_path=evidence_copy,
        plant_attributes_path=attributes_copy,
        derived_at="2026-04-09T00:15:00+00:00",
    )

    evidence_records = load_json(evidence_copy)
    attributes = load_json(attributes_copy)
    updated_attribute = next(
        record
        for record in attributes
        if record["plant_id"] == "peace_lily" and record["attribute_name"] == "observed_soil_moisture"
    )

    assert summary["registered_record_id"] == "peace_lily_observed_soil_moisture_20260329_210000"
    assert summary["evidence_record_count"] == 5
    assert len(evidence_records) == 5
    assert len(attributes) == 4
    assert updated_attribute["value"] == 0.03
    assert updated_attribute["source_record_id"] == "peace_lily_observed_soil_moisture_20260329_210000"
    assert updated_attribute["observed_at"] == "2026-03-29T21:00:00+00:00"


def test_malformed_record_fails_loudly(tmp_path):
    evidence_copy = tmp_path / "evidence_records.json"
    attributes_copy = tmp_path / "plant_attributes.json"
    shutil.copyfile(EVIDENCE_RECORDS_PATH, evidence_copy)
    shutil.copyfile(PLANT_ATTRIBUTES_PATH, attributes_copy)

    with pytest.raises(EvidenceValidationError, match="Schema validation failed"):
        register_evidence_record(
            FIXTURES_DIR / "invalid_missing_provenance.json",
            evidence_records_path=evidence_copy,
            plant_attributes_path=attributes_copy,
        )


def test_derived_attribute_builder_is_deterministic():
    evidence_records = load_evidence_records()

    first = build_plant_attributes(
        evidence_records,
        derived_at="2026-04-09T00:00:00+00:00",
    )
    second = build_plant_attributes(
        evidence_records,
        derived_at="2026-04-09T00:00:00+00:00",
    )

    assert first == second


def test_schema_files_exist_for_evidence_layer():
    assert load_json(EVIDENCE_RECORDS_SCHEMA_PATH)["title"] == "Bloom Pot Evidence Records"
    assert load_json(PLANT_ATTRIBUTES_SCHEMA_PATH)["title"] == "Bloom Pot Derived Plant Attributes"
