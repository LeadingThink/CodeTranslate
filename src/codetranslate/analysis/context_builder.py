from __future__ import annotations

from pathlib import Path

from ..core.models import AnalysisResult, MigrationUnit, UnitContext


class UnitContextBuilder:
    def build(
        self,
        unit: MigrationUnit,
        analysis: AnalysisResult,
        units_by_id: dict[str, MigrationUnit],
    ) -> UnitContext:
        source_file_content = Path(unit.file_path).read_text(encoding="utf-8")
        models = [model.name for model in analysis.models if model.file_path == unit.file_path]
        module_symbols = [symbol for symbol in analysis.symbols if symbol.file_path == unit.file_path]
        dependency_summaries = []
        for dependency_id in unit.dependencies:
            dependency = units_by_id[dependency_id]
            dependency_summaries.append(f"{dependency.name}: migrated to {dependency.target_file_path}")
        module_imports = self._extract_module_imports(source_file_content)
        decorators = self._resolve_decorators(unit, module_symbols)
        module_level_context = self._build_module_level_context(module_symbols, models, unit)

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
            target_constraints={
                "source_language": unit.language,
                "language": unit.target_language,
                "strategy": "high-fidelity incremental migration",
                "preserve_behavior": True,
            },
            test_requirements=unit.test_requirements,
            latest_failure_log=unit.failure_reason,
        )

    def _extract_module_imports(self, source_file_content: str) -> list[str]:
        imports: list[str] = []
        for line in source_file_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(line)
        return imports

    def _resolve_decorators(self, unit: MigrationUnit, module_symbols: list[object]) -> list[str]:
        if unit.kind in {"module", "file"}:
            decorators: list[str] = []
            for symbol in module_symbols:
                decorators.extend(getattr(symbol, "decorators", []))
            return sorted({decorator for decorator in decorators if decorator})
        for symbol in module_symbols:
            if getattr(symbol, "symbol_id", None) == unit.symbol_id:
                return list(getattr(symbol, "decorators", []))
        return []

    def _build_module_level_context(self, module_symbols: list[object], models: list[str], unit: MigrationUnit) -> str:
        lines: list[str] = []
        lines.append(f"execution_unit={unit.kind} module={unit.module} file={unit.file_path}")
        for symbol in module_symbols:
            signature = getattr(symbol, "signature", None) or getattr(symbol, "name", "unknown")
            decorators = getattr(symbol, "decorators", [])
            prefix = f"{getattr(symbol, 'kind', 'symbol')} {signature}"
            if decorators:
                prefix += f" decorators={decorators}"
            lines.append(prefix)
        if models:
            lines.append(f"models={models}")
        return "\n".join(lines)
