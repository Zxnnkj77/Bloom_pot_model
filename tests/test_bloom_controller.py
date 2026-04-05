import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


BASE_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = BASE_DIR / "bloom_controller.py"
PLANT_FACTS_PATH = BASE_DIR / "plant_facts.json"
PLANT_FACTS_SCHEMA_PATH = BASE_DIR / "plant_facts.schema.json"
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
UNRESOLVED_SPECIES_SCHEMA_PATH = BASE_DIR / "unresolved_species.schema.json"
LEGACY_PATH = BASE_DIR / "bloom_plant_schema.json.legacy-20260329-2145.bak"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

SPEC = importlib.util.spec_from_file_location("bloom_model_for_plants", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


BloomPotController = MODULE.BloomPotController
ControllerState = MODULE.ControllerState
ModelValidationError = MODULE.ModelValidationError


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_json(path):
    return json.loads(path.read_text())


def load_fixture(name):
    return load_json(FIXTURES_DIR / name)


def validate_schema(payload, schema_path):
    schema = load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    return errors


def build_plant_record(**overrides):
    record = {
        "id": "test_plant",
        "common_name": "Test Plant",
        "scientific_name": "Testus plantus",
        "legacy_category": "tropical",
        "legacy_light_preference_lux": 1000,
        "legacy_water_preference": "evenly_moist",
        "controller_family": "soil_even_moist",
        "controller_family_confidence": "legacy_rule_based",
        "migration_status": "accepted_auto",
        "special_handling": [],
        "manual_review_reasons": [],
        "provenance": {
            "source_file": "legacy.json",
            "source_type": "legacy_backup_record",
            "match_type": "exact_common_name_and_scientific_name",
        },
    }
    record.update(overrides)
    return record


def build_unresolved_record(**overrides):
    record = {
        "id": "unresolved_plant",
        "common_name": "Unresolved Plant",
        "scientific_name": "Unresolved plantus",
        "legacy_category": "succulent",
        "legacy_light_preference_lux": 1000,
        "legacy_water_preference": "evenly_moist",
        "unresolved_reasons": [
            "unsupported_legacy_category_water_preference_combination"
        ],
        "provenance": {
            "source_file": "legacy.json",
            "source_type": "legacy_backup_record",
            "match_type": "exact_common_name_and_scientific_name",
        },
    }
    record.update(overrides)
    return record


def build_controller_profile(**overrides):
    profile = {
        "moisture_target": {"minimum": 0.18, "maximum": 0.28},
        "hard_dry_cutoff": 0.05,
        "hard_wet_cutoff": 0.38,
        "watering_dose_ml": 60,
        "cooldown_minutes": 360,
        "confirm_low_readings": 1,
        "max_daily_dose_ml": 240,
        "sensor_model": "capacitive_soil_v1",
        "substrate_type": "potting_mix",
        "autowater_enabled": True,
        "manual_review_reasons": [],
    }
    profile.update(overrides)
    return profile


def make_controller(
    tmp_path,
    *,
    plant_records=None,
    controller_profiles=None,
    unresolved_species=None,
    default_reservoir_ml=200.0,
):
    plant_path = tmp_path / "plant_facts.json"
    controller_path = tmp_path / "controller_profiles.json"
    unresolved_path = tmp_path / "unresolved_species.json"
    write_json(plant_path, plant_records or [build_plant_record()])
    write_json(
        controller_path,
        controller_profiles or {"soil_even_moist": build_controller_profile()},
    )
    write_json(unresolved_path, unresolved_species or [])
    return BloomPotController(
        plant_facts_path=plant_path,
        controller_profiles_path=controller_path,
        unresolved_species_path=unresolved_path,
        default_reservoir_ml=default_reservoir_ml,
    )


def test_state_persistence(tmp_path):
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    state_path = tmp_path / "controller_state.json"
    controller.save_state(state, state_path)
    restored = controller.load_state(state_path)

    assert result["pump_on"] is True
    assert restored.to_dict() == state.to_dict()
    assert restored.last_watered_at == "2026-03-29T08:00:00+00:00"


def test_dry_trigger():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is True
    assert result["dose_ml"] == 60.0
    assert "hard dry cutoff" in result["reason"]


def test_hard_dry_cutoff_takes_precedence_over_confirm_low_readings():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    result = controller.step(
        "golden_pothos",
        0.05,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is True
    assert result["dose_ml"] == 50.0
    assert "hard dry cutoff" in result["reason"]
    assert state.low_reading_count == 0


@pytest.mark.parametrize(
    ("soil_moisture", "expected_reason"),
    [
        (0.18, "inside target band"),
        (0.28, "inside target band"),
        (0.38, "wet cutoff"),
    ],
)
def test_cutoff_boundaries(soil_moisture, expected_reason):
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    result = controller.step(
        "peace_lily",
        soil_moisture,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is False
    assert result["dose_ml"] == 0.0
    assert expected_reason in result["reason"]


def test_reservoir_exactly_equal_to_dose_waters():
    controller = BloomPotController(default_reservoir_ml=60.0)
    state = controller.initialize_state(reservoir_ml=60.0)

    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is True
    assert state.reservoir_ml == 0.0


def test_cooldown_behavior():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    first = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )
    second = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T09:00:00+00:00",
        state=state,
    )

    assert first["pump_on"] is True
    assert second["pump_on"] is False
    assert "Cooldown active" in second["reason"]


def test_cooldown_exact_boundary_allows_watering():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )
    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T14:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is True
    assert "Cooldown active" not in result["reason"]


@pytest.mark.parametrize(
    ("state_payload", "timestamp", "expected_reason"),
    [
        (
            {"reservoir_ml": 40.0, "low_reading_count": 1},
            "2026-03-29T08:00:00+00:00",
            "Reservoir does not contain the fixed watering dose",
        ),
        (
            {
                "reservoir_ml": 200.0,
                "low_reading_count": 1,
                "last_watered_at": "2026-03-29T07:30:00+00:00",
            },
            "2026-03-29T08:00:00+00:00",
            "Cooldown active",
        ),
        (
            {
                "reservoir_ml": 200.0,
                "low_reading_count": 1,
                "daily_dose_ml": 150.0,
                "daily_dose_day": "2026-03-29",
            },
            "2026-03-29T08:00:00+00:00",
            "Max daily fixed-dose budget reached",
        ),
    ],
)
def test_blocked_low_readings_do_not_advance_confirmation_count(
    state_payload,
    timestamp,
    expected_reason,
):
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = ControllerState.from_dict(state_payload, default_reservoir_ml=200.0)

    result = controller.step(
        "golden_pothos",
        0.1,
        timestamp=timestamp,
        state=state,
    )

    assert result["pump_on"] is False
    assert expected_reason in result["reason"]
    assert state.low_reading_count == 1


def test_confirm_low_readings_behavior():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    first = controller.step(
        "golden_pothos",
        0.1,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )
    second = controller.step(
        "golden_pothos",
        0.1,
        timestamp="2026-03-29T21:00:00+00:00",
        state=state,
    )

    assert first["pump_on"] is False
    assert "waiting for 2 confirmations" in first["reason"]
    assert second["pump_on"] is True
    assert "Confirmed 2 consecutive low readings" in second["reason"]


def test_low_reading_count_resets_on_non_low_reading():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    first = controller.step(
        "golden_pothos",
        0.1,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )
    middle = controller.step(
        "golden_pothos",
        0.18,
        timestamp="2026-03-29T12:00:00+00:00",
        state=state,
    )
    third = controller.step(
        "golden_pothos",
        0.1,
        timestamp="2026-03-29T21:00:00+00:00",
        state=state,
    )

    assert first["pump_on"] is False
    assert middle["pump_on"] is False
    assert "inside target band" in middle["reason"]
    assert state.low_reading_count == 1
    assert third["pump_on"] is False
    assert "waiting for 2 confirmations" in third["reason"]


def test_hard_wet_cutoff_resets_low_reading_count():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = controller.initialize_state(reservoir_ml=200.0)

    controller.step(
        "golden_pothos",
        0.1,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )
    result = controller.step(
        "golden_pothos",
        0.3,
        timestamp="2026-03-29T12:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is False
    assert "wet cutoff" in result["reason"]
    assert state.low_reading_count == 0


def test_daily_budget_blocks_same_day_watering():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = ControllerState(
        reservoir_ml=200.0,
        daily_dose_ml=240.0,
        daily_dose_day="2026-03-29",
    )

    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is False
    assert "Max daily fixed-dose budget reached" in result["reason"]


def test_daily_rollover_resets_budget():
    controller = BloomPotController(default_reservoir_ml=200.0)
    state = ControllerState(
        reservoir_ml=200.0,
        daily_dose_ml=240.0,
        daily_dose_day="2026-03-28",
    )

    result = controller.step(
        "peace_lily",
        0.03,
        timestamp="2026-03-29T08:00:00+00:00",
        state=state,
    )

    assert result["pump_on"] is True
    assert state.daily_dose_day == "2026-03-29"
    assert state.daily_dose_ml == 60.0


def test_simulate_scenario_returns_deterministic_decision_trace():
    controller = BloomPotController(default_reservoir_ml=240.0)
    fixture = load_fixture("peace_lily_full_day.json")

    scenario = controller.simulate_scenario(
        fixture["plant_id"],
        fixture["observations"],
        initial_state=fixture["initial_state"],
    )

    trace = scenario["trace"]
    assert [step["pump_on"] for step in trace] == [False, True, False, True, False, True, False]
    assert [step["decision_code"] for step in trace] == [
        "wet_cutoff_block",
        "hard_dry_approved",
        "cooldown_block",
        "confirmed_low_approved",
        "wet_cutoff_block",
        "hard_dry_approved",
        "cooldown_block",
    ]
    assert [step["reservoir_ml_after"] for step in trace] == [240.0, 180.0, 180.0, 120.0, 120.0, 60.0, 60.0]
    assert [step["daily_dose_ml_after"] for step in trace] == [0.0, 60.0, 60.0, 120.0, 120.0, 180.0, 180.0]
    assert [step["low_reading_count_after"] for step in trace] == [0, 0, 0, 0, 0, 0, 0]
    assert scenario["final_state"] == {
        "reservoir_ml": 60.0,
        "low_reading_count": 0,
        "last_watered_at": "2026-03-29T21:00:00+00:00",
        "daily_dose_ml": 180.0,
        "daily_dose_day": "2026-03-29",
    }


def test_simulate_scenario_cooldown_blocked_low_reading_does_not_advance_confirmation_count():
    controller = BloomPotController(default_reservoir_ml=200.0)

    scenario = controller.simulate_scenario(
        "golden_pothos",
        [
            {"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.04},
            {"timestamp": "2026-03-29T09:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-29T21:00:00+00:00", "soil_moisture": 0.10},
            {"timestamp": "2026-03-30T10:00:00+00:00", "soil_moisture": 0.10},
        ],
        initial_reservoir_ml=200.0,
    )

    trace = scenario["trace"]
    assert [step["pump_on"] for step in trace] == [True, False, False, True]
    assert trace[1]["low_reading_count_before"] == 0
    assert trace[1]["low_reading_count_after"] == 0
    assert trace[1]["decision_code"] == "cooldown_block"
    assert trace[2]["low_reading_count_before"] == 0
    assert trace[2]["low_reading_count_after"] == 1
    assert trace[2]["decision_code"] == "confirmation_wait"
    assert trace[3]["low_reading_count_before"] == 1
    assert trace[3]["decision_code"] == "confirmed_low_approved"


def test_simulate_scenario_rejects_unresolved_species_ids_explicitly():
    controller = BloomPotController(default_reservoir_ml=200.0)

    with pytest.raises(KeyError, match="unresolved and not loadable"):
        controller.simulate_scenario(
            "christmas_cactus",
            [{"timestamp": "2026-03-29T08:00:00+00:00", "soil_moisture": 0.1}],
            initial_reservoir_ml=200.0,
        )


def test_manual_no_autowater_families():
    controller = BloomPotController(default_reservoir_ml=200.0)

    for plant_id in ("moth_orchid", "venus_fly_trap"):
        state = controller.initialize_state(reservoir_ml=200.0)
        state.low_reading_count = 3
        result = controller.step(
            plant_id,
            0.01,
            timestamp="2026-03-29T08:00:00+00:00",
            state=state,
        )

        assert result["pump_on"] is False
        assert result["dose_ml"] == 0.0
        assert "manual review" in result["reason"]
        assert state.low_reading_count == 0


@pytest.mark.parametrize(
    "bad_timestamp",
    [
        "not-a-timestamp",
        "2026-03-29T08:00:00",
    ],
)
def test_malformed_timestamp_fails_loudly(bad_timestamp):
    controller = BloomPotController(default_reservoir_ml=200.0)

    with pytest.raises(ValueError, match="timestamp"):
        controller.step("peace_lily", 0.03, timestamp=bad_timestamp)


@pytest.mark.parametrize("bad_soil_moisture", [-0.1, 101.0, float("nan"), True, "dry"])
def test_invalid_soil_moisture_fails_loudly(bad_soil_moisture):
    controller = BloomPotController(default_reservoir_ml=200.0)

    with pytest.raises(ValueError, match="soil_moisture"):
        controller.step("peace_lily", bad_soil_moisture)


@pytest.mark.parametrize(
    "state_payload",
    [
        {"reservoir_ml": -1},
        {"reservoir_ml": 200, "low_reading_count": -1},
        {"reservoir_ml": 200, "last_watered_at": "bad-timestamp"},
        {"reservoir_ml": 200, "daily_dose_ml": 5},
        {"reservoir_ml": 200, "daily_dose_ml": 5, "daily_dose_day": "bad-day"},
    ],
)
def test_invalid_state_fails_loudly(state_payload):
    controller = BloomPotController(default_reservoir_ml=200.0)

    with pytest.raises(ValueError, match="state"):
        controller.step(
            "peace_lily",
            0.03,
            timestamp="2026-03-29T08:00:00+00:00",
            state=state_payload,
        )


def test_unknown_plant_id_fails_loudly():
    controller = BloomPotController(default_reservoir_ml=200.0)

    with pytest.raises(KeyError, match="Unknown plant id"):
        controller.step("missing_plant", 0.03)


def test_unresolved_species_id_is_rejected_explicitly():
    controller = BloomPotController(default_reservoir_ml=200.0)

    with pytest.raises(KeyError, match="unresolved and not loadable"):
        controller.step("christmas_cactus", 0.03)


def test_invalid_plant_schema_fails_loudly(tmp_path):
    plant_path = tmp_path / "plant_facts.json"
    controller_path = tmp_path / "controller_profiles.json"
    unresolved_path = tmp_path / "unresolved_species.json"

    write_json(
        plant_path,
        [
            {
                "id": "test_plant",
                "common_name": "Test Plant",
                "scientific_name": "Testus plantus",
                "legacy_category": "tropical",
                "legacy_light_preference_lux": 1000,
                "legacy_water_preference": "evenly_moist",
                "controller_family": "soil_even_moist",
                "controller_family_confidence": "legacy_rule_based",
                "migration_status": "accepted_auto",
                "special_handling": "manual_review_required",
                "manual_review_reasons": [],
                "provenance": {
                    "source_file": "legacy.json",
                    "source_type": "legacy_backup_record",
                    "match_type": "exact_common_name_and_scientific_name",
                },
            }
        ],
    )
    write_json(
        controller_path,
        {"soil_even_moist": build_controller_profile()},
    )
    write_json(unresolved_path, [])

    with pytest.raises(ModelValidationError, match="Schema validation failed"):
        BloomPotController(
            plant_facts_path=plant_path,
            controller_profiles_path=controller_path,
            unresolved_species_path=unresolved_path,
        )


def test_unknown_controller_family_reference_fails_loudly(tmp_path):
    plant_path = tmp_path / "plant_facts.json"
    controller_path = tmp_path / "controller_profiles.json"
    unresolved_path = tmp_path / "unresolved_species.json"

    write_json(
        plant_path,
        [build_plant_record(controller_family="missing_family")],
    )
    write_json(
        controller_path,
        {"soil_even_moist": build_controller_profile()},
    )
    write_json(unresolved_path, [])

    with pytest.raises(ModelValidationError, match="unknown controller family"):
        BloomPotController(
            plant_facts_path=plant_path,
            controller_profiles_path=controller_path,
            unresolved_species_path=unresolved_path,
        )


def test_invalid_controller_profile_thresholds_fail_loudly(tmp_path):
    controller_path = tmp_path / "controller_profiles.json"
    plant_path = tmp_path / "plant_facts.json"
    unresolved_path = tmp_path / "unresolved_species.json"

    write_json(plant_path, [build_plant_record()])
    write_json(
        controller_path,
        {
            "soil_even_moist": build_controller_profile(
                moisture_target={"minimum": 0.04, "maximum": 0.28}
            )
        },
    )
    write_json(unresolved_path, [])

    with pytest.raises(ModelValidationError, match="inconsistent moisture thresholds"):
        BloomPotController(
            plant_facts_path=plant_path,
            controller_profiles_path=controller_path,
            unresolved_species_path=unresolved_path,
        )


@pytest.mark.parametrize(
    ("profile_overrides", "expected_error"),
    [
        (
            {"watering_dose_ml": 80, "max_daily_dose_ml": 60},
            "watering_dose_ml greater than max_daily_dose_ml",
        ),
        (
            {"autowater_enabled": True, "manual_review_reasons": ["needs_manual_review"]},
            "manual review reasons while autowater_enabled is true",
        ),
    ],
)
def test_controller_profile_invariants_fail_loudly(
    tmp_path,
    profile_overrides,
    expected_error,
):
    plant_path = tmp_path / "plant_facts.json"
    controller_path = tmp_path / "controller_profiles.json"
    unresolved_path = tmp_path / "unresolved_species.json"

    write_json(plant_path, [build_plant_record()])
    write_json(
        controller_path,
        {"soil_even_moist": build_controller_profile(**profile_overrides)},
    )
    write_json(unresolved_path, [])

    with pytest.raises(ModelValidationError, match=expected_error):
        BloomPotController(
            plant_facts_path=plant_path,
            controller_profiles_path=controller_path,
            unresolved_species_path=unresolved_path,
        )


def test_accepted_auto_records_cannot_carry_manual_review_only_tags(tmp_path):
    with pytest.raises(ModelValidationError, match="manual-review-only tags"):
        make_controller(
            tmp_path,
            plant_records=[
                build_plant_record(
                    special_handling=["manual_review_required"],
                    manual_review_reasons=["legacy_category_orchid_requires_manual_review"],
                )
            ],
        )


def test_accepted_manual_records_require_manual_review_reason(tmp_path):
    with pytest.raises(ModelValidationError, match="at least one manual review reason"):
        make_controller(
            tmp_path,
            plant_records=[
                build_plant_record(
                    migration_status="accepted_manual",
                    controller_family_confidence="manual_review",
                    special_handling=["manual_review_required"],
                    manual_review_reasons=[],
                    controller_family="orchid_bark",
                )
            ],
            controller_profiles={
                "orchid_bark": build_controller_profile(
                    autowater_enabled=False,
                    manual_review_reasons=["substrate_specific_autowatering_not_validated"],
                )
            },
        )


def test_accepted_auto_records_must_reference_autowater_enabled_families(tmp_path):
    with pytest.raises(ModelValidationError, match="Accepted auto plant record"):
        make_controller(
            tmp_path,
            plant_records=[build_plant_record(controller_family="orchid_bark")],
            controller_profiles={
                "orchid_bark": build_controller_profile(
                    autowater_enabled=False,
                    manual_review_reasons=["substrate_specific_autowatering_not_validated"],
                )
            },
        )


def test_accepted_manual_records_must_reference_manual_review_families(tmp_path):
    with pytest.raises(ModelValidationError, match="Accepted manual plant record"):
        make_controller(
            tmp_path,
            plant_records=[
                build_plant_record(
                    migration_status="accepted_manual",
                    controller_family_confidence="manual_review",
                    special_handling=["manual_review_required"],
                    manual_review_reasons=["legacy_category_orchid_requires_manual_review"],
                )
            ],
        )


def test_unresolved_species_cannot_overlap_with_plant_facts(tmp_path):
    with pytest.raises(ModelValidationError, match="overlap"):
        make_controller(
            tmp_path,
            plant_records=[build_plant_record(id="duplicate_id")],
            unresolved_species=[build_unresolved_record(id="duplicate_id")],
        )


def test_unresolved_species_file_cannot_be_loaded_as_plant_facts():
    with pytest.raises(ModelValidationError, match="Schema validation failed"):
        BloomPotController(
            plant_facts_path=UNRESOLVED_SPECIES_PATH,
            unresolved_species_path=UNRESOLVED_SPECIES_PATH,
        )


def test_expanded_plant_facts_validate_against_schema():
    plant_facts = load_json(PLANT_FACTS_PATH)
    errors = validate_schema(plant_facts, PLANT_FACTS_SCHEMA_PATH)

    assert errors == []
    assert len(plant_facts) > 10


def test_unresolved_species_file_shape():
    unresolved_species = load_json(UNRESOLVED_SPECIES_PATH)
    errors = validate_schema(unresolved_species, UNRESOLVED_SPECIES_SCHEMA_PATH)

    assert errors == []
    assert all(record["unresolved_reasons"] for record in unresolved_species)


def test_controller_family_confidence_enum_handling():
    base_record = build_plant_record()

    for confidence in ("legacy_direct", "legacy_rule_based", "manual_review"):
        payload = [{**base_record, "controller_family_confidence": confidence}]
        assert validate_schema(payload, PLANT_FACTS_SCHEMA_PATH) == []

    invalid_payload = [{**base_record, "controller_family_confidence": "curated"}]
    assert validate_schema(invalid_payload, PLANT_FACTS_SCHEMA_PATH)


def test_migration_status_enum_handling():
    base_record = build_plant_record()

    for status in ("accepted_auto", "accepted_manual"):
        payload = [{**base_record, "migration_status": status}]
        assert validate_schema(payload, PLANT_FACTS_SCHEMA_PATH) == []

    invalid_payload = [{**base_record, "migration_status": "unresolved"}]
    assert validate_schema(invalid_payload, PLANT_FACTS_SCHEMA_PATH)


def test_catalog_controller_consistency():
    controller = BloomPotController(default_reservoir_ml=200.0)

    accepted_manual_ids = []
    for plant_id, record in controller.plant_facts.items():
        profile = controller.controller_profiles[record["controller_family"]]
        if record["migration_status"] == "accepted_auto":
            assert "manual_review_required" not in record["special_handling"]
            assert record["manual_review_reasons"] == []
            assert record["controller_family_confidence"] != "manual_review"
            assert profile["autowater_enabled"] is True
        else:
            accepted_manual_ids.append(plant_id)
            assert record["migration_status"] == "accepted_manual"
            assert "manual_review_required" in record["special_handling"]
            assert record["manual_review_reasons"]
            assert record["controller_family_confidence"] == "manual_review"
            assert profile["autowater_enabled"] is False

    assert len(accepted_manual_ids) == 10


def test_no_accepted_plant_record_missing_controller_family():
    plant_facts = load_json(PLANT_FACTS_PATH)
    missing_controller_family = [
        record["id"] for record in plant_facts if not record.get("controller_family")
    ]

    assert missing_controller_family == []


def test_no_unresolved_species_accidentally_included_in_plant_facts():
    plant_ids = {record["id"] for record in load_json(PLANT_FACTS_PATH)}
    unresolved_ids = {record["id"] for record in load_json(UNRESOLVED_SPECIES_PATH)}

    assert plant_ids.isdisjoint(unresolved_ids)


def test_migration_outputs_cover_every_legacy_species_once():
    plant_facts = load_json(PLANT_FACTS_PATH)
    unresolved_species = load_json(UNRESOLVED_SPECIES_PATH)
    legacy_records = load_json(LEGACY_PATH)

    assert len(plant_facts) + len(unresolved_species) == len(legacy_records)
