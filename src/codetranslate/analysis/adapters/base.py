from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ...core.models import (
    CallEdge,
    DataModelRecord,
    EntrypointRecord,
    IRNode,
    ModuleDependency,
    ProjectScanSummary,
    SourceFileRecord,
    SymbolRecord,
)


@dataclass(slots=True)
class ScanObservation:
    languages: set[str] = field(default_factory=set)
    frameworks: set[str] = field(default_factory=set)
    build_tools: set[str] = field(default_factory=set)
    dependency_managers: set[str] = field(default_factory=set)
    entrypoints: set[str] = field(default_factory=set)
    candidate_entrypoints: set[str] = field(default_factory=set)


@dataclass(slots=True)
class LanguageAnalysis:
    source_files: list[SourceFileRecord] = field(default_factory=list)
    module_dependencies: list[ModuleDependency] = field(default_factory=list)
    entrypoints: list[EntrypointRecord] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    models: list[DataModelRecord] = field(default_factory=list)
    call_graph: list[CallEdge] = field(default_factory=list)
    ir_nodes: list[IRNode] = field(default_factory=list)
    risk_nodes: list[str] = field(default_factory=list)
    project_insights: dict[str, Any] = field(default_factory=dict)


class LanguageAdapter(Protocol):
    language: str

    def detect_file(self, path: Path) -> bool: ...

    def scan_file(self, path: Path, project_root: Path) -> ScanObservation: ...

    def analyze_project(
        self, project_root: Path, scan: ProjectScanSummary
    ) -> LanguageAnalysis: ...
