from __future__ import annotations

from pathlib import Path

from ...core.models import ProjectScanSummary
from ..language_specs import LANGUAGE_SPECS
from .base import LanguageAnalysis, ScanObservation


class GenericAdapter:
    def __init__(self, language: str) -> None:
        self.language = language
        self.spec = LANGUAGE_SPECS[language]

    def detect_file(self, path: Path) -> bool:
        return path.suffix in self.spec.extensions

    def scan_file(self, path: Path, project_root: Path) -> ScanObservation:
        relative = path.relative_to(project_root).as_posix()
        observation = ScanObservation(
            languages={self.language},
            build_tools=set(self.spec.build_tools),
            dependency_managers=set(self.spec.dependency_managers),
            frameworks=set(self.spec.frameworks),
        )
        if path.name in self.spec.entrypoint_filenames:
            observation.entrypoints.add(relative)
        return observation

    def analyze_project(self, project_root: Path, scan: ProjectScanSummary) -> LanguageAnalysis:
        return LanguageAnalysis()
