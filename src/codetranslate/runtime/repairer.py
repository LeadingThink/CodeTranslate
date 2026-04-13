from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
from ..core.models import MigrationUnit, RepairRecord, UnitContext, UnitStatus
from ..storage.workspace import WorkspaceManager


class Repairer:
    def __init__(self, llm: LLMClient, workspace: WorkspaceManager) -> None:
        self.llm = llm
        self.workspace = workspace

    def repair(self, unit: MigrationUnit, context: UnitContext, failure_log: str, test_path: Path) -> bool:
        unit.status = UnitStatus.REPAIRING
        unit.retry_count += 1
        artifact = self.llm.repair_artifact(context, failure_log, str(test_path))
        failure_type = self._classify_failure(failure_log)
        impact_scope: list[str] = []
        action = "No automated repair artifact was produced."
        if artifact is not None:
            path = Path(artifact.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            compile(artifact.content, str(path), "exec")
            path.write_text(artifact.content.rstrip() + "\n", encoding="utf-8")
            impact_scope = [str(path)]
            action = artifact.rationale
        record = RepairRecord(
            unit_id=unit.unit_id,
            attempt=unit.retry_count,
            failure_type=failure_type,
            failure_reason=failure_log[:500],
            action=action,
            impact_scope=impact_scope or [unit.target_file_path, str(test_path)],
            verification_passed=False,
        )
        self.workspace.save_repair_record(record)
        if unit.retry_count > unit.max_retries:
            unit.status = UnitStatus.FAILED
            unit.failure_reason = failure_log[:500]
            return False
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
