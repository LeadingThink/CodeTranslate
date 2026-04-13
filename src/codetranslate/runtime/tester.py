from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .llm import LLMClient
from ..core.models import MigrationUnit, UnitContext, UnitExecutionResult, UnitStatus
from ..storage.workspace import WorkspaceManager


class UnitTester:
    def __init__(self, llm: LLMClient, workspace: WorkspaceManager) -> None:
        self.llm = llm
        self.workspace = workspace

    def generate_test(self, unit: MigrationUnit, context: UnitContext) -> Path:
        test_path = self.workspace.generated_tests_dir / f"test_{unit.unit_id}.py"
        test_content = self.llm.generate_tests(context, str(test_path))
        test_path.write_text(test_content, encoding="utf-8")
        return test_path

    def run_test(self, unit: MigrationUnit, test_path: Path) -> UnitExecutionResult:
        unit.status = UnitStatus.TESTING
        process = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        combined = (process.stdout or "") + "\n" + (process.stderr or "")
        log_path = self.workspace.log_unit(unit.unit_id, "test", combined.strip())
        unit.status = UnitStatus.TESTED if process.returncode == 0 else UnitStatus.REPAIRING
        return UnitExecutionResult(
            unit_id=unit.unit_id,
            status=unit.status,
            log_path=str(log_path),
            details={"returncode": process.returncode},
        )
