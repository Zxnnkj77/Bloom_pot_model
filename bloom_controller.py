from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


BASE_DIR = Path(__file__).resolve().parent
PLANT_FACTS_PATH = BASE_DIR / "plant_facts.json"
CONTROLLER_PROFILES_PATH = BASE_DIR / "controller_profiles.json"
PLANT_FACTS_SCHEMA_PATH = BASE_DIR / "plant_facts.schema.json"
CONTROLLER_PROFILES_SCHEMA_PATH = BASE_DIR / "controller_profiles.schema.json"


class ModelValidationError(ValueError):
    """Raised when the on-disk data model violates the declared contract."""


def _parse_timestamp(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _normalize_soil_moisture(value: float) -> float:
    if value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


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
        return cls(
            reservoir_ml=reservoir_ml,
            low_reading_count=int(data.get("low_reading_count", 0)),
            last_watered_at=data.get("last_watered_at"),
            daily_dose_ml=float(data.get("daily_dose_ml", 0.0)),
            daily_dose_day=data.get("daily_dose_day"),
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
        *,
        default_reservoir_ml: float = 1000.0,
    ) -> None:
        self.controller_profiles = self._load_controller_profiles(controller_profiles_path)
        self.plant_facts = self._load_plant_facts(plant_facts_path)
        self._validate_model_relationships(self.plant_facts, self.controller_profiles)
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

    def initialize_state(self, reservoir_ml: float | None = None) -> ControllerState:
        if reservoir_ml is None:
            reservoir_ml = self.default_reservoir_ml
        return ControllerState(reservoir_ml=float(reservoir_ml))

    def save_state(self, state: ControllerState, path: str | Path) -> None:
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
            "timestamp": evaluation["timestamp"],
            "plant_id": plant_id,
            "controller_family": evaluation["controller_family"],
            "pump_on": evaluation["pump_on"],
            "dose_ml": evaluation["dose_ml"],
            "reason": evaluation["reason"],
            "state": current_state.to_dict(),
            "soil_moisture": evaluation["normalized_soil_moisture"],
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

            current_time = _parse_timestamp(reading["timestamp"])
            if previous_time is not None and current_time < previous_time:
                raise ValueError("Scenario readings must be ordered by timestamp.")

            trace.append(
                self._evaluate_step(
                    plant_id,
                    float(reading["soil_moisture"]),
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
        if plant_id not in self.plant_facts:
            raise KeyError(f"Unknown plant id: {plant_id}")

        plant = self.plant_facts[plant_id]
        family = plant["controller_family"]
        profile = self.controller_profiles[family]
        current_time = _parse_timestamp(timestamp)
        soil_value = _normalize_soil_moisture(soil_moisture)
        lower_target = float(profile["moisture_target"]["minimum"])
        upper_target = float(profile["moisture_target"]["maximum"])

        trace = {
            "timestamp": current_time.isoformat(),
            "plant_id": plant_id,
            "controller_family": family,
            "input_soil_moisture": soil_moisture,
            "normalized_soil_moisture": round(soil_value, 3),
            "reservoir_ml_before": round(state.reservoir_ml, 3),
            "low_reading_count_before": state.low_reading_count,
            "daily_dose_ml_before": round(state.daily_dose_ml, 3),
            "daily_dose_day_before": state.daily_dose_day,
            "target_band": [lower_target, upper_target],
        }

        self._roll_daily_window(state, current_time)

        reasons: list[str] = []
        decision = False
        dose_ml = 0.0
        autowater_enabled = profile["autowater_enabled"]

        if not autowater_enabled:
            state.low_reading_count = 0
            reasons.append(
                "Autowatering disabled for this controller family; manual review required."
            )
        elif soil_value >= profile["hard_wet_cutoff"]:
            state.low_reading_count = 0
            reasons.append(
                f"Soil moisture {soil_value:.2f} is at or above wet cutoff "
                f"{profile['hard_wet_cutoff']:.2f}; no watering."
            )
        else:
            reading_is_low = soil_value < lower_target
            if not reading_is_low:
                state.low_reading_count = 0

            if state.reservoir_ml < profile["watering_dose_ml"]:
                reasons.append(
                    "Reservoir does not contain the fixed watering dose; refill required."
                )
            elif self._cooldown_active(state, current_time, profile["cooldown_minutes"]):
                reasons.append(
                    f"Cooldown active for {profile['cooldown_minutes']} minutes after the last dose."
                )
            elif state.daily_dose_ml + profile["watering_dose_ml"] > profile["max_daily_dose_ml"]:
                reasons.append("Max daily fixed-dose budget reached; no watering.")
            elif soil_value <= profile["hard_dry_cutoff"]:
                decision = True
                dose_ml = float(profile["watering_dose_ml"])
                reasons.append(
                    f"Soil moisture {soil_value:.2f} is at or below hard dry cutoff "
                    f"{profile['hard_dry_cutoff']:.2f}; fixed dose approved."
                )
            elif reading_is_low:
                state.low_reading_count += 1
                if state.low_reading_count >= profile["confirm_low_readings"]:
                    decision = True
                    dose_ml = float(profile["watering_dose_ml"])
                    reasons.append(
                        f"Confirmed {state.low_reading_count} consecutive low readings "
                        f"below target band floor {lower_target:.2f}; fixed dose approved."
                    )
                else:
                    reasons.append(
                        f"Low reading observed below target band floor {lower_target:.2f}, "
                        f"waiting for {profile['confirm_low_readings']} confirmations."
                    )
            elif soil_value <= upper_target:
                reasons.append(
                    f"Soil moisture {soil_value:.2f} is inside target band "
                    f"{lower_target:.2f}-{upper_target:.2f}; no watering."
                )
            else:
                reasons.append(
                    f"Soil moisture {soil_value:.2f} is above target band ceiling "
                    f"{upper_target:.2f}; no watering."
                )

        if decision:
            state.reservoir_ml -= dose_ml
            state.daily_dose_ml += dose_ml
            state.last_watered_at = current_time.isoformat()
            state.low_reading_count = 0

        trace.update(
            {
                "pump_on": decision,
                "dose_ml": dose_ml,
                "reason": " ".join(reasons),
                "reservoir_ml_after": round(state.reservoir_ml, 3),
                "low_reading_count_after": state.low_reading_count,
                "daily_dose_ml_after": round(state.daily_dose_ml, 3),
                "daily_dose_day_after": state.daily_dose_day,
                "last_watered_at_after": state.last_watered_at,
                "state_after": state.to_dict(),
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
        if requires_review and not has_reasons:
            raise ModelValidationError(
                f"Plant record {record['id']} in {data_path} requires manual review reasons."
            )
        if not requires_review and has_reasons:
            raise ModelValidationError(
                f"Plant record {record['id']} in {data_path} has manual review reasons "
                "without the manual_review_required tag."
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
        if float(profile["watering_dose_ml"]) > float(profile["max_daily_dose_ml"]):
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
    ) -> None:
        for plant_id, record in plant_facts.items():
            family = record["controller_family"]
            if family not in controller_profiles:
                raise ModelValidationError(
                    f"Plant record {plant_id} references unknown controller family: {family}"
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
