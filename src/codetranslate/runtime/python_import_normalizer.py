from __future__ import annotations

import ast
from pathlib import Path


def normalize_python_imports(path: Path, target_root: Path) -> bool:
    if path.suffix != ".py" or not path.exists():
        return False
    if not _is_under(path, target_root):
        return False

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    replacements = _collect_replacements(path, target_root, tree)
    if not replacements:
        return False

    lines = source.splitlines(keepends=True)
    changed = False
    for line_number, replacement in replacements.items():
        index = line_number - 1
        if index < 0 or index >= len(lines):
            continue
        newline = "\n" if lines[index].endswith("\n") else ""
        if lines[index] != f"{replacement}{newline}":
            lines[index] = f"{replacement}{newline}"
            changed = True

    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    return changed


def _collect_replacements(
    path: Path, target_root: Path, tree: ast.AST
) -> dict[int, str]:
    replacements: dict[int, str] = {}
    sibling_modules = _sibling_modules(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            replacement = _replacement_for_from_import(
                node, path, target_root, sibling_modules
            )
            if replacement:
                replacements[node.lineno] = replacement
        elif isinstance(node, ast.Import):
            replacement = _replacement_for_import(node, path, target_root)
            if replacement:
                replacements[node.lineno] = replacement
    return replacements


def _replacement_for_from_import(
    node: ast.ImportFrom,
    path: Path,
    target_root: Path,
    sibling_modules: set[str],
) -> str | None:
    module_name = node.module or ""
    if node.level != 0:
        return None

    resolved_module = None
    if module_name.startswith("net."):
        resolved_module = _resolve_java_module(module_name, target_root, path)
    elif "." not in module_name and module_name in sibling_modules:
        resolved_module = _module_name_for_path(path.parent / f"{module_name}.py", target_root)

    if not resolved_module:
        return None
    return f"from {resolved_module} import {_format_aliases(node.names)}"


def _replacement_for_import(
    node: ast.Import, path: Path, target_root: Path
) -> str | None:
    rewritten_aliases: list[str] = []
    changed = False
    for alias in node.names:
        module_name = alias.name
        resolved_module = (
            _resolve_java_module(module_name, target_root, path)
            if module_name.startswith("net.")
            else None
        )
        if resolved_module:
            rewritten_aliases.append(_format_alias(alias, resolved_module))
            changed = True
        else:
            rewritten_aliases.append(_format_alias(alias, module_name))
    if not changed:
        return None
    return f"import {', '.join(rewritten_aliases)}"


def _resolve_java_module(
    module_name: str, target_root: Path, current_path: Path
) -> str | None:
    relative_module = Path(*module_name.split(".")).with_suffix(".py")
    root = target_root.resolve()
    for top_level in _candidate_top_levels(root, current_path):
        if not top_level.is_dir():
            continue
        candidate = top_level / relative_module
        if candidate.exists():
            return _module_name_for_path(candidate, root)
    return None


def _candidate_top_levels(target_root: Path, current_path: Path) -> list[Path]:
    current_top_level = _current_top_level(target_root, current_path)
    candidates: list[Path] = []
    if current_top_level and current_top_level.exists():
        candidates.append(current_top_level)
    for top_level in sorted(target_root.iterdir(), key=lambda item: item.name):
        if current_top_level and top_level.resolve() == current_top_level.resolve():
            continue
        candidates.append(top_level)
    return candidates


def _current_top_level(target_root: Path, current_path: Path) -> Path | None:
    relative_path = current_path.resolve().relative_to(target_root.resolve())
    for part in relative_path.parts:
        return target_root / part
    return None


def _module_name_for_path(path: Path, target_root: Path) -> str:
    relative_path = path.resolve().relative_to(target_root.resolve())
    return ".".join(relative_path.with_suffix("").parts)


def _sibling_modules(path: Path) -> set[str]:
    return {
        child.stem
        for child in path.parent.glob("*.py")
        if child.stem != path.stem
    }


def _format_aliases(aliases: list[ast.alias]) -> str:
    return ", ".join(_format_alias(alias, alias.name) for alias in aliases)


def _format_alias(alias: ast.alias, module_name: str) -> str:
    if alias.asname:
        return f"{module_name} as {alias.asname}"
    return module_name


def _is_under(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    resolved_root = root.resolve()
    return resolved == resolved_root or resolved_root in resolved.parents
