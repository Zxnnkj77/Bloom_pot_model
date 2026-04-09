import importlib.util
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = BASE_DIR / "plant_evidence.py"
PLANT_ATTRIBUTES_PATH = BASE_DIR / "plant_attributes.json"
SUMMARY_PATH = BASE_DIR / "evidence_coverage_summary.json"

SPEC = importlib.util.spec_from_file_location("plant_evidence_module", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def load_json(path):
    return json.loads(Path(path).read_text())


def test_evidence_bundle_validates_and_matches_checked_in_outputs():
    records, plant_attributes, summary = MODULE.validate_evidence_bundle()

    assert len(records) == 22
    assert plant_attributes == load_json(PLANT_ATTRIBUTES_PATH)
    assert summary == load_json(SUMMARY_PATH)


def test_plant_attribute_rebuild_is_deterministic():
    records = MODULE.load_evidence_records()

    first = MODULE.build_plant_attributes(records)
    second = MODULE.build_plant_attributes(records)

    assert first == second


def test_evidence_summary_counts_are_correct():
    _, _, summary = MODULE.validate_evidence_bundle()

    assert summary == {
        "evidence_record_count": 22,
        "plant_count": 3,
        "evidence_class_counts": {
            "controller_profile_value": 2,
            "derived_inference": 3,
            "legacy_migrated_value": 12,
            "replay_derived_observation": 5,
        },
        "controller_status_counts": {
            "controller_ready": 1,
            "blocked": 2,
        },
        "attribute_status_counts": {
            "supported": 21,
            "blocked": 4,
        },
        "plant_summary": {
            "christmas_cactus": {
                "evidence_record_count": 5,
                "controller_status": "blocked",
                "supported_attribute_count": 4,
                "blocked_attribute_count": 2,
            },
            "moth_orchid": {
                "evidence_record_count": 9,
                "controller_status": "blocked",
                "supported_attribute_count": 8,
                "blocked_attribute_count": 2,
            },
            "peace_lily": {
                "evidence_record_count": 8,
                "controller_status": "controller_ready",
                "supported_attribute_count": 9,
                "blocked_attribute_count": 0,
            },
        },
    }


def test_controller_blocked_attributes_remain_blocked():
    _, plant_attributes, _ = MODULE.validate_evidence_bundle()
    attributes_by_plant = {record["plant_id"]: record for record in plant_attributes}

    peace_lily = attributes_by_plant["peace_lily"]
    assert peace_lily["controller_status"] == "controller_ready"
    assert peace_lily["attributes"]["autowater_controller_access"]["status"] == "supported"

    moth_orchid = attributes_by_plant["moth_orchid"]
    assert moth_orchid["controller_status"] == "blocked"
    assert moth_orchid["attributes"]["controller_readiness"]["status"] == "blocked"
    assert moth_orchid["attributes"]["autowater_controller_access"]["status"] == "blocked"
    assert moth_orchid["controller_block_reasons"] == [
        "legacy_category_orchid_requires_manual_review"
    ]

    christmas_cactus = attributes_by_plant["christmas_cactus"]
    assert christmas_cactus["controller_status"] == "blocked"
    assert christmas_cactus["attributes"]["controller_readiness"]["status"] == "blocked"
    assert christmas_cactus["attributes"]["controller_family_assignment"]["status"] == "blocked"
    assert christmas_cactus["controller_block_reasons"] == [
        "unsupported_legacy_category_water_preference_combination"
    ]
