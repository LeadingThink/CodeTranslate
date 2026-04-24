from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codetranslate.analysis.context_builder import UnitContextBuilder
from codetranslate.core.models import (
    AnalysisResult,
    MigrationRequest,
    MigrationUnit,
    ProjectIR,
    ProjectPaths,
    ProjectScanSummary,
    UnitContext,
    UnitStatus,
)
from codetranslate.runtime.unit_executor import UnitExecutor
from codetranslate.storage.workspace import WorkspaceManager


class _FailingMigrator:
    def migrate(self, unit: MigrationUnit, context: UnitContext) -> None:
        raise RuntimeError("boom from migrate")


class _UnusedTester:
    def generate_test(self, unit: MigrationUnit, context: UnitContext) -> Path:
        raise AssertionError("generate_test should not be called")

    def run_test(self, unit: MigrationUnit, test_path: Path):
        raise AssertionError("run_test should not be called")


class _UnusedVerifier:
    def verify_unit(self, unit: MigrationUnit):
        raise AssertionError("verify_unit should not be called")


class _UnusedRepairer:
    def repair(self, unit: MigrationUnit, context: UnitContext, failure_log: str, test_path: Path) -> bool:
        raise AssertionError("repair should not be called")


class UnitExecutorFailureTests(unittest.TestCase):
    def test_execute_marks_unit_failed_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "source"
            workspace_root = root / "workspace"
            target_root = root / "target"
            source_root.mkdir()
            workspace_root.mkdir()
            target_root.mkdir()

            source_file = source_root / "A.java"
            source_file.write_text("class A {}", encoding="utf-8")

            unit = MigrationUnit(
                unit_id="module.a",
                symbol_id="module.a:file",
                name="A",
                language="java",
                target_language="python",
                module="module.a",
                file_path=str(source_file),
                target_file_path=str(target_root / "A.py"),
                kind="file",
                source_code="class A {}",
                signature="file module.a",
                status=UnitStatus.READY,
            )
            analysis = AnalysisResult(
                project_root=str(source_root),
                scan=ProjectScanSummary(
                    project_root=str(source_root),
                    source_directories=[],
                    test_directories=[],
                    resource_directories=[],
                    config_files=[],
                    languages=["java"],
                    frameworks=[],
                    build_tools=[],
                    dependency_managers=[],
                    entrypoints=[],
                    candidate_entrypoints=[],
                    files_scanned=1,
                ),
                source_files=[],
                module_dependencies=[],
                entrypoints=[],
                symbols=[],
                models=[],
                call_graph=[],
                ir=ProjectIR(nodes=[], edges=[]),
                risk_nodes=[],
                project_insights={},
            )
            workspace = WorkspaceManager(
                ProjectPaths(
                    source_root=str(source_root),
                    workspace_root=str(workspace_root),
                    target_root=str(target_root),
                    request=MigrationRequest(
                        source_language="java",
                        target_language="python",
                    ),
                )
            )
            workspace.initialize()

            executor = UnitExecutor(
                context_builder=UnitContextBuilder(),
                migrator=_FailingMigrator(),
                tester=_UnusedTester(),
                verifier=_UnusedVerifier(),
                repairer=_UnusedRepairer(),
                workspace=workspace,
            )

            result = executor.execute(unit, analysis, {unit.unit_id: unit})

            self.assertFalse(result)
            self.assertEqual(unit.status, UnitStatus.FAILED)
            self.assertIn("boom from migrate", unit.failure_reason)


if __name__ == "__main__":
    unittest.main()
