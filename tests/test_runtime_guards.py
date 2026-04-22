from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codetranslate.core.models import MigrationRequest, MigrationUnit, ProjectPaths
from codetranslate.core.models import UnitStatus
from codetranslate.runtime.llm import (
    AgentContext,
    LLMClient,
    _resolve_output_dir_path,
    _resolve_output_path,
)
from codetranslate.runtime.unit_state import UnitStateMachine


def _build_paths(root: Path) -> ProjectPaths:
    return ProjectPaths(
        source_root=str(root / "source"),
        workspace_root=str(root / "workspace"),
        target_root=str(root / "target"),
        request=MigrationRequest(source_language="java", target_language="python"),
    )


def _build_unit(
    unit_id: str,
    target_file_path: str,
    dependents: list[str],
    status: UnitStatus = UnitStatus.VERIFIED,
) -> MigrationUnit:
    return MigrationUnit(
        unit_id=unit_id,
        symbol_id=f"{unit_id}:file",
        name=unit_id,
        language="java",
        target_language="python",
        module=unit_id,
        file_path=f"{unit_id}.java",
        target_file_path=target_file_path,
        kind="file",
        source_code="class Sample {}",
        signature=f"file {unit_id}",
        dependents=dependents,
        status=status,
        verified_output_signatures={target_file_path: "before"},
    )


class OutputPathGuardTests(unittest.TestCase):
    def test_write_path_must_match_current_unit_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _build_paths(root)
            target_file = (Path(paths.target_root) / "pkg" / "Allowed.py").resolve()
            target_file.parent.mkdir(parents=True, exist_ok=True)
            context = AgentContext(
                paths=paths,
                allowed_write_paths=[str(target_file)],
            )

            resolved = _resolve_output_path(str(target_file), context)
            self.assertEqual(resolved, target_file)

            with self.assertRaises(ValueError):
                _resolve_output_path(
                    str(target_file.with_name("Other.py")),
                    context,
                )

    def test_mkdir_allows_parent_directories_of_owned_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _build_paths(root)
            target_file = (
                Path(paths.target_root) / "validator_core" / "net" / "pkg" / "File.py"
            ).resolve()
            context = AgentContext(
                paths=paths,
                allowed_write_paths=[str(target_file)],
            )

            self.assertEqual(
                _resolve_output_dir_path(str(target_file.parent), context),
                target_file.parent,
            )
            self.assertEqual(
                _resolve_output_dir_path(str(target_file.parent.parent), context),
                target_file.parent.parent,
            )

            with self.assertRaises(ValueError):
                _resolve_output_dir_path(
                    str(target_file.parents[3] / "other"),
                    context,
                )


class JavaPythonGuardrailTests(unittest.TestCase):
    def test_java_python_prompt_includes_enum_and_constructor_requirements(self) -> None:
        client = object.__new__(LLMClient)
        requirements = client._language_specific_requirements("java", "python")

        self.assertIn("preserve unique enum member values", requirements)
        self.assertIn("Preserve overloaded Java constructors", requirements)
        self.assertIn("do not replace unconditional `put` behavior", requirements)


class VerifiedUnitInvalidationTests(unittest.TestCase):
    def test_changed_verified_unit_invalidates_itself_and_dependents(self) -> None:
        state_machine = UnitStateMachine()
        unit_a = _build_unit("module.a", "target/A.py", ["module.b"])
        unit_b = _build_unit("module.b", "target/B.py", [], status=UnitStatus.VERIFIED)
        unit_b.dependencies = ["module.a"]

        invalidated = state_machine.invalidate_stale_verified_units(
            [unit_a, unit_b],
            {
                "module.a": {"target/A.py": "after"},
                "module.b": {"target/B.py": "before"},
            },
        )

        self.assertEqual(invalidated, ["module.a", "module.b"])
        self.assertEqual(unit_a.status, UnitStatus.DISCOVERED)
        self.assertEqual(unit_b.status, UnitStatus.DISCOVERED)
        self.assertEqual(unit_a.verified_output_signatures, {})
        self.assertEqual(unit_b.verified_output_signatures, {})


if __name__ == "__main__":
    unittest.main()
