from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


def validate_source_file(path: Path, language: str) -> None:
    if language == "python":
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        _validate_python_import_contract(path, source, tree)
        compile(source, str(path), "exec")
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


def _validate_python_import_contract(path: Path, source: str, tree: ast.AST) -> None:
    if _is_generated_test_path(path):
        return
    _reject_dynamic_bridge_strategy(path, source)
    _reject_invalid_import_patterns(path, tree)


def _is_generated_test_path(path: Path) -> bool:
    return "generated_tests" in {part.casefold() for part in path.parts}


def _reject_dynamic_bridge_strategy(path: Path, source: str) -> None:
    forbidden_tokens = (
        "spec_from_file_location",
        "module_from_spec",
        "exec_module(",
        "sys.path.insert(",
        "sys.path.append(",
    )
    for token in forbidden_tokens:
        if token in source:
            raise ValueError(
                f"forbidden Python dependency bridge in {path}: {token}"
            )


def _reject_invalid_import_patterns(path: Path, tree: ast.AST) -> None:
    sibling_modules = {child.stem for child in path.parent.glob("*.py")}
    sibling_modules.discard(path.stem)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if node.level == 0 and module_name.startswith("net."):
                raise ValueError(
                    f"invalid Java-style Python import in {path}: from {module_name} import ..."
                )
            if node.level == 0 and module_name and "." not in module_name and module_name in sibling_modules:
                raise ValueError(
                    f"invalid bare sibling import in {path}: from {module_name} import ..."
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name
                if module_name.startswith("net."):
                    raise ValueError(
                        f"invalid Java-style Python import in {path}: import {module_name}"
                    )
                if "." not in module_name and module_name in sibling_modules:
                    raise ValueError(
                        f"invalid bare sibling import in {path}: import {module_name}"
                    )
