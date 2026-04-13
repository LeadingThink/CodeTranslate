from __future__ import annotations

from pathlib import Path

from ..core.models import AnalysisResult, MigrationRequest, ProjectIR, ProjectScanSummary
from .language_registry import LanguageRegistry
from .project_intelligence import ProjectIntelligenceAnalyzer


class ProjectAnalyzer:
    def __init__(
        self,
        registry: LanguageRegistry | None = None,
        intelligence: ProjectIntelligenceAnalyzer | None = None,
    ) -> None:
        self.registry = registry or LanguageRegistry()
        self.intelligence = intelligence

    def analyze(self, project_root: str, scan: ProjectScanSummary, request: MigrationRequest) -> AnalysisResult:
        root = Path(project_root).resolve()
        source_files = []
        module_dependencies = []
        entrypoints = []
        symbols = []
        models = []
        edges = []
        ir_nodes = []
        risk_nodes: set[str] = set()

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
        )
        if self.intelligence is not None:
            result.project_insights = self.intelligence.enrich(result, request)
        return result
