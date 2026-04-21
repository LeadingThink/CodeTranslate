from __future__ import annotations

import unittest
from pathlib import Path

from codetranslate.analysis.adapters.java_adapter import JavaAdapter
from codetranslate.core.models import ProjectScanSummary


class JavaAdapterIntegrationTests(unittest.TestCase):
    def test_javaparser_bridge_analyzes_sample_project(self) -> None:
        project_root = Path("examples/java_sample").resolve()
        adapter = JavaAdapter()
        scan = ProjectScanSummary(
            project_root=str(project_root),
            source_directories=["src/main/java/com/example"],
            test_directories=[],
            resource_directories=[],
            config_files=[],
            languages=["java"],
            frameworks=[],
            build_tools=[],
            dependency_managers=[],
            entrypoints=[],
            candidate_entrypoints=[],
            files_scanned=2,
            maven_modules=[],
            test_files=[],
            resource_files=[],
        )

        analysis = adapter.analyze_project(project_root, scan)

        self.assertEqual(len(analysis.source_files), 2)
        self.assertTrue(any(symbol.name == "App" for symbol in analysis.symbols))
        self.assertTrue(any(entrypoint.module == "com.example.App" for entrypoint in analysis.entrypoints))
        self.assertTrue(
            any(
                dependency.source_module == "com.example.App"
                and dependency.target_module == "com.example.Service"
                for dependency in analysis.module_dependencies
            )
        )


if __name__ == "__main__":
    unittest.main()
