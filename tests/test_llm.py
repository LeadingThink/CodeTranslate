from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codetranslate.core.models import MigrationRequest, ProjectPaths, UnitContext
from codetranslate.runtime.llm import LLMClient


class _RetryingAgent:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.calls: list[str] = []

    def invoke(self, payload: dict[str, object], context: object) -> dict[str, object]:
        messages = payload["messages"]
        message = messages[0]["content"]
        assert isinstance(message, str)
        self.calls.append(message)
        if len(self.calls) == 1:
            raise FileNotFoundError("file does not exist: missing.py")
        self.output_path.write_text("print('ok')\n", encoding="utf-8")
        return {
            "messages": [SimpleNamespace(content="Recovered and wrote the file.")],
        }


class _InvalidThenValidAgent:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.calls: list[str] = []

    def invoke(self, payload: dict[str, object], context: object) -> dict[str, object]:
        messages = payload["messages"]
        message = messages[0]["content"]
        assert isinstance(message, str)
        self.calls.append(message)
        if len(self.calls) == 1:
            self.output_path.write_text(
                "import importlib.util\n"
                "spec = importlib.util.spec_from_file_location('x', 'x.py')\n",
                encoding="utf-8",
            )
        else:
            self.output_path.write_text("print('ok')\n", encoding="utf-8")
        return {"messages": [SimpleNamespace(content="Wrote the file.")]}


class LLMClientRetryTests(unittest.TestCase):
    def test_run_agent_retries_with_error_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            output_path = temp_root / "generated.py"
            client = object.__new__(LLMClient)
            client.paths = ProjectPaths(
                source_root=str(temp_root / "source"),
                workspace_root=str(temp_root / "workspace"),
                target_root=str(temp_root / "target"),
                request=MigrationRequest(
                    source_language="java",
                    target_language="python",
                ),
            )
            client.agent = _RetryingAgent(output_path)
            client.model = SimpleNamespace()

            with patch("codetranslate.runtime.llm.get_reporter") as reporter_mock:
                reporter_mock.return_value = SimpleNamespace(
                    model=lambda *args, **kwargs: None
                )
                result = client._run_agent("Generate a test file.", [str(output_path)])

            self.assertEqual(result, "Recovered and wrote the file.")
            self.assertEqual(len(client.agent.calls), 2)
            self.assertIn("Previous attempt failed", client.agent.calls[1])
            self.assertIn("file does not exist: missing.py", client.agent.calls[1])
            self.assertTrue(output_path.exists())

    def test_run_agent_retries_when_required_file_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            target_root = temp_root / "target"
            target_root.mkdir()
            output_path = target_root / "generated.py"
            client = object.__new__(LLMClient)
            client.paths = ProjectPaths(
                source_root=str(temp_root / "source"),
                workspace_root=str(temp_root / "workspace"),
                target_root=str(target_root),
                request=MigrationRequest(
                    source_language="java",
                    target_language="python",
                ),
            )
            client.agent = _InvalidThenValidAgent(output_path)
            client.model = SimpleNamespace()

            with patch("codetranslate.runtime.llm.get_reporter") as reporter_mock:
                reporter_mock.return_value = SimpleNamespace(
                    model=lambda *args, **kwargs: None
                )
                result = client._run_agent("Generate a source file.", [str(output_path)])

            self.assertEqual(result, "Wrote the file.")
            self.assertEqual(len(client.agent.calls), 2)
            self.assertIn("forbidden Python dependency bridge", client.agent.calls[1])
            self.assertEqual(output_path.read_text(encoding="utf-8"), "print('ok')\n")

    def test_migration_task_explicitly_forbids_java_style_python_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            client = object.__new__(LLMClient)
            client.paths = ProjectPaths(
                source_root=str(temp_root / "source"),
                workspace_root=str(temp_root / "workspace"),
                target_root=str(temp_root / "target"),
                request=MigrationRequest(
                    source_language="java",
                    target_language="python",
                ),
            )

            context = UnitContext(
                unit_id="sample.unit",
                source_code="import a.b.C;",
                source_file_content="import a.b.C;",
                signature="file sample.unit",
                summary="java file Sample from module sample.unit",
                module_imports=["import a.b.C;"],
                dependency_targets=[
                    {
                        "unit_id": "dep.unit",
                        "name": "C",
                        "module": "a.b.C",
                        "target_path": str(temp_root / "target" / "pkg" / "c.py"),
                    }
                ],
                decorators=[],
                module_level_context="execution_unit=file module=sample.unit",
                input_models=[],
                output_models=[],
                direct_dependencies=["dep.unit"],
                dependency_summaries=["C: migrated to pkg/c.py"],
                target_file_path=str(temp_root / "target" / "pkg" / "sample.py"),
                target_file_paths=[str(temp_root / "target" / "pkg" / "sample.py")],
                target_constraints={
                    "source_language": "java",
                    "language": "python",
                    "strategy": "high-fidelity incremental migration",
                    "preserve_behavior": True,
                },
                test_requirements=[],
            )

            task = client._build_migration_task(context)

            self.assertIn("Dependency target files:", task)
            self.assertIn("do not keep Java package/import syntax", task)
            self.assertIn("Do not emit imports like `from net... import ...`", task)
            self.assertIn("Use a single Python import convention", task)
            self.assertIn("spec_from_file_location", task)
            self.assertIn("Current Python module path must be", task)
            self.assertIn('"module": "pkg.c"', task)


if __name__ == "__main__":
    unittest.main()
