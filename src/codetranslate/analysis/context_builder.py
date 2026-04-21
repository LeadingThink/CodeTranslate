from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..core.models import AnalysisResult, MigrationUnit, UnitContext


class UnitContextBuilder:
    def build(
        self,
        unit: MigrationUnit,
        analysis: AnalysisResult,
        units_by_id: dict[str, MigrationUnit],
    ) -> UnitContext:
        source_paths = unit.batch_file_paths or [unit.file_path]
        target_paths = unit.batch_target_file_paths or [unit.target_file_path]
        batch_sources: list[dict[str, str]] = []
        models: list[str] = []
        module_symbols: list[object] = []
        for source_path in source_paths:
            content = Path(source_path).read_text(encoding="utf-8")
            batch_sources.append({"path": source_path, "content": content})
            models.extend(
                model.name
                for model in analysis.models
                if model.file_path == source_path
            )
            module_symbols.extend(
                symbol for symbol in analysis.symbols if symbol.file_path == source_path
            )
        source_file_content = "\n\n".join(
            f"# FILE: {item['path']}\n{item['content']}" for item in batch_sources
        )
        dependency_summaries = []
        for dependency_id in unit.dependencies:
            dependency = units_by_id[dependency_id]
            dependency_summaries.append(
                f"{dependency.name}: migrated to {', '.join(dependency.batch_target_file_paths or [dependency.target_file_path])}"
            )
        module_imports = self._extract_module_imports(source_file_content)
        decorators = self._resolve_decorators(unit, module_symbols)
        module_level_context = self._build_module_level_context(
            module_symbols, models, unit
        )
        related_tests = self._related_tests(unit, analysis)
        related_resources = self._related_resources(unit, analysis)
        build_context = self._build_context(unit, analysis)
        java_migration_hints = self._java_migration_hints(unit, analysis)
        if unit.cycle_group and unit.cycle_peers:
            dependency_summaries.append(
                f"cyclic peers in {unit.cycle_group}: {', '.join(unit.cycle_peers)}"
            )

        return UnitContext(
            unit_id=unit.unit_id,
            source_code=unit.source_code,
            source_file_content=source_file_content,
            signature=unit.signature,
            summary=f"{unit.language} {unit.kind} {unit.name} from module {unit.module}",
            module_imports=module_imports,
            decorators=decorators,
            module_level_context=module_level_context,
            input_models=models,
            output_models=models,
            direct_dependencies=unit.dependencies,
            dependency_summaries=dependency_summaries,
            target_file_path=unit.target_file_path,
            target_file_paths=target_paths,
            target_constraints={
                "source_language": unit.language,
                "language": unit.target_language,
                "strategy": "high-fidelity incremental migration",
                "preserve_behavior": True,
            },
            test_requirements=unit.test_requirements,
            batch_sources=batch_sources,
            related_tests=related_tests,
            related_resources=related_resources,
            build_context=build_context,
            java_migration_hints=java_migration_hints,
            latest_failure_log=unit.failure_reason,
        )

    def _extract_module_imports(self, source_file_content: str) -> list[str]:
        imports: list[str] = []
        for line in source_file_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(line)
        return imports

    def _resolve_decorators(
        self, unit: MigrationUnit, module_symbols: Sequence[object]
    ) -> list[str]:
        if unit.kind in {"module", "file", "cycle_batch"}:
            decorators: list[str] = []
            for symbol in module_symbols:
                decorators.extend(getattr(symbol, "decorators", []))
            return sorted({decorator for decorator in decorators if decorator})
        for symbol in module_symbols:
            if getattr(symbol, "symbol_id", None) == unit.symbol_id:
                return list(getattr(symbol, "decorators", []))
        return []

    def _build_module_level_context(
        self, module_symbols: Sequence[object], models: list[str], unit: MigrationUnit
    ) -> str:
        lines: list[str] = []
        lines.append(
            f"execution_unit={unit.kind} module={unit.module} file={unit.file_path}"
        )
        if unit.batch_members:
            lines.append(f"batch_members={unit.batch_members}")
        for symbol in module_symbols:
            signature = getattr(symbol, "signature", None) or getattr(
                symbol, "name", "unknown"
            )
            decorators = getattr(symbol, "decorators", [])
            prefix = f"{getattr(symbol, 'kind', 'symbol')} {signature}"
            if decorators:
                prefix += f" decorators={decorators}"
            lines.append(prefix)
        if models:
            lines.append(f"models={models}")
        return "\n".join(lines)

    def _related_tests(
        self, unit: MigrationUnit, analysis: AnalysisResult
    ) -> list[dict[str, str]]:
        stems = {
            Path(path).stem.lower()
            for path in (unit.batch_file_paths or [unit.file_path])
        }
        related: list[dict[str, str]] = []
        for test_path in analysis.scan.test_files:
            test_stem = Path(test_path).stem.lower()
            if any(
                stem in test_stem or test_stem.replace("test", "") in stem
                for stem in stems
            ):
                related.append({"path": test_path, "kind": "test_file"})
        return related[:12]

    def _related_resources(
        self, unit: MigrationUnit, analysis: AnalysisResult
    ) -> list[dict[str, str]]:
        segments = {
            part.lower()
            for path in (unit.batch_file_paths or [unit.file_path])
            for part in Path(path).parts
            if part
        }
        related: list[dict[str, str]] = []
        for resource_path in analysis.scan.resource_files:
            resource_segments = {
                part.lower() for part in Path(resource_path).parts if part
            }
            if segments.intersection(resource_segments):
                related.append({"path": resource_path, "kind": "resource_file"})
        return related[:12]

    def _build_context(
        self, unit: MigrationUnit, analysis: AnalysisResult
    ) -> dict[str, object]:
        baseline = analysis.project_insights.get("java_baseline", {})
        module_info = {}
        for module in analysis.scan.maven_modules:
            if module.name == unit.project_module:
                module_info = {
                    "name": module.name,
                    "relative_path": module.relative_path,
                    "packaging": module.packaging,
                    "dependencies": module.dependencies,
                    "source_roots": module.source_roots,
                    "test_roots": module.test_roots,
                    "resource_roots": module.resource_roots,
                }
                break
        return {
            "project_module": unit.project_module,
            "maven_module": module_info,
            "java_baseline": baseline,
            "cycle_group": unit.cycle_group,
            "cycle_peers": unit.cycle_peers,
            "batch_members": unit.batch_members,
            "batch_file_paths": unit.batch_file_paths,
            "batch_target_file_paths": unit.batch_target_file_paths,
        }

    def _java_migration_hints(
        self, unit: MigrationUnit, analysis: AnalysisResult
    ) -> list[str]:
        if unit.language != "java" or unit.target_language != "python":
            return []
        hints = [
            "Translate Java classes into idiomatic Python classes; prefer dataclasses for immutable/value-oriented state.",
            "Map interfaces and abstract classes to abc.ABC or Protocol when contract clarity matters.",
            "Replace Java collections and streams with Python list/dict/set and comprehensions while preserving ordering semantics.",
            "Preserve exception and validation semantics explicitly; do not silently drop checked-error behavior.",
            "Use module-level helpers instead of static utility classes when no object state is required.",
        ]
        notes = analysis.project_insights.get("migration_notes_by_language", {})
        java_notes = notes.get("java", []) if isinstance(notes, dict) else []
        return hints + [str(note) for note in java_notes[:8]]
