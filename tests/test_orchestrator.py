from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codetranslate.core.models import (
    AnalysisResult,
    MigrationRequest,
    MigrationUnit,
    ProjectIR,
    ProjectPaths,
    ProjectScanSummary,
    SourceFileRecord,
)
from codetranslate.core.models import UnitStatus
from codetranslate.engine.orchestrator import MigrationOrchestrator


def _build_unit(
    unit_id: str,
    file_name: str,
    dependencies: list[str],
    dependents: list[str],
    status: UnitStatus = UnitStatus.ANALYZED,
) -> MigrationUnit:
    return MigrationUnit(
        unit_id=unit_id,
        symbol_id=f"{unit_id}:__file__",
        name=file_name.removesuffix(".java"),
        language="java",
        target_language="python",
        module=unit_id,
        file_path=file_name,
        target_file_path=f"out/{file_name.removesuffix('.java')}.py",
        kind="file",
        source_code="class Sample {}",
        signature=f"file {unit_id}",
        dependencies=dependencies,
        dependents=dependents,
        status=status,
    )


class CriticalChainTests(unittest.TestCase):
    def setUp(self) -> None:
        paths = ProjectPaths(
            source_root="source",
            workspace_root="workspace",
            target_root="target",
            request=MigrationRequest(source_language="java", target_language="python"),
        )
        self.orchestrator = MigrationOrchestrator(paths)

    def test_critical_chain_handles_cycle(self) -> None:
        unit_a = _build_unit(
            unit_id="module.a",
            file_name="A.java",
            dependencies=["module.b"],
            dependents=["module.b"],
        )
        unit_b = _build_unit(
            unit_id="module.b",
            file_name="B.java",
            dependencies=["module.a"],
            dependents=["module.a"],
        )

        chain = self.orchestrator._critical_chain(
            [unit_a, unit_b], {unit.unit_id: unit for unit in [unit_a, unit_b]}
        )

        self.assertTrue(chain)
        self.assertIn("A.java", chain)

    def test_critical_chain_skips_verified_units(self) -> None:
        unit_a = _build_unit(
            unit_id="module.a",
            file_name="A.java",
            dependencies=[],
            dependents=["module.b"],
            status=UnitStatus.VERIFIED,
        )
        unit_b = _build_unit(
            unit_id="module.b",
            file_name="B.java",
            dependencies=["module.a"],
            dependents=[],
        )

        chain = self.orchestrator._critical_chain(
            [unit_a, unit_b], {unit.unit_id: unit for unit in [unit_a, unit_b]}
        )

        self.assertEqual(chain, "B.java")


class PlanRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        self.project_root = temp_root / "validator"
        self.workspace_root = temp_root / "workspace"
        self.target_root = temp_root / "output"
        self.project_root.mkdir()
        self.workspace_root.mkdir()
        self.target_root.mkdir()
        self.orchestrator = MigrationOrchestrator(
            ProjectPaths(
                source_root=str(self.project_root),
                workspace_root=str(self.workspace_root),
                target_root=str(self.target_root),
                request=MigrationRequest(
                    source_language="java",
                    target_language="python",
                ),
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_plan_is_rebuilt_when_analysis_discovers_sibling_source_files(self) -> None:
        main_file = self.project_root / "validator-core" / "src" / "main" / "java" / "A.java"
        sibling_file = (
            self.project_root.parent
            / "validator-api"
            / "src"
            / "main"
            / "java"
            / "B.java"
        )
        main_file.parent.mkdir(parents=True, exist_ok=True)
        sibling_file.parent.mkdir(parents=True, exist_ok=True)
        main_file.write_text("class A {}", encoding="utf-8")
        sibling_file.write_text("class B {}", encoding="utf-8")

        stale_unit = _build_unit(
            unit_id="module.a",
            file_name=str(main_file),
            dependencies=[],
            dependents=[],
            status=UnitStatus.READY,
        )
        self.orchestrator.workspace.save_units([stale_unit])
        self.orchestrator.workspace.save_unit_statuses([stale_unit])

        analysis = AnalysisResult(
            project_root=str(self.project_root),
            scan=ProjectScanSummary(
                project_root=str(self.project_root),
                source_directories=[],
                test_directories=[],
                resource_directories=[],
                config_files=[],
                languages=["java"],
                frameworks=[],
                build_tools=["maven"],
                dependency_managers=["maven"],
                entrypoints=[],
                candidate_entrypoints=[],
                files_scanned=2,
            ),
            source_files=[
                SourceFileRecord(
                    path="validator-core/src/main/java/A.java",
                    language="java",
                    module="module.a",
                    role="source",
                ),
                SourceFileRecord(
                    path="validator-api/src/main/java/B.java",
                    language="java",
                    module="module.b",
                    role="source",
                ),
            ],
            module_dependencies=[],
            entrypoints=[],
            symbols=[],
            models=[],
            call_graph=[],
            ir=ProjectIR(nodes=[], edges=[]),
            risk_nodes=[],
            project_insights={},
        )

        rebuilt_units = self.orchestrator._load_or_create_plan(analysis)

        self.assertEqual(len(rebuilt_units), 2)
        self.assertEqual({unit.module for unit in rebuilt_units}, {"module.a", "module.b"})


if __name__ == "__main__":
    unittest.main()
