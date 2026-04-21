from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codetranslate.core.models import MigrationRequest, ProjectPaths
from codetranslate.storage.workspace import WorkspaceManager


class WorkspaceManagerTests(unittest.TestCase):
    def test_stage_related_resources_is_idempotent_for_staged_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_root = temp_root / "source"
            workspace_root = temp_root / "workspace"
            target_root = temp_root / "output"
            resource_path = (
                source_root
                / "validator-core"
                / "src"
                / "main"
                / "resources"
                / "META-INF"
                / "services"
                / "net.pinnacle21.validator.api.DataValidator"
            )
            resource_path.parent.mkdir(parents=True, exist_ok=True)
            resource_path.write_text("net.pinnacle21.validator.ValidatorImpl\n")

            paths = ProjectPaths(
                source_root=str(source_root),
                workspace_root=str(workspace_root),
                target_root=str(target_root),
                request=MigrationRequest(
                    source_language="java",
                    target_language="python",
                ),
            )
            workspace = WorkspaceManager(paths)
            workspace.initialize()

            staged_once = workspace.stage_related_resources(
                [{"path": str(resource_path), "kind": "resource_file"}]
            )
            staged_twice = workspace.stage_related_resources(staged_once)

            self.assertEqual(len(staged_once), 1)
            self.assertEqual(staged_twice, staged_once)
            self.assertTrue(Path(staged_once[0]["path"]).exists())

    def test_resolve_target_destination_rejects_drive_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_root = temp_root / "source"
            workspace_root = temp_root / "workspace"
            target_root = temp_root / "output"
            source_root.mkdir()

            paths = ProjectPaths(
                source_root=str(source_root),
                workspace_root=str(workspace_root),
                target_root=str(target_root),
                request=MigrationRequest(
                    source_language="java",
                    target_language="python",
                ),
            )
            workspace = WorkspaceManager(paths)
            workspace.initialize()

            sample_source = source_root / "sample.txt"
            sample_source.write_text("sample")

            with self.assertRaises(ValueError):
                workspace.copy_file_to_target(sample_source, r"D:drive-relative.txt")


if __name__ == "__main__":
    unittest.main()
