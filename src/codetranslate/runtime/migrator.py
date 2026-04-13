from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
from .language_runtime import validate_source_file
from ..core.models import MigrationUnit, UnitContext, UnitExecutionResult, UnitStatus
from ..storage.workspace import WorkspaceManager


class UnitMigrator:
    def __init__(self, llm: LLMClient, workspace: WorkspaceManager) -> None:
        self.llm = llm
        self.workspace = workspace

    def migrate(self, unit: MigrationUnit, context: UnitContext) -> UnitExecutionResult:
        unit.status = UnitStatus.GENERATING
        generation = self.llm.generate_code(context)
        target_path = Path(context.target_file_path)
        if not target_path.exists():
            raise RuntimeError(f"Agent did not write target file: {target_path}")
        validate_source_file(target_path, unit.target_language)
        log_path = self.workspace.log_unit(unit.unit_id, "generate", generation.rationale)
        unit.status = UnitStatus.GENERATED
        return UnitExecutionResult(
            unit_id=unit.unit_id,
            status=UnitStatus.GENERATED,
            output_path=str(target_path),
            log_path=str(log_path),
            details={"rationale": generation.rationale},
        )
