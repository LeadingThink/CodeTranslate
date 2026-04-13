from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
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
        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = generation.code.strip() + "\n"
        self._validate_python(content, target_path)
        target_path.write_text(content, encoding="utf-8")
        log_path = self.workspace.log_unit(unit.unit_id, "generate", generation.rationale)
        unit.status = UnitStatus.GENERATED
        return UnitExecutionResult(
            unit_id=unit.unit_id,
            status=UnitStatus.GENERATED,
            output_path=str(target_path),
            log_path=str(log_path),
            details={"rationale": generation.rationale},
        )

    def _validate_python(self, content: str, target_path: Path) -> None:
        compile(content, str(target_path), "exec")
