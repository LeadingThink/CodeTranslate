from __future__ import annotations

from dataclasses import dataclass
from ..core.models import MigrationUnit, UnitStatus


@dataclass(slots=True)
class UnitStateMachine:
    _TERMINAL_STATUSES = {
        UnitStatus.VERIFIED,
        UnitStatus.FAILED,
        UnitStatus.BLOCKED,
    }
    _RESTARTABLE_STATUSES = {
        UnitStatus.GENERATING,
        UnitStatus.GENERATED,
        UnitStatus.TESTING,
        UnitStatus.TESTED,
        UnitStatus.REPAIRING,
    }

    def refresh_ready_units(self, units: list[MigrationUnit]) -> list[MigrationUnit]:
        units_by_id = {unit.unit_id: unit for unit in units}
        for unit in units:
            if unit.status in self._TERMINAL_STATUSES:
                continue
            if unit.status in self._RESTARTABLE_STATUSES:
                unit.status = UnitStatus.DISCOVERED
            if self._dependencies_verified(unit, units_by_id) and unit.status in {
                UnitStatus.ANALYZED,
                UnitStatus.DISCOVERED,
            }:
                unit.status = UnitStatus.READY
        return [unit for unit in units if unit.status == UnitStatus.READY]

    def unlock_dependents(
        self, unit: MigrationUnit, units_by_id: dict[str, MigrationUnit]
    ) -> None:
        for dependent_id in unit.dependents:
            dependent = units_by_id[dependent_id]
            if dependent.status in {
                UnitStatus.VERIFIED,
                UnitStatus.FAILED,
                UnitStatus.BLOCKED,
            }:
                continue
            if self._dependencies_verified(dependent, units_by_id):
                dependent.status = UnitStatus.READY

    def invalidate_stale_verified_units(
        self,
        units: list[MigrationUnit],
        current_signatures: dict[str, dict[str, str]],
    ) -> list[str]:
        units_by_id = {unit.unit_id: unit for unit in units}
        invalidated: list[str] = []
        for unit in units:
            if unit.status != UnitStatus.VERIFIED:
                continue
            expected = unit.verified_output_signatures
            current = current_signatures.get(unit.unit_id, {})
            if not expected or expected == current:
                continue
            self._mark_dirty(unit, units_by_id, invalidated)
        return invalidated

    def can_run_as_single_unit(
        self,
        unit: MigrationUnit,
        units_by_id: dict[str, MigrationUnit],
    ) -> bool:
        return self._dependencies_verified(unit, units_by_id)

    def build_blocked_report(self, units: list[MigrationUnit]) -> dict[str, object]:
        return {
            "blocked": [
                {
                    "unit_id": unit.unit_id,
                    "status": unit.status.value,
                    "dependencies": unit.dependencies,
                    "failure_reason": unit.failure_reason,
                }
                for unit in units
                if unit.status in {UnitStatus.BLOCKED, UnitStatus.FAILED}
            ]
        }

    def _dependencies_verified(
        self,
        unit: MigrationUnit,
        units_by_id: dict[str, MigrationUnit],
    ) -> bool:
        return all(
            units_by_id[dependency].status == UnitStatus.VERIFIED
            for dependency in unit.dependencies
        )

    def _mark_dirty(
        self,
        unit: MigrationUnit,
        units_by_id: dict[str, MigrationUnit],
        invalidated: list[str],
    ) -> None:
        if unit.unit_id in invalidated:
            return
        unit.status = UnitStatus.DISCOVERED
        unit.failure_reason = None
        unit.verified_output_signatures = {}
        invalidated.append(unit.unit_id)
        for dependent_id in unit.dependents:
            dependent = units_by_id[dependent_id]
            if dependent.status == UnitStatus.FAILED:
                continue
            self._mark_dirty(dependent, units_by_id, invalidated)
