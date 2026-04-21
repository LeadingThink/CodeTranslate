from __future__ import annotations

import unittest
from unittest.mock import patch

from codetranslate.core.path_utils import (
    normalize_user_path,
    sanitize_path_component,
    sanitize_target_relative_path,
)


class NormalizeUserPathTests(unittest.TestCase):
    def test_windows_host_keeps_windows_drive_paths(self) -> None:
        with patch("codetranslate.core.path_utils.os.name", "nt"):
            path = normalize_user_path(r"D:\study\CodeTranslate")
        self.assertEqual(str(path), r"D:\study\CodeTranslate")

    def test_windows_host_accepts_wsl_mount_paths(self) -> None:
        with patch("codetranslate.core.path_utils.os.name", "nt"):
            path = normalize_user_path("/mnt/d/study/CodeTranslate")
        self.assertEqual(str(path), r"D:\study\CodeTranslate")

    def test_wsl_host_translates_windows_drive_paths(self) -> None:
        with (
            patch("codetranslate.core.path_utils.os.name", "posix"),
            patch("codetranslate.core.path_utils.platform.release", return_value="WSL2"),
        ):
            path = normalize_user_path(r"D:\study\CodeTranslate")
        self.assertEqual(str(path), "/mnt/d/study/CodeTranslate")


class SanitizeTargetPathTests(unittest.TestCase):
    def test_sanitize_path_component_replaces_invalid_characters(self) -> None:
        self.assertEqual(sanitize_path_component("validator-api"), "validator_api")

    def test_sanitize_target_relative_path_normalizes_directories_and_filename(self) -> None:
        path = sanitize_target_relative_path(
            "validator-api/src/main/java/net/pinnacle21/My-Class.py"
        )
        self.assertEqual(
            str(path).replace("\\", "/"),
            "validator_api/src/main/java/net/pinnacle21/My_Class.py",
        )


if __name__ == "__main__":
    unittest.main()
