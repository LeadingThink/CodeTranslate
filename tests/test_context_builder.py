from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codetranslate.analysis.context_builder import UnitContextBuilder
from codetranslate.core.models import (
    AnalysisResult,
    MigrationUnit,
    ProjectIR,
    ProjectScanSummary,
)


class UnitContextBuilderTests(unittest.TestCase):
    def test_build_includes_dependency_target_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_file = temp_root / "ValidationEvents.java"
            source_file.write_text(
                "package net.pinnacle21.validator.util;\n"
                "import net.pinnacle21.validator.api.events.ValidationEvent;\n",
                encoding="utf-8",
            )

            unit = MigrationUnit(
                unit_id="net__pinnacle21__validator__util__ValidationEvents__file",
                symbol_id="net__pinnacle21__validator__util__ValidationEvents__file:__file__",
                name="ValidationEvents",
                language="java",
                target_language="python",
                module="net.pinnacle21.validator.util.ValidationEvents",
                file_path=str(source_file),
                target_file_path=str(temp_root / "output" / "ValidationEvents.py"),
                kind="file",
                source_code=source_file.read_text(encoding="utf-8"),
                signature="file net.pinnacle21.validator.util.ValidationEvents",
                dependencies=["validation_event_dep"],
            )
            dependency = MigrationUnit(
                unit_id="validation_event_dep",
                symbol_id="validation_event_dep:__file__",
                name="ValidationEvent",
                language="java",
                target_language="python",
                module="net.pinnacle21.validator.api.events.ValidationEvent",
                file_path=str(temp_root / "ValidationEvent.java"),
                target_file_path=str(temp_root / "output" / "ValidationEvent.py"),
                kind="file",
                source_code="public interface ValidationEvent {}",
                signature="file net.pinnacle21.validator.api.events.ValidationEvent",
            )
            analysis = AnalysisResult(
                project_root=str(temp_root),
                scan=ProjectScanSummary(
                    project_root=str(temp_root),
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
                    maven_modules=[],
                    test_files=[],
                    resource_files=[],
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

            context = UnitContextBuilder().build(
                unit, analysis, {unit.unit_id: unit, dependency.unit_id: dependency}
            )

            self.assertEqual(context.module_imports, ["import net.pinnacle21.validator.api.events.ValidationEvent;"])
            self.assertEqual(
                context.dependency_targets,
                [
                    {
                        "unit_id": "validation_event_dep",
                        "name": "ValidationEvent",
                        "module": "net.pinnacle21.validator.api.events.ValidationEvent",
                        "target_path": str(temp_root / "output" / "ValidationEvent.py"),
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
