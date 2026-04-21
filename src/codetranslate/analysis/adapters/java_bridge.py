from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class JavaParserBridge:
    def __init__(self, bridge_root: Path | None = None) -> None:
        self.bridge_root = bridge_root or Path(__file__).resolve().parents[4] / "java_parser_bridge"

    def analyze_project(self, project_root: Path) -> dict[str, Any]:
        jar_path = self._ensure_bridge_built()
        command = [
            self._resolve_executable("java"),
            "-jar",
            str(jar_path),
            "--project-root",
            str(project_root),
        ]
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=240,
        )
        if process.returncode != 0:
            message = process.stderr.strip() or process.stdout.strip() or "java parser bridge failed"
            raise RuntimeError(message)
        return json.loads(process.stdout)

    def _ensure_bridge_built(self) -> Path:
        jar_path = self.bridge_root / "target" / "java-parser-bridge-0.1.0-jar-with-dependencies.jar"
        source_files = list(self.bridge_root.rglob("*.java")) + [self.bridge_root / "pom.xml"]
        if jar_path.exists() and all(jar_path.stat().st_mtime >= path.stat().st_mtime for path in source_files):
            return jar_path

        process = subprocess.run(
            [self._resolve_executable("mvn"), "-q", "-DskipTests", "package"],
            cwd=self.bridge_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=240,
        )
        if process.returncode != 0:
            message = process.stderr.strip() or process.stdout.strip() or "failed to build java parser bridge"
            raise RuntimeError(message)
        if not jar_path.exists():
            raise RuntimeError(f"expected bridge jar was not produced: {jar_path}")
        return jar_path

    def _resolve_executable(self, name: str) -> str:
        candidates = [name]
        if name == "mvn":
            candidates.extend(["mvn.cmd", "mvn.bat"])
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        raise RuntimeError(f"required executable not found in PATH: {name}")
