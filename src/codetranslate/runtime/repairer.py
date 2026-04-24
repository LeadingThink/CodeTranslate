from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
from .language_runtime import validate_source_file
from .python_import_normalizer import normalize_python_imports
from ..core.models import MigrationUnit, RepairRecord, UnitContext, UnitStatus
from ..storage.workspace import WorkspaceManager


class Repairer:
    def __init__(self, llm: LLMClient, workspace: WorkspaceManager) -> None:
        self.llm = llm
        self.workspace = workspace

    def repair(
        self,
        unit: MigrationUnit,
        context: UnitContext,
        failure_log: str,
        test_path: Path,
    ) -> bool:
        unit.status = UnitStatus.REPAIRING
        unit.retry_count += 1
        action = self.llm.repair_artifact(context, failure_log, str(test_path))
        failure_type = self._classify_failure(failure_log)
        target_paths = [
            Path(path)
            for path in (unit.batch_target_file_paths or [unit.target_file_path])
        ]
        impact_scope = [
            str(path) for path in (*target_paths, test_path) if path.exists()
        ]
        record = RepairRecord(
            unit_id=unit.unit_id,
            attempt=unit.retry_count,
            failure_type=failure_type,
            failure_reason=failure_log[:500],
            action=action,
            impact_scope=impact_scope
            or [
                *(str(path) for path in target_paths),
                str(test_path),
            ],
            verification_passed=False,
        )
        self.workspace.save_repair_record(record)
        if unit.retry_count > unit.max_retries:
            unit.status = UnitStatus.FAILED
            unit.failure_reason = failure_log[:500]
            return False
        self._normalize_and_validate_existing_targets(unit, test_path)
        unit.status = UnitStatus.GENERATED
        return True

    def _classify_failure(self, failure_log: str) -> str:
        lowered = failure_log.lower()
        if "syntaxerror" in lowered or "indentationerror" in lowered:
            return "compile_error"
        if "assert" in lowered or "expected" in lowered:
            return "test_assertion_failure"
        if "modulenotfounderror" in lowered or "importerror" in lowered:
            return "dependency_error"
        if "typeerror" in lowered or "attributeerror" in lowered:
            return "type_or_interface_error"
        if "traceback" in lowered:
            return "runtime_exception"
        return "unknown_failure"

    def _normalize_and_validate_existing_targets(
        self, unit: MigrationUnit, test_path: Path
    ) -> None:
        target_root = Path(self.llm.paths.target_root)
        target_items = unit.batch_target_file_paths or [unit.target_file_path]
        for path in (Path(item) for item in target_items):
            if path.exists():
                normalize_python_imports(path, target_root)
                validate_source_file(path, unit.target_language)
        if test_path.exists():
            validate_source_file(test_path, unit.target_language)
