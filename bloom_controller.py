from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


BASE_DIR = Path(__file__).resolve().parent
PLANT_FACTS_PATH = BASE_DIR / "plant_facts.json"
CONTROLLER_PROFILES_PATH = BASE_DIR / "controller_profiles.json"
PLANT_FACTS_SCHEMA_PATH = BASE_DIR / "plant_facts.schema.json"
CONTROLLER_PROFILES_SCHEMA_PATH = BASE_DIR / "controller_profiles.schema.json"
UNRESOLVED_SPECIES_PATH = BASE_DIR / "unresolved_species.json"
UNRESOLVED_SPECIES_SCHEMA_PATH = BASE_DIR / "unresolved_species.schema.json"


class ModelValidationError(ValueError):
    """Raised when the on-disk data model violates the declared contract."""


def _parse_timestamp(
    value: str | datetime | None,
    *,
    field_name: str = "timestamp",
) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid ISO 8601 timestamp.") from exc
    else:
        raise ValueError(f"{field_name} must be an ISO 8601 string or datetime object.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information.")
    return parsed


def _parse_iso_day(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 date string.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO 8601 date.") from exc


def _normalize_soil_moisture(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("soil_moisture must be numeric, not boolean.")
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("soil_moisture must be numeric.") from exc
    if not math.isfinite(numeric_value):
        raise ValueError("soil_moisture must be finite.")
    if 0.0 <= numeric_value <= 1.0:
        return numeric_value
    if 1.0 < numeric_value <= 100.0:
        return numeric_value / 100.0
    raise ValueError(
        "soil_moisture must be between 0 and 1, or between 0 and 100 when expressed as percent."
    )


@dataclass
class ControllerState:
    reservoir_ml: float
    low_reading_count: int = 0
    last_watered_at: str | None = None
    daily_dose_ml: float = 0.0
    daily_dose_day: str | None = None

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        *,
        default_reservoir_ml: float,
    ) -> "ControllerState":
        data = data or {}
        reservoir_ml = float(data.get("reservoir_ml", default_reservoir_ml))
        state = cls(
            reservoir_ml=reservoir_ml,
            low_reading_count=int(data.get("low_reading_count", 0)),
            last_watered_at=data.get("last_watered_at"),
            daily_dose_ml=float(data.get("daily_dose_ml", 0.0)),
            daily_dose_day=data.get("daily_dose_day"),
        )
        state.validate()
        return state

    def validate(self) -> None:
        if not math.isfinite(self.reservoir_ml) or self.reservoir_ml < 0:
            raise ValueError("state.reservoir_ml must be a finite nonnegative number.")
        if isinstance(self.low_reading_count, bool) or not isinstance(self.low_reading_count, int):
            raise ValueError("state.low_reading_count must be a nonnegative integer.")
        if self.low_reading_count < 0:
            raise ValueError("state.low_reading_count must be a nonnegative integer.")
        if self.last_watered_at is not None:
            _parse_timestamp(
                self.last_watered_at,
                field_name="state.last_watered_at",
            )
        if not math.isfinite(self.daily_dose_ml) or self.daily_dose_ml < 0:
            raise ValueError("state.daily_dose_ml must be a finite nonnegative number.")
        self.daily_dose_day = _parse_iso_day(
            self.daily_dose_day,
            field_name="state.daily_dose_day",
        )
        if self.daily_dose_ml > 0 and self.daily_dose_day is None:
            raise ValueError(
                "state.daily_dose_day is required when state.daily_dose_ml is positive."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservoir_ml": round(self.reservoir_ml, 3),
            "low_reading_count": self.low_reading_count,
            "last_watered_at": self.last_watered_at,
            "daily_dose_ml": round(self.daily_dose_ml, 3),
            "daily_dose_day": self.daily_dose_day,
        }


class BloomPotController:
    def __init__(
        self,
        plant_facts_path: str | Path = PLANT_FACTS_PATH,
        controller_profiles_path: str | Path = CONTROLLER_PROFILES_PATH,
        unresolved_species_path: str | Path = UNRESOLVED_SPECIES_PATH,
        *,
        default_reservoir_ml: float = 1000.0,
    ) -> None:
        self.controller_profiles = self._load_controller_profiles(controller_profiles_path)
        self.plant_facts = self._load_plant_facts(plant_facts_path)
        self.unresolved_species = self._load_unresolved_species(unresolved_species_path)
        self._validate_model_relationships(
            self.plant_facts,
            self.controller_profiles,
            self.unresolved_species,
        )
        self.default_reservoir_ml = float(default_reservoir_ml)

    @staticmethod
    def _load_plant_facts(path: str | Path) -> dict[str, dict[str, Any]]:
        payload = json.loads(Path(path).read_text())
        BloomPotController._validate_schema(
            payload,
            schema_path=PLANT_FACTS_SCHEMA_PATH,
            data_path=path,
        )
        records: dict[str, dict[str, Any]] = {}
        for record in payload:
            plant_id = record["id"]
            if plant_id in records:
                raise ModelValidationError(f"Duplicate plant id found in {path}: {plant_id}")
            BloomPotController._validate_plant_record(record, data_path=path)
            records[plant_id] = record
        return records

    @staticmethod
    def _load_controller_profiles(path: str | Path) -> dict[str, dict[str, Any]]:
        payload = json.loads(Path(path).read_text())
        BloomPotController._validate_schema(
            payload,
            schema_path=CONTROLLER_PROFILES_SCHEMA_PATH,
            data_path=path,
        )
        for family, profile in payload.items():
            BloomPotController._validate_controller_profile(
                family,
                profile,
                data_path=path,
            )
        return payload

    @staticmethod
    def _load_unresolved_species(path: str | Path) -> dict[str, dict[str, Any]]:
        payload = json.loads(Path(path).read_text())
        BloomPotController._validate_schema(
            payload,
            schema_path=UNRESOLVED_SPECIES_SCHEMA_PATH,
            data_path=path,
        )
        records: dict[str, dict[str, Any]] = {}
        for record in payload:
            species_id = record["id"]
            if species_id in records:
                raise ModelValidationError(
                    f"Duplicate unresolved species id found in {path}: {species_id}"
                )
            records[species_id] = record
        return records

    def initialize_state(self, reservoir_ml: float | None = None) -> ControllerState:
        if reservoir_ml is None:
            reservoir_ml = self.default_reservoir_ml
        state = ControllerState(reservoir_ml=float(reservoir_ml))
        state.validate()
        return state

    def save_state(self, state: ControllerState, path: str | Path) -> None:
        state.validate()
        Path(path).write_text(json.dumps(state.to_dict(), indent=2) + "\n")

    def load_state(self, path: str | Path) -> ControllerState:
        payload = json.loads(Path(path).read_text())
        return ControllerState.from_dict(payload, default_reservoir_ml=self.default_reservoir_ml)

    def step(
        self,
        plant_id: str,
        soil_moisture: float,
        *,
        timestamp: str | datetime | None = None,
        state: ControllerState | dict[str, Any] | None = None,
        reservoir_ml: float | None = None,
    ) -> dict[str, Any]:
        current_state = self._coerce_state(state, reservoir_ml=reservoir_ml)
        evaluation = self._evaluate_step(
            plant_id,
            soil_moisture,
            timestamp=timestamp,
            state=current_state,
        )

        return {
            "plant_id": plant_id,
            "controller_family": evaluation["controller_family"],
            "pump_on": evaluation["pump_on"],
            "dose_ml": evaluation["dose_ml"],
            "reason": evaluation["reason"],
            "state": evaluation["state_after"],
            "soil_moisture": evaluation["soil_moisture"],
            "target_band": evaluation["target_band"],
        }

    def simulate_scenario(
        self,
        plant_id: str,
        readings: Iterable[dict[str, Any]],
        *,
        initial_state: ControllerState | dict[str, Any] | None = None,
        initial_reservoir_ml: float | None = None,
    ) -> dict[str, Any]:
        scenario_state = self._clone_state(
            initial_state,
            reservoir_ml=initial_reservoir_ml,
        )
        trace: list[dict[str, Any]] = []
        previous_time: datetime | None = None

        for index, reading in enumerate(readings):
            if "timestamp" not in reading:
                raise KeyError(f"Reading {index} is missing timestamp.")
            if "soil_moisture" not in reading:
                raise KeyError(f"Reading {index} is missing soil_moisture.")

            current_time = _parse_timestamp(
                reading["timestamp"],
                field_name=f"readings[{index}].timestamp",
            )
            if previous_time is not None and current_time < previous_time:
                raise ValueError("Scenario readings must be ordered by timestamp.")

            trace.append(
                self._evaluate_step(
                    plant_id,
                    reading["soil_moisture"],
                    timestamp=current_time,
                    state=scenario_state,
                )
            )
            previous_time = current_time

        return {
            "plant_id": plant_id,
            "trace": trace,
            "final_state": scenario_state.to_dict(),
        }

    def _evaluate_step(
        self,
        plant_id: str,
        soil_moisture: float,
        *,
        timestamp: str | datetime | None,
        state: ControllerState,
    ) -> dict[str, Any]:
        plant = self._get_plant_record(plant_id)
        family = plant["controller_family"]
        profile = self.controller_profiles[family]
        current_time = _parse_timestamp(timestamp, field_name="timestamp")
        state.validate()
        soil_value = _normalize_soil_moisture(soil_moisture)
        lower_target = float(profile["moisture_target"]["minimum"])
        upper_target = float(profile["moisture_target"]["maximum"])
        watering_dose_ml = float(profile["watering_dose_ml"])
        max_daily_dose_ml = float(profile["max_daily_dose_ml"])

        before_state = state.to_dict()
        trace = {
            "timestamp": current_time.isoformat(),
            "plant_id": plant_id,
            "controller_family": family,
            "input_soil_moisture": soil_moisture,
            "soil_moisture": round(soil_value, 3),
            "target_band": [lower_target, upper_target],
            "state_before": before_state,
            "reservoir_ml_before": before_state["reservoir_ml"],
            "low_reading_count_before": before_state["low_reading_count"],
            "last_watered_at_before": before_state["last_watered_at"],
            "daily_dose_ml_before": before_state["daily_dose_ml"],
            "daily_dose_day_before": before_state["daily_dose_day"],
        }

        self._roll_daily_window(state, current_time)

        reasons: list[str] = []
        decision = False
        dose_ml = 0.0
        decision_code: str

        if plant["migration_status"] == "accepted_manual":
            state.low_reading_count = 0
            decision_code = "manual_review_block"
            reasons.append("Plant record requires manual review; autowatering blocked.")
        elif not profile["autowater_enabled"]:
            state.low_reading_count = 0
            decision_code = "manual_review_block"
            reasons.append(
                "Autowatering disabled for this controller family; manual review required."
            )
        elif soil_value >= profile["hard_wet_cutoff"]:
            state.low_reading_count = 0
            decision_code = "wet_cutoff_block"
            reasons.append(
                f"Soil moisture {soil_value:.2f} is at or above wet cutoff "
                f"{profile['hard_wet_cutoff']:.2f}; no watering."
            )
        else:
            reading_is_low = soil_value < lower_target
            if not reading_is_low:
                state.low_reading_count = 0

            if state.reservoir_ml < watering_dose_ml:
                decision_code = "reservoir_block"
                reasons.append(
                    "Reservoir does not contain the fixed watering dose; refill required."
                )
            elif self._cooldown_active(state, current_time, profile["cooldown_minutes"]):
                decision_code = "cooldown_block"
                reasons.append(
                    f"Cooldown active for {profile['cooldown_minutes']} minutes after the last dose."
                )
            elif state.daily_dose_ml + watering_dose_ml > max_daily_dose_ml:
                decision_code = "daily_budget_block"
                reasons.append("Max daily fixed-dose budget reached; no watering.")
            elif soil_value <= profile["hard_dry_cutoff"]:
                decision = True
                dose_ml = watering_dose_ml
                decision_code = "hard_dry_approved"
                reasons.append(
                    f"Soil moisture {soil_value:.2f} is at or below hard dry cutoff "
                    f"{profile['hard_dry_cutoff']:.2f}; fixed dose approved."
                )
            elif reading_is_low:
                state.low_reading_count += 1
                if state.low_reading_count >= profile["confirm_low_readings"]:
                    decision = True
                    dose_ml = watering_dose_ml
                    decision_code = "confirmed_low_approved"
                    reasons.append(
                        f"Confirmed {state.low_reading_count} consecutive low readings "
                        f"below target band floor {lower_target:.2f}; fixed dose approved."
                    )
                else:
                    decision_code = "confirmation_wait"
                    reasons.append(
                        f"Low reading observed below target band floor {lower_target:.2f}, "
                        f"waiting for {profile['confirm_low_readings']} confirmations."
                    )
            elif soil_value <= upper_target:
                decision_code = "inside_target_band"
                reasons.append(
                    f"Soil moisture {soil_value:.2f} is inside target band "
                    f"{lower_target:.2f}-{upper_target:.2f}; no watering."
                )
            else:
                decision_code = "above_target_band"
                reasons.append(
                    f"Soil moisture {soil_value:.2f} is above target band ceiling "
                    f"{upper_target:.2f}; no watering."
                )

        if decision:
            state.reservoir_ml -= dose_ml
            state.daily_dose_ml += dose_ml
            state.last_watered_at = current_time.isoformat()
            state.low_reading_count = 0

        after_state = state.to_dict()
        trace.update(
            {
                "decision_code": decision_code,
                "pump_on": decision,
                "dose_ml": dose_ml,
                "reason": " ".join(reasons),
                "state_after": after_state,
                "reservoir_ml_after": after_state["reservoir_ml"],
                "low_reading_count_after": after_state["low_reading_count"],
                "last_watered_at_after": after_state["last_watered_at"],
                "daily_dose_ml_after": after_state["daily_dose_ml"],
                "daily_dose_day_after": after_state["daily_dose_day"],
            }
        )
        return trace

    def _coerce_state(
        self,
        state: ControllerState | dict[str, Any] | None,
        *,
        reservoir_ml: float | None,
    ) -> ControllerState:
        if isinstance(state, ControllerState):
            current_state = state
        else:
            current_state = ControllerState.from_dict(
                state,
                default_reservoir_ml=self.default_reservoir_ml,
            )
        if reservoir_ml is not None:
            current_state.reservoir_ml = float(reservoir_ml)
        current_state.validate()
        return current_state

    def _clone_state(
        self,
        state: ControllerState | dict[str, Any] | None,
        *,
        reservoir_ml: float | None,
    ) -> ControllerState:
        if isinstance(state, ControllerState):
            payload = state.to_dict()
        else:
            payload = dict(state or {})
        if reservoir_ml is not None:
            payload["reservoir_ml"] = float(reservoir_ml)
        return ControllerState.from_dict(
            payload,
            default_reservoir_ml=self.default_reservoir_ml,
        )

    def _get_plant_record(self, plant_id: str) -> dict[str, Any]:
        if plant_id in self.plant_facts:
            return self.plant_facts[plant_id]
        if plant_id in self.unresolved_species:
            raise KeyError(
                f"Plant id is unresolved and not loadable as accepted plant facts: {plant_id}"
            )
        raise KeyError(f"Unknown plant id: {plant_id}")

    @staticmethod
    def _roll_daily_window(state: ControllerState, current_time: datetime) -> None:
        current_day = current_time.date().isoformat()
        if state.daily_dose_day != current_day:
            state.daily_dose_day = current_day
            state.daily_dose_ml = 0.0

    @staticmethod
    def _cooldown_active(
        state: ControllerState,
        current_time: datetime,
        cooldown_minutes: int,
    ) -> bool:
        if not state.last_watered_at:
            return False
        last_watered = _parse_timestamp(state.last_watered_at)
        elapsed_minutes = (current_time - last_watered).total_seconds() / 60.0
        return elapsed_minutes < cooldown_minutes

    @staticmethod
    def _validate_schema(
        payload: Any,
        *,
        schema_path: Path,
        data_path: str | Path,
    ) -> None:
        schema = json.loads(schema_path.read_text())
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload),
            key=lambda error: list(error.absolute_path),
        )
        if not errors:
            return
        first_error = errors[0]
        location = ".".join(str(part) for part in first_error.absolute_path) or "<root>"
        raise ModelValidationError(
            f"Schema validation failed for {data_path} at {location}: {first_error.message}"
        )

    @staticmethod
    def _validate_plant_record(record: dict[str, Any], *, data_path: str | Path) -> None:
        requires_review = "manual_review_required" in record["special_handling"]
        has_reasons = bool(record["manual_review_reasons"])
        if record["migration_status"] == "accepted_manual":
            if not requires_review:
                raise ModelValidationError(
                    f"Accepted manual plant record {record['id']} in {data_path} must include "
                    "the manual_review_required tag."
                )
            if not has_reasons:
                raise ModelValidationError(
                    f"Accepted manual plant record {record['id']} in {data_path} must include "
                    "at least one manual review reason."
                )
            if record["controller_family_confidence"] != "manual_review":
                raise ModelValidationError(
                    f"Accepted manual plant record {record['id']} in {data_path} must use "
                    "manual_review controller_family_confidence."
                )
        if requires_review and not has_reasons:
            raise ModelValidationError(
                f"Plant record {record['id']} in {data_path} requires manual review reasons."
            )
        if not requires_review and has_reasons:
            raise ModelValidationError(
                f"Plant record {record['id']} in {data_path} has manual review reasons "
                "without the manual_review_required tag."
            )
        if record["migration_status"] == "accepted_auto":
            if requires_review:
                raise ModelValidationError(
                    f"Accepted auto plant record {record['id']} in {data_path} cannot carry "
                    "manual-review-only tags."
                )
            if record["controller_family_confidence"] == "manual_review":
                raise ModelValidationError(
                    f"Accepted auto plant record {record['id']} in {data_path} cannot use "
                    "manual_review controller_family_confidence."
                )

    @staticmethod
    def _validate_controller_profile(
        family: str,
        profile: dict[str, Any],
        *,
        data_path: str | Path,
    ) -> None:
        minimum = float(profile["moisture_target"]["minimum"])
        maximum = float(profile["moisture_target"]["maximum"])
        hard_dry_cutoff = float(profile["hard_dry_cutoff"])
        hard_wet_cutoff = float(profile["hard_wet_cutoff"])

        if not hard_dry_cutoff <= minimum <= maximum <= hard_wet_cutoff:
            raise ModelValidationError(
                f"Controller profile {family} in {data_path} has inconsistent moisture thresholds."
            )
        watering_dose_ml = float(profile["watering_dose_ml"])
        max_daily_dose_ml = float(profile["max_daily_dose_ml"])
        if profile["confirm_low_readings"] <= 0:
            raise ModelValidationError(
                f"Controller profile {family} in {data_path} must use a positive "
                "confirm_low_readings value."
            )
        if profile["cooldown_minutes"] < 0:
            raise ModelValidationError(
                f"Controller profile {family} in {data_path} cannot use a negative cooldown."
            )
        if profile["autowater_enabled"] and watering_dose_ml <= 0:
            raise ModelValidationError(
                f"Controller profile {family} in {data_path} must use a positive "
                "watering_dose_ml when autowater_enabled is true."
            )
        if max_daily_dose_ml < watering_dose_ml:
            raise ModelValidationError(
                f"Controller profile {family} in {data_path} has watering_dose_ml greater "
                "than max_daily_dose_ml."
            )
        if not profile["autowater_enabled"] and not profile["manual_review_reasons"]:
            raise ModelValidationError(
                f"Controller profile {family} in {data_path} disables autowatering without "
                "manual review reasons."
            )
        if profile["autowater_enabled"] and profile["manual_review_reasons"]:
            raise ModelValidationError(
                f"Controller profile {family} in {data_path} has manual review reasons while "
                "autowater_enabled is true."
            )

    @staticmethod
    def _validate_model_relationships(
        plant_facts: dict[str, dict[str, Any]],
        controller_profiles: dict[str, dict[str, Any]],
        unresolved_species: dict[str, dict[str, Any]],
    ) -> None:
        overlapping_ids = sorted(set(plant_facts).intersection(unresolved_species))
        if overlapping_ids:
            raise ModelValidationError(
                "Accepted plant facts and unresolved species overlap: "
                + ", ".join(overlapping_ids)
            )
        for plant_id, record in plant_facts.items():
            family = record["controller_family"]
            if family not in controller_profiles:
                raise ModelValidationError(
                    f"Plant record {plant_id} references unknown controller family: {family}"
                )
            profile = controller_profiles[family]
            if record["migration_status"] == "accepted_auto" and not profile["autowater_enabled"]:
                raise ModelValidationError(
                    f"Accepted auto plant record {plant_id} references manual-review "
                    f"controller family: {family}"
                )
            if record["migration_status"] == "accepted_manual" and profile["autowater_enabled"]:
                raise ModelValidationError(
                    f"Accepted manual plant record {plant_id} references autowater-enabled "
                    f"controller family: {family}"
                )


def bloompot_step(
    sensor_data: dict[str, Any],
    plant_id: str,
    *,
    state: ControllerState | dict[str, Any] | None = None,
    plant_facts_path: str | Path = PLANT_FACTS_PATH,
    controller_profiles_path: str | Path = CONTROLLER_PROFILES_PATH,
) -> dict[str, Any]:
    controller = BloomPotController(
        plant_facts_path=plant_facts_path,
        controller_profiles_path=controller_profiles_path,
        default_reservoir_ml=float(sensor_data.get("reservoir_ml", 1000.0)),
    )
    return controller.step(
        plant_id,
        sensor_data["soil_moisture"],
        timestamp=sensor_data.get("timestamp"),
        state=state,
        reservoir_ml=sensor_data.get("reservoir_ml"),
    )


if __name__ == "__main__":
    controller = BloomPotController()
    demo_state = controller.initialize_state(reservoir_ml=250.0)
    demo_result = controller.step(
        "peace_lily",
        0.04,
        timestamp="2026-03-29T12:00:00+00:00",
        state=demo_state,
    )
    print(json.dumps(demo_result, indent=2))
