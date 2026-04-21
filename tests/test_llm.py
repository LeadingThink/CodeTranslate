from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codetranslate.core.models import MigrationRequest, ProjectPaths
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


if __name__ == "__main__":
    unittest.main()
