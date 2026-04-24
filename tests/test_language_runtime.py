from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codetranslate.runtime.language_runtime import validate_source_file


class PythonImportContractValidationTests(unittest.TestCase):
    def test_rejects_java_style_python_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "Definition.py"
            file_path.write_text(
                "from net.pinnacle21.validator.util.KeyMap import KeyMap\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "invalid Java-style Python import"):
                validate_source_file(file_path, "python")

    def test_rejects_bare_sibling_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "AbstractOptions.py").write_text("class AbstractOptions: ...\n", encoding="utf-8")
            file_path = temp_root / "SourceOptions.py"
            file_path.write_text(
                "from AbstractOptions import AbstractOptions\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "invalid bare sibling import"):
                validate_source_file(file_path, "python")

    def test_rejects_dynamic_bridge_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "DiagnosticImpl.py"
            file_path.write_text(
                "\n".join(
                    [
                        "import importlib.util",
                        "spec = importlib.util.spec_from_file_location('x', 'y.py')",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "forbidden Python dependency bridge"):
                validate_source_file(file_path, "python")

    def test_allows_absolute_package_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "SourceOptions.py"
            file_path.write_text(
                "from validator_api.net.pinnacle21.validator.api.model.AbstractOptions import AbstractOptions\n",
                encoding="utf-8",
            )

            validate_source_file(file_path, "python")


if __name__ == "__main__":
    unittest.main()
