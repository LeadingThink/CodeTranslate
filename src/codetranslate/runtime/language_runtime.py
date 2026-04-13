from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def validate_source_file(path: Path, language: str) -> None:
    if language == "python":
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
        return
    if language == "nodejs":
        if path.suffix == ".ts":
            source = path.read_text(encoding="utf-8")
            if not source.strip():
                raise ValueError(f"empty TypeScript file: {path}")
            return
        process = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, check=False)
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or process.stdout.strip() or f"node --check failed for {path}")
        return


def run_test_file(path: Path, language: str) -> subprocess.CompletedProcess[str]:
    if language == "nodejs":
        return subprocess.run(["node", str(path)], capture_output=True, text=True, check=False)
    return subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=False)
