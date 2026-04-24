from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codetranslate.runtime.language_runtime import validate_source_file
from codetranslate.runtime.python_import_normalizer import normalize_python_imports


class PythonImportNormalizerTests(unittest.TestCase):
    def test_rewrites_java_style_import_to_existing_target_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dependency = (
                root
                / "validator_core"
                / "net"
                / "pinnacle21"
                / "validator"
                / "util"
                / "KeyMap.py"
            )
            dependency.parent.mkdir(parents=True)
            dependency.write_text("class KeyMap: ...\n", encoding="utf-8")
            target = (
                root
                / "validator_core"
                / "net"
                / "pinnacle21"
                / "validator"
                / "settings"
                / "Definition.py"
            )
            target.parent.mkdir(parents=True)
            target.write_text(
                "from net.pinnacle21.validator.util.KeyMap import KeyMap\n",
                encoding="utf-8",
            )

            self.assertTrue(normalize_python_imports(target, root))

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "from validator_core.net.pinnacle21.validator.util.KeyMap import KeyMap\n",
            )
            validate_source_file(target, "python")

    def test_rewrites_bare_sibling_import_to_package_root_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = (
                root
                / "validator_api"
                / "net"
                / "pinnacle21"
                / "validator"
                / "api"
                / "model"
            )
            package.mkdir(parents=True)
            (package / "AbstractOptions.py").write_text(
                "class AbstractOptions: ...\nclass AbstractBuilder: ...\n",
                encoding="utf-8",
            )
            target = package / "SourceOptions.py"
            target.write_text(
                "from AbstractOptions import AbstractBuilder, AbstractOptions\n",
                encoding="utf-8",
            )

            self.assertTrue(normalize_python_imports(target, root))

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "from validator_api.net.pinnacle21.validator.api.model.AbstractOptions import AbstractBuilder, AbstractOptions\n",
            )
            validate_source_file(target, "python")

    def test_prefers_current_top_level_package_for_java_style_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for package_name in ("validator_core", "validator_data"):
                dependency = (
                    root
                    / package_name
                    / "net"
                    / "pinnacle21"
                    / "validator"
                    / "util"
                    / "Values.py"
                )
                dependency.parent.mkdir(parents=True)
                dependency.write_text("class Values: ...\n", encoding="utf-8")

            target = (
                root
                / "validator_data"
                / "net"
                / "pinnacle21"
                / "validator"
                / "data"
                / "SasTransportDataSource.py"
            )
            target.parent.mkdir(parents=True)
            target.write_text(
                "from net.pinnacle21.validator.util.Values import Values\n",
                encoding="utf-8",
            )

            self.assertTrue(normalize_python_imports(target, root))

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "from validator_data.net.pinnacle21.validator.util.Values import Values\n",
            )
            validate_source_file(target, "python")


if __name__ == "__main__":
    unittest.main()
