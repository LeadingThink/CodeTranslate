from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
from .language_runtime import run_test_file
from ..core.models import MigrationUnit, UnitContext, UnitExecutionResult, UnitStatus
from ..storage.workspace import WorkspaceManager


class UnitTester:
    def __init__(self, llm: LLMClient, workspace: WorkspaceManager) -> None:
        self.llm = llm
        self.workspace = workspace

    def generate_test(self, unit: MigrationUnit, context: UnitContext) -> Path:
        test_suffix = self._test_suffix_for_language(unit.target_language)
        test_path = self.workspace.generated_tests_dir / f"test_{unit.unit_id}{test_suffix}"
        self.llm.generate_tests(context, str(test_path))
        if not test_path.exists():
            raise RuntimeError(f"Agent did not write test file: {test_path}")
        return test_path

    def run_test(self, unit: MigrationUnit, test_path: Path) -> UnitExecutionResult:
        unit.status = UnitStatus.TESTING
        process = run_test_file(test_path, unit.target_language)
        combined = (process.stdout or "") + "\n" + (process.stderr or "")
        log_path = self.workspace.log_unit(unit.unit_id, "test", combined.strip())
        unit.status = UnitStatus.TESTED if process.returncode == 0 else UnitStatus.REPAIRING
        return UnitExecutionResult(
            unit_id=unit.unit_id,
            status=unit.status,
            log_path=str(log_path),
            details={"returncode": process.returncode},
        )

    def _test_suffix_for_language(self, language: str) -> str:
        if language == "nodejs":
            return ".js"
        return ".py"
