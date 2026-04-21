from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.models import (
    CallEdge,
    DataModelRecord,
    EntrypointRecord,
    IRNode,
    ModelField,
    ModuleDependency,
    ProjectScanSummary,
    SourceFileRecord,
    SymbolRecord,
)
from .base import LanguageAnalysis


def map_bridge_payload(
    payload: dict[str, Any],
    project_root: Path,
    scan: ProjectScanSummary,
) -> LanguageAnalysis:
    result = LanguageAnalysis()
    result.source_files.extend(
        _source_file(item, scan) for item in payload.get("source_files", [])
    )
    result.module_dependencies.extend(
        ModuleDependency(**item) for item in payload.get("module_dependencies", [])
    )
    result.entrypoints.extend(
        EntrypointRecord(**item) for item in payload.get("entrypoints", [])
    )
    result.symbols.extend(SymbolRecord(**item) for item in payload.get("symbols", []))
    result.models.extend(_model(item) for item in payload.get("models", []))
    result.call_graph.extend(CallEdge(**item) for item in payload.get("call_graph", []))
    result.ir_nodes.extend(_ir_node(symbol) for symbol in result.symbols)
    result.risk_nodes.extend(str(item) for item in payload.get("risk_nodes", []))
    details = payload.get("details", {})
    result.project_insights["language_insights"] = {
        "java": {
            "summary": _build_summary(details),
            "migration_notes": _build_notes(details),
            "high_risk_files": sorted(
                {
                    str(item.get("path")).replace("\\", "/")
                    for category in ("reflection_points", "async_flows")
                    for item in details.get(category, [])
                    if item.get("path")
                }
            ),
            "details": details,
        }
    }
    return result


def _source_file(
    item: dict[str, Any],
    scan: ProjectScanSummary,
) -> SourceFileRecord:
    relative_path = str(item["path"]).replace("\\", "/")
    return SourceFileRecord(
        path=relative_path,
        language=str(item["language"]),
        module=str(item["module"]),
        role=str(item["role"]),
        project_module=_project_module_name(relative_path, scan),
    )


def _project_module_name(relative_path: str, scan: ProjectScanSummary) -> str | None:
    for project_module in scan.maven_modules:
        prefix = project_module.relative_path.strip("./")
        if prefix and (
            relative_path == prefix or relative_path.startswith(prefix.rstrip("/") + "/")
        ):
            return project_module.name
    return None


def _model(item: dict[str, Any]) -> DataModelRecord:
    return DataModelRecord(
        model_id=str(item["model_id"]),
        name=str(item["name"]),
        language=str(item["language"]),
        module=str(item["module"]),
        file_path=str(item["file_path"]),
        fields=[ModelField(**field) for field in item.get("fields", [])],
    )


def _ir_node(symbol: SymbolRecord) -> IRNode:
    metadata = {
        "signature": symbol.signature,
        "decorators": symbol.decorators,
        "bases": symbol.bases,
    }
    if symbol.decorators:
        metadata["java_annotation_analysis"] = True
    return IRNode(
        node_id=symbol.symbol_id,
        symbol_id=symbol.symbol_id,
        node_type=symbol.kind,
        language=symbol.language,
        file_path=symbol.file_path,
        module=symbol.module,
        metadata=metadata,
    )


def _build_summary(details: dict[str, Any]) -> str:
    return (
        "JavaParser bridge built AST-backed analysis with "
        f"{len(details.get('ioc_components', []))} IoC components, "
        f"{len(details.get('reflection_points', []))} reflection points, "
        f"{len(details.get('dynamic_calls', []))} dynamic dispatch candidates, "
        f"{len(details.get('middleware', []))} middleware integrations, and "
        f"{len(details.get('async_flows', []))} async flow hints."
    )


def _build_notes(details: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if details.get("reflection_points"):
        notes.append(
            "JavaParser AST detected reflection hotspots; replace runtime reflective dispatch with explicit factories or metadata in the target design."
        )
    if details.get("dynamic_calls"):
        notes.append(
            "JavaParser AST detected IoC or event-driven dynamic dispatch; preserve bean lookup and runtime routing semantics during migration."
        )
    if details.get("middleware"):
        notes.append(
            "Middleware integrations were inferred from typed AST nodes and annotations; preserve infrastructure semantics, not only API names."
        )
    if details.get("async_flows"):
        notes.append(
            "Async flows include executor, listener, or scheduled patterns; preserve producer-consumer boundaries and concurrency contracts."
        )
    return notes
