from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codetranslate.app.interactive import ConsoleReporter, _create_prompt_session, _prompt


class InteractivePromptTests(unittest.TestCase):
    def test_create_prompt_session_uses_file_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / ".codetranslate_history"
            session_instance = Mock()
            with (
                patch("codetranslate.app.interactive._HISTORY_FILE", history_path),
                patch(
                    "codetranslate.app.interactive.PromptSession",
                    return_value=session_instance,
                ) as prompt_session,
            ):
                session = _create_prompt_session()

        self.assertIs(session, session_instance)
        prompt_session.assert_called_once()
        history = prompt_session.call_args.kwargs["history"]
        self.assertEqual(history.filename, str(history_path))

    def test_prompt_returns_explicit_value(self) -> None:
        session = Mock()
        session.prompt.return_value = " python "

        value = _prompt(session, "Source language")

        self.assertEqual(value, "python")
        session.prompt.assert_called_once_with("Source language: ", default="")

    def test_prompt_falls_back_to_default_on_empty_input(self) -> None:
        session = Mock()
        session.prompt.return_value = "   "

        value = _prompt(session, "Action [analyze|plan|run]", "run")

        self.assertEqual(value, "run")
        session.prompt.assert_called_once_with(
            "Action [analyze|plan|run] [run]: ", default="run"
        )

    def test_console_reporter_model_includes_token_usage(self) -> None:
        reporter = ConsoleReporter()

        with patch("builtins.print") as print_mock:
            reporter.model(
                "response",
                "Done.\nextra lines are ignored",
                token_usage={
                    "input_tokens": 120,
                    "output_tokens": 45,
                    "total_tokens": 165,
                },
            )

        self.assertEqual(print_mock.call_count, 1)
        printed = print_mock.call_args.args[0]
        self.assertIn("[model] response | Done.", printed)
        self.assertIn("tokens in=120 out=45 total=165", printed)


if __name__ == "__main__":
    unittest.main()
