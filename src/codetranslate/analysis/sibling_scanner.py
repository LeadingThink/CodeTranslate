"""Sibling module scanner – a reusable tool that runs full Java-adapter-level
dependency analysis on Maven sibling modules discovered outside the main
project root.

The problem it solves
---------------------
When ``validator-core`` imports ``ValidationEvent`` from ``validator-api``,
the import target lives in a *sibling* Maven module that is **not** under
``project_root``.  The standard analysis pipeline only scans files inside
``project_root``, so ``validator-api`` classes are invisible.  The planner
then sees ``dependencies: []`` and the LLM is forced to ``try / except
ImportError`` against a Java package path.

This module provides :func:`analyze_sibling_modules` which:
1. Detects unresolved dependencies from the main analysis.
2. Locates sibling Maven module directories next to ``project_root``.
3. Runs the **full** ``JavaAdapter.analyze_project`` on each sibling root
   (every .java file, every import, every symbol – *not* a shallow scan).
4. Returns a :class:`SiblingAnalysisResult` that can be merged into the
   main :class:`AnalysisResult`.

The returned data includes ``source_files``, ``module_dependencies``,
``symbols``, etc. so the planner gets the *complete* dependency graph
(e.g. ``Diagnostic → DataDetails → SourceDetails``) rather than just
the directly-imported class.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import (
    AnalysisResult,
    MavenModuleRecord,
    ModuleDependency,
    SourceFileRecord,
)
from .adapters.base import LanguageAnalysis
from .adapters.java_adapter import JavaAdapter
from .language_registry import LanguageRegistry

logger = logging.getLogger(__name__)

_IMPORT_RE = re.compile(r"\bimport\s+(static\s+)?([\w.*]+)\s*;")


# ── public API ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class SiblingAnalysisResult:
    """Aggregated analysis output for all discovered sibling modules."""

    source_files: list[SourceFileRecord] = field(default_factory=list)
    module_dependencies: list[ModuleDependency] = field(default_factory=list)
    sibling_roots_scanned: list[str] = field(default_factory=list)

    # Forwarded from LanguageAnalysis so the planner can build symbols.
    symbols: list[Any] = field(default_factory=list)
    models: list[Any] = field(default_factory=list)
    call_graph: list[Any] = field(default_factory=list)
    ir_nodes: list[Any] = field(default_factory=list)
    risk_nodes: list[str] = field(default_factory=list)
    entrypoints: list[Any] = field(default_factory=list)
    project_insights: dict[str, Any] = field(default_factory=dict)


def analyze_sibling_modules(
    analysis: AnalysisResult,
    registry: LanguageRegistry | None = None,
) -> SiblingAnalysisResult:
    """Detect unresolved imports, find sibling Maven modules, and run full
    Java adapter analysis on them.

    Parameters
    ----------
    analysis:
        The completed analysis result for the main project root.
    registry:
        Language registry (a default one is created if *None*).

    Returns
    -------
    SiblingAnalysisResult
        Merged analysis data for all discovered sibling modules.
    """
    project_root = Path(analysis.project_root)
    existing_modules = {sf.module for sf in analysis.source_files}

    unresolved = _collect_unresolved_modules(
        analysis.module_dependencies, existing_modules
    )
    if not unresolved:
        logger.debug("No unresolved sibling dependencies detected.")
        return SiblingAnalysisResult()

    sibling_roots = _find_sibling_module_roots(
        project_root, analysis.scan.maven_modules
    )
    if not sibling_roots:
        logger.info(
            "Unresolved deps found but no sibling module directories discovered."
        )
        return SiblingAnalysisResult()

    adapter = _get_java_adapter(registry)
    if adapter is None:
        logger.warning("Java adapter not available – cannot analyse sibling modules.")
        return SiblingAnalysisResult()

    result = SiblingAnalysisResult()
    for sibling_root in sibling_roots:
        logger.info("Analyzing sibling module: %s", sibling_root)
        lang_analysis = _analyze_single_root(adapter, sibling_root)
        _merge_into(result, lang_analysis, project_root, sibling_root)
        result.sibling_roots_scanned.append(str(sibling_root))

    return result


def analyze_java_directory(directory: str) -> dict[str, Any]:
    """Convenience wrapper used as an AI-callable tool.

    Runs the Java adapter on *directory* and returns a JSON-serializable
    summary of discovered source files, imports and symbols.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        return {"error": f"Not a directory: {root}"}

    adapter = _get_java_adapter()
    if adapter is None:
        return {"error": "Java adapter not available."}

    from .adapters.base import ScanObservation  # noqa: avoid circular

    scan = _minimal_scan_summary(root)
    lang_analysis = adapter.analyze_project(root, scan)

    return {
        "root": str(root),
        "source_files": [
            {"path": sf.path, "module": sf.module, "language": sf.language}
            for sf in lang_analysis.source_files
        ],
        "module_dependencies": [
            {
                "source": dep.source_module,
                "target": dep.target_module,
                "kind": dep.import_kind,
                "symbols": dep.symbols,
            }
            for dep in lang_analysis.module_dependencies
        ],
        "symbols": [
            {"id": s.symbol_id, "name": s.name, "kind": s.kind, "module": s.module}
            for s in lang_analysis.symbols
        ],
    }


# ── internal helpers ────────────────────────────────────────────────────


def _collect_unresolved_modules(
    module_dependencies: list[ModuleDependency],
    existing_modules: set[str],
) -> set[str]:
    """Return target modules that are not covered by any source file."""
    skipped_prefixes = ("java.", "javax.", "org.", "com.")
    unresolved: set[str] = set()
    for dep in module_dependencies:
        target = dep.target_module
        if target in existing_modules:
            continue
        if any(target.startswith(p) for p in skipped_prefixes):
            continue
        unresolved.add(target)
    return unresolved


def _find_sibling_module_roots(
    project_root: Path,
    maven_modules: list[MavenModuleRecord],
) -> list[Path]:
    """Look for sibling Maven modules next to *project_root*."""
    parent = project_root.parent
    if not parent.is_dir():
        return []

    # Collect declared dependency artifact IDs.
    artifact_ids: set[str] = set()
    for module in maven_modules:
        artifact_ids.update(module.dependencies)

    if not artifact_ids:
        return []

    roots: list[Path] = []
    for candidate in parent.iterdir():
        if not candidate.is_dir() or candidate.name == project_root.name:
            continue
        if candidate.name.startswith("."):
            continue
        # Only include directories whose name matches a declared Maven
        # dependency artifact ID.  Do NOT fall back to "has pom.xml" –
        # that would pull in unrelated sibling projects.
        if candidate.name in artifact_ids:
            roots.append(candidate)
    return sorted(roots)


def _get_java_adapter(registry: LanguageRegistry | None = None) -> JavaAdapter | None:
    reg = registry or LanguageRegistry()
    adapter = reg.adapter_for_language("java")
    return adapter if isinstance(adapter, JavaAdapter) else None


def _minimal_scan_summary(root: Path) -> Any:
    """Build a minimal ``ProjectScanSummary`` sufficient for ``analyze_project``."""
    from ..core.models import ProjectScanSummary

    return ProjectScanSummary(
        project_root=str(root),
        source_directories=["src/main/java"]
        if (root / "src/main/java").is_dir()
        else [],
        test_directories=[],
        resource_directories=[],
        config_files=["pom.xml"] if (root / "pom.xml").exists() else [],
        languages=["java"],
        frameworks=[],
        build_tools=["maven"],
        dependency_managers=["maven"],
        entrypoints=[],
        candidate_entrypoints=[],
        files_scanned=0,
    )


def _analyze_single_root(adapter: JavaAdapter, root: Path) -> LanguageAnalysis:
    """Run full analysis on a single sibling module root."""
    scan = _minimal_scan_summary(root)
    return adapter.analyze_project(root, scan)


def _merge_into(
    result: SiblingAnalysisResult,
    lang: LanguageAnalysis,
    project_root: Path,
    sibling_root: Path,
) -> None:
    """Merge one sibling module's LanguageAnalysis into the aggregate result.

    Source file paths are stored as paths **relative to project_root.parent**
    (e.g. ``validator-api/src/main/java/.../Foo.java``).  This ensures the
    planner computes the correct *target* path under the output root.

    The planner's ``_build_file_unit`` will resolve the actual read location
    by falling back to ``project_root.parent / source_file.path`` when the
    file is not found directly under ``project_root``.
    """
    for sf in lang.source_files:
        abs_path = (sibling_root / sf.path).resolve()
        try:
            rel = abs_path.relative_to(project_root.parent).as_posix()
        except ValueError:
            rel = sf.path
        result.source_files.append(
            SourceFileRecord(
                path=rel,
                language=sf.language,
                module=sf.module,
                role=sf.role,
                project_module=sf.project_module,
            )
        )

    result.module_dependencies.extend(lang.module_dependencies)
    result.symbols.extend(lang.symbols)
    result.models.extend(lang.models)
    result.call_graph.extend(lang.call_graph)
    result.ir_nodes.extend(lang.ir_nodes)
    result.risk_nodes.extend(lang.risk_nodes)
    result.entrypoints.extend(lang.entrypoints)
