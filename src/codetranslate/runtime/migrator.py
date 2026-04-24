from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
from .language_runtime import validate_source_file
from .python_import_normalizer import normalize_python_imports
from ..core.models import MigrationUnit, UnitContext, UnitExecutionResult, UnitStatus
from ..storage.workspace import WorkspaceManager


class UnitMigrator:
    def __init__(self, llm: LLMClient, workspace: WorkspaceManager) -> None:
        self.llm = llm
        self.workspace = workspace

    def migrate(self, unit: MigrationUnit, context: UnitContext) -> UnitExecutionResult:
        unit.status = UnitStatus.GENERATING
        generation = self.llm.generate_code(context)
        target_paths = [
            Path(path)
            for path in context.target_file_paths or [context.target_file_path]
        ]
        missing_paths = [path for path in target_paths if not path.exists()]
        if missing_paths:
            raise RuntimeError(
                f"Agent did not write target file(s): {[str(path) for path in missing_paths]}"
            )
        for target_path in target_paths:
            normalize_python_imports(target_path, Path(self.llm.paths.target_root))
            validate_source_file(target_path, unit.target_language)
        log_path = self.workspace.log_unit(
            unit.unit_id, "generate", generation.rationale
        )
        unit.status = UnitStatus.GENERATED
        return UnitExecutionResult(
            unit_id=unit.unit_id,
            status=UnitStatus.GENERATED,
            output_path=str(target_paths[0]),
            log_path=str(log_path),
            details={
                "rationale": generation.rationale,
                "target_paths": [str(path) for path in target_paths],
            },
        )
