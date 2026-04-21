from __future__ import annotations

import logging
from pathlib import Path

from ..core.models import (
    AnalysisResult,
    MigrationRequest,
    ProjectIR,
    ProjectScanSummary,
)
from .build_analysis import JavaBaselineRunner
from .language_registry import LanguageRegistry
from .project_intelligence import ProjectIntelligenceAnalyzer
from .sibling_scanner import analyze_sibling_modules

logger = logging.getLogger(__name__)


class ProjectAnalyzer:
    def __init__(
        self,
        registry: LanguageRegistry | None = None,
        intelligence: ProjectIntelligenceAnalyzer | None = None,
    ) -> None:
        self.registry = registry or LanguageRegistry()
        self.intelligence = intelligence
        self.baseline_runner = JavaBaselineRunner()

    def analyze(
        self, project_root: str, scan: ProjectScanSummary, request: MigrationRequest
    ) -> AnalysisResult:
        root = Path(project_root).resolve()
        source_files = []
        module_dependencies = []
        entrypoints = []
        symbols = []
        models = []
        edges = []
        ir_nodes = []
        risk_nodes: set[str] = set()
        project_insights: dict[str, object] = {}

        for language in scan.languages:
            adapter = self.registry.adapter_for_language(language)
            if adapter is None:
                continue
            analysis = adapter.analyze_project(root, scan)
            source_files.extend(analysis.source_files)
            module_dependencies.extend(analysis.module_dependencies)
            entrypoints.extend(analysis.entrypoints)
            symbols.extend(analysis.symbols)
            models.extend(analysis.models)
            edges.extend(analysis.call_graph)
            ir_nodes.extend(analysis.ir_nodes)
            risk_nodes.update(analysis.risk_nodes)
            self._merge_project_insight_maps(
                project_insights, analysis.project_insights
            )

        project_insights = self._normalize_project_insights(project_insights)
        if request.source_language == "java":
            project_insights["java_baseline"] = self.baseline_runner.run(
                root, scan.maven_modules
            )
            project_insights["maven_modules"] = [
                {
                    "name": module.name,
                    "relative_path": module.relative_path,
                    "packaging": module.packaging,
                    "dependencies": module.dependencies,
                    "source_roots": module.source_roots,
                    "test_roots": module.test_roots,
                    "resource_roots": module.resource_roots,
                }
                for module in scan.maven_modules
            ]

        result = AnalysisResult(
            project_root=str(root),
            scan=scan,
            source_files=source_files,
            module_dependencies=module_dependencies,
            entrypoints=entrypoints,
            symbols=symbols,
            models=models,
            call_graph=edges,
            ir=ProjectIR(nodes=ir_nodes, edges=edges),
            risk_nodes=sorted(risk_nodes),
            project_insights=project_insights,
        )

        # Analyse sibling Maven modules (e.g. validator-api) whose classes
        # are imported by the main project but live outside project_root.
        if request.source_language == "java":
            self._integrate_sibling_analysis(result, self.registry)

        if self.intelligence is not None:
            self._merge_project_insight_maps(
                result.project_insights, self.intelligence.enrich(result, request)
            )
            result.project_insights = self._normalize_project_insights(
                result.project_insights
            )
        return result

    def _integrate_sibling_analysis(
        self, result: AnalysisResult, registry: LanguageRegistry
    ) -> None:
        """Run full Java adapter analysis on sibling Maven modules and merge
        the results (source_files, module_dependencies, symbols, …) into
        *result* so the planner sees a complete dependency graph."""
        sibling = analyze_sibling_modules(result, registry)
        if not sibling.source_files:
            return

        result.source_files.extend(sibling.source_files)
        result.module_dependencies.extend(sibling.module_dependencies)
        result.symbols.extend(sibling.symbols)
        result.models.extend(sibling.models)
        result.call_graph.extend(sibling.call_graph)
        result.ir.nodes.extend(sibling.ir_nodes)
        result.ir.edges.extend(sibling.call_graph)
        result.risk_nodes = sorted(set(result.risk_nodes) | set(sibling.risk_nodes))
        result.entrypoints.extend(sibling.entrypoints)

        logger.info(
            "Integrated sibling module analysis: %d source files, %d dependencies from %s",
            len(sibling.source_files),
            len(sibling.module_dependencies),
            sibling.sibling_roots_scanned,
        )

    def _merge_project_insight_maps(
        self, base: dict[str, object], incoming: dict[str, object]
    ) -> None:
        for key, value in incoming.items():
            if key == "language_insights" and isinstance(value, dict):
                existing = base.get("language_insights")
                merged = dict(existing) if isinstance(existing, dict) else {}
                for language, payload in value.items():
                    if not isinstance(payload, dict):
                        continue
                    current = merged.get(language)
                    if isinstance(current, dict):
                        combined = dict(current)
                        combined.update(payload)
                        merged[language] = combined
                    else:
                        merged[language] = dict(payload)
                base[key] = merged
                continue
            base[key] = value

    def _normalize_project_insights(
        self, project_insights: dict[str, object]
    ) -> dict[str, object]:
        normalized = dict(project_insights)
        raw_language_insights = normalized.get("language_insights", {})
        language_insights: dict[str, dict[str, object]] = {}
        if isinstance(raw_language_insights, dict):
            for language, payload in raw_language_insights.items():
                if isinstance(payload, dict):
                    language_insights[str(language)] = dict(payload)

        aggregate_summary: dict[str, str] = {}
        aggregate_notes: dict[str, list[str]] = {}
        aggregate_high_risk: dict[str, list[str]] = {}

        for language, payload in language_insights.items():
            summary = payload.get("summary")
            if isinstance(summary, str) and summary.strip():
                aggregate_summary[language] = summary.strip()

            notes = payload.get("migration_notes")
            if isinstance(notes, list):
                aggregate_notes[language] = [
                    str(item).strip() for item in notes if str(item).strip()
                ]
            else:
                aggregate_notes[language] = []

            files = payload.get("high_risk_files")
            if isinstance(files, list):
                aggregate_high_risk[language] = sorted(
                    {str(item).strip() for item in files if str(item).strip()}
                )
            else:
                aggregate_high_risk[language] = []

        normalized["language_insights"] = language_insights
        normalized["summary_by_language"] = aggregate_summary
        normalized["migration_notes_by_language"] = aggregate_notes
        normalized["high_risk_files_by_language"] = aggregate_high_risk
        normalized["summary"] = self._merge_global_summary(
            normalized.get("summary"), aggregate_summary
        )
        normalized["migration_notes"] = self._merge_global_notes(
            normalized.get("migration_notes"), aggregate_notes
        )
        normalized["high_risk_files"] = self._merge_global_files(
            normalized.get("high_risk_files"), aggregate_high_risk
        )
        return normalized

    def _merge_global_summary(
        self, existing_summary: object, summary_by_language: dict[str, str]
    ) -> str:
        parts: list[str] = []
        if isinstance(existing_summary, str) and existing_summary.strip():
            parts.append(existing_summary.strip())
        for language, summary in sorted(summary_by_language.items()):
            labeled = f"[{language}] {summary}"
            if labeled not in parts:
                parts.append(labeled)
        return "\n\n".join(parts)

    def _merge_global_notes(
        self,
        existing_notes: object,
        notes_by_language: dict[str, list[str]],
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        if isinstance(existing_notes, list):
            for item in existing_notes:
                text = str(item).strip()
                if text and text not in seen:
                    seen.add(text)
                    merged.append(text)
        for language, notes in sorted(notes_by_language.items()):
            for note in notes:
                labeled = f"[{language}] {note}"
                if labeled not in seen:
                    seen.add(labeled)
                    merged.append(labeled)
        return merged

    def _merge_global_files(
        self,
        existing_files: object,
        files_by_language: dict[str, list[str]],
    ) -> list[str]:
        merged: set[str] = set()
        if isinstance(existing_files, list):
            merged.update(
                str(item).strip() for item in existing_files if str(item).strip()
            )
        for files in files_by_language.values():
            merged.update(str(item).strip() for item in files if str(item).strip())
        return sorted(merged)
