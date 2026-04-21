from __future__ import annotations

import unittest

from codetranslate.core.models import MigrationRequest, MigrationUnit, ProjectPaths
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


if __name__ == "__main__":
    unittest.main()
