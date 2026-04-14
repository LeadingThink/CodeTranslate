from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class UnitStatus(str, Enum):
    DISCOVERED = "discovered"
    ANALYZED = "analyzed"
    READY = "ready"
    GENERATING = "generating"
    GENERATED = "generated"
    TESTING = "testing"
    TESTED = "tested"
    VERIFIED = "verified"
    REPAIRING = "repairing"
    FAILED = "failed"
    BLOCKED = "blocked"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(slots=True)
class MigrationRequest:
    source_language: str
    target_language: str
    entry_hints: list[str] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectPaths:
    source_root: str
    workspace_root: str
    target_root: str
    request: MigrationRequest


@dataclass(slots=True)
class MavenModuleRecord:
    name: str
    relative_path: str
    pom_path: str
    packaging: str = "jar"
    parent: str | None = None
    dependencies: list[str] = field(default_factory=list)
    source_roots: list[str] = field(default_factory=list)
    test_roots: list[str] = field(default_factory=list)
    resource_roots: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectScanSummary:
    project_root: str
    source_directories: list[str]
    test_directories: list[str]
    resource_directories: list[str]
    config_files: list[str]
    languages: list[str]
    frameworks: list[str]
    build_tools: list[str]
    dependency_managers: list[str]
    entrypoints: list[str]
    candidate_entrypoints: list[str]
    files_scanned: int
    maven_modules: list[MavenModuleRecord] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    resource_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceFileRecord:
    path: str
    language: str
    module: str
    role: str
    project_module: str | None = None


@dataclass(slots=True)
class ModuleDependency:
    source_module: str
    target_module: str
    language: str
    import_kind: str
    symbols: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EntrypointRecord:
    path: str
    language: str
    kind: str
    module: str


@dataclass(slots=True)
class SymbolRecord:
    symbol_id: str
    name: str
    qualname: str
    kind: str
    language: str
    module: str
    file_path: str
    line_start: int
    line_end: int
    signature: str | None = None
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    docstring: str | None = None


@dataclass(slots=True)
class ModelField:
    name: str
    annotation: str | None
    default: str | None


@dataclass(slots=True)
class DataModelRecord:
    model_id: str
    name: str
    language: str
    module: str
    file_path: str
    fields: list[ModelField]


@dataclass(slots=True)
class CallEdge:
    source: str
    target: str
    kind: str = "call"


@dataclass(slots=True)
class IRNode:
    node_id: str
    symbol_id: str
    node_type: str
    language: str
    file_path: str
    module: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProjectIR:
    nodes: list[IRNode]
    edges: list[CallEdge]


@dataclass(slots=True)
class AnalysisResult:
    project_root: str
    scan: ProjectScanSummary
    source_files: list[SourceFileRecord]
    module_dependencies: list[ModuleDependency]
    entrypoints: list[EntrypointRecord]
    symbols: list[SymbolRecord]
    models: list[DataModelRecord]
    call_graph: list[CallEdge]
    ir: ProjectIR
    risk_nodes: list[str]
    project_insights: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MigrationUnit:
    unit_id: str
    symbol_id: str
    name: str
    language: str
    target_language: str
    module: str
    file_path: str
    target_file_path: str
    kind: str
    source_code: str
    signature: str | None
    project_module: str | None = None
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    test_requirements: list[str] = field(default_factory=list)
    status: UnitStatus = UnitStatus.DISCOVERED
    retry_count: int = 0
    max_retries: int = 2
    failure_reason: str | None = None


@dataclass(slots=True)
class UnitContext:
    unit_id: str
    source_code: str
    source_file_content: str
    signature: str | None
    summary: str
    module_imports: list[str]
    decorators: list[str]
    module_level_context: str
    input_models: list[str]
    output_models: list[str]
    direct_dependencies: list[str]
    dependency_summaries: list[str]
    target_file_path: str
    target_constraints: dict[str, Any]
    test_requirements: list[str]
    related_tests: list[dict[str, str]] = field(default_factory=list)
    related_resources: list[dict[str, str]] = field(default_factory=list)
    build_context: dict[str, Any] = field(default_factory=dict)
    java_migration_hints: list[str] = field(default_factory=list)
    latest_failure_log: str | None = None


@dataclass(slots=True)
class UnitExecutionResult:
    unit_id: str
    status: UnitStatus
    output_path: str | None = None
    log_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RepairRecord:
    unit_id: str
    attempt: int
    failure_type: str
    failure_reason: str
    action: str
    impact_scope: list[str]
    verification_passed: bool


@dataclass(slots=True)
class PipelineState:
    project_root: str
    workspace_root: str
    target_root: str
    initialized: bool = False
    analyzed: bool = False
    planned: bool = False
    completed_units: int = 0
    failed_units: list[str] = field(default_factory=list)
    blocked_units: list[str] = field(default_factory=list)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        serialized = asdict(value)  # pyright: ignore[reportArgumentType]
        return {key: to_jsonable(item) for key, item in serialized.items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value
