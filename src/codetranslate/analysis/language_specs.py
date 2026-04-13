from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    config_files: tuple[str, ...] = ()
    build_tools: tuple[str, ...] = ()
    dependency_managers: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    entrypoint_filenames: tuple[str, ...] = ()


LANGUAGE_SPECS: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        extensions=(".py",),
        config_files=("pyproject.toml", "requirements.txt"),
        build_tools=("uv",),
        dependency_managers=("uv",),
        entrypoint_filenames=("main.py", "app.py", "manage.py", "__main__.py"),
    ),
    "java": LanguageSpec(
        name="java",
        extensions=(".java",),
        config_files=("pom.xml", "build.gradle"),
        build_tools=("maven", "gradle"),
        dependency_managers=("maven", "gradle"),
    ),
    "go": LanguageSpec(
        name="go",
        extensions=(".go",),
        config_files=("go.mod",),
        build_tools=("go",),
        dependency_managers=("go",),
        entrypoint_filenames=("main.go",),
    ),
    "rust": LanguageSpec(
        name="rust",
        extensions=(".rs",),
        config_files=("Cargo.toml",),
        build_tools=("cargo",),
        dependency_managers=("cargo",),
        entrypoint_filenames=("main.rs", "lib.rs"),
    ),
    "nodejs": LanguageSpec(
        name="nodejs",
        extensions=(".js", ".mjs", ".cjs", ".ts"),
        config_files=("package.json",),
        build_tools=("npm", "pnpm", "yarn"),
        dependency_managers=("npm", "pnpm", "yarn"),
        entrypoint_filenames=("index.js", "main.js", "app.js", "server.js"),
    ),
}


def detect_language_by_suffix(filename: str) -> str | None:
    for spec in LANGUAGE_SPECS.values():
        if filename.endswith(spec.extensions):
            return spec.name
    return None


def detect_languages_from_config(filename: str) -> list[str]:
    matched = []
    for spec in LANGUAGE_SPECS.values():
        if filename in spec.config_files:
            matched.append(spec.name)
    return matched
