from __future__ import annotations

from pathlib import Path

from ..core.models import MigrationRequest, ProjectScanSummary
from .language_registry import LanguageRegistry
from .language_specs import detect_languages_from_config


class ProjectScanner:
    def __init__(self, registry: LanguageRegistry | None = None) -> None:
        self.registry = registry or LanguageRegistry()

    def scan(self, project_root: str, request: MigrationRequest) -> ProjectScanSummary:
        root = Path(project_root).resolve()
        source_directories: set[str] = set()
        test_directories: set[str] = set()
        config_files: list[str] = []
        languages: set[str] = set()
        frameworks: set[str] = set()
        build_tools: set[str] = set()
        dependency_managers: set[str] = set()
        entrypoints: set[str] = set()
        candidate_entrypoints: set[str] = set()
        files_scanned = 0

        for path in root.rglob("*"):
            if any(part.startswith(".git") or part == "__pycache__" or part == ".venv" for part in path.parts):
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if request.include_paths and not any(
                relative == include or relative.startswith(include.rstrip("/") + "/")
                for include in request.include_paths
            ):
                continue
            if any(
                relative == exclude or relative.startswith(exclude.rstrip("/") + "/")
                for exclude in request.exclude_paths
            ):
                continue

            files_scanned += 1
            parts = set(path.parts)

            if detect_languages_from_config(path.name):
                config_files.append(relative)
                for language in detect_languages_from_config(path.name):
                    if language != request.source_language:
                        continue
                    adapter = self.registry.adapter_for_language(language)
                    if adapter is not None:
                        languages.add(language)

            if "test" in relative or "tests" in parts:
                test_directories.add(path.parent.relative_to(root).as_posix())
            else:
                source_directories.add(path.parent.relative_to(root).as_posix())

            adapter = self.registry.adapter_for_path(path)
            if adapter is None:
                continue
            if getattr(adapter, "language", None) != request.source_language:
                continue
            observation = adapter.scan_file(path, root)
            languages.update(observation.languages)
            frameworks.update(observation.frameworks)
            build_tools.update(observation.build_tools)
            dependency_managers.update(observation.dependency_managers)
            entrypoints.update(observation.entrypoints)
            candidate_entrypoints.update(observation.candidate_entrypoints)

        return ProjectScanSummary(
            project_root=str(root),
            source_directories=sorted(source_directories),
            test_directories=sorted(test_directories),
            config_files=sorted(config_files),
            languages=sorted(languages),
            frameworks=sorted(frameworks),
            build_tools=sorted(build_tools),
            dependency_managers=sorted(dependency_managers),
            entrypoints=sorted(set(entrypoints).union(request.entry_hints)),
            candidate_entrypoints=sorted(set(candidate_entrypoints).union(request.entry_hints)),
            files_scanned=files_scanned,
        )
