from __future__ import annotations

import unittest
from unittest.mock import patch

from codetranslate.core.path_utils import normalize_user_path


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


if __name__ == "__main__":
    unittest.main()
