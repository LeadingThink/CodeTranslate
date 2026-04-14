from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..core.models import (
    AnalysisResult,
    MigrationUnit,
    ModuleDependency,
    RiskLevel,
    SourceFileRecord,
    UnitStatus,
)


class MigrationPlanner:
    def build_units(
        self, analysis: AnalysisResult, target_root: str, target_language: str
    ) -> list[MigrationUnit]:
        target_path = Path(target_root)
        project_root = Path(analysis.project_root)
        entrypoint_modules = {entrypoint.module for entrypoint in analysis.entrypoints}
        entrypoint_modules.update(self._modules_from_project_insights(analysis))
        symbols_by_module: dict[str, list[object]] = defaultdict(list)
        for symbol in analysis.symbols:
            symbols_by_module[symbol.module].append(symbol)

        units: list[MigrationUnit] = []
        unit_by_module: dict[str, MigrationUnit] = {}

        for source_file in analysis.source_files:
            if source_file.role != "source":
                continue
            unit = self._build_file_unit(
                source_file=source_file,
                project_root=project_root,
                target_path=target_path,
                target_language=target_language,
                symbols=symbols_by_module.get(source_file.module, []),
                risk_nodes=analysis.risk_nodes,
                entrypoint_modules=entrypoint_modules,
            )
            units.append(unit)
            unit_by_module[source_file.module] = unit

        self._attach_module_dependencies(analysis.module_dependencies, unit_by_module)

        for unit in units:
            if unit.risk_level == RiskLevel.HIGH:
                unit.status = (
                    UnitStatus.ANALYZED if unit.dependencies else UnitStatus.READY
                )
            elif not unit.dependencies:
                unit.status = UnitStatus.READY

        return units

    def _modules_from_project_insights(self, analysis: AnalysisResult) -> set[str]:
        inferred = set()
        source_file_by_path = {
            record.path: record.module for record in analysis.source_files
        }
        for path in analysis.project_insights.get("inferred_entrypoints", []):
            normalized = str(path).lstrip("./")
            module = source_file_by_path.get(normalized)
            if module:
                inferred.add(module)
        for path in analysis.project_insights.get("startup_files", []):
            normalized = str(path).lstrip("./")
            module = source_file_by_path.get(normalized)
            if module:
                inferred.add(module)
        return inferred

    def _build_file_unit(
        self,
        source_file: SourceFileRecord,
        project_root: Path,
        target_path: Path,
        target_language: str,
        symbols: list[object],
        risk_nodes: list[str],
        entrypoint_modules: set[str],
    ) -> MigrationUnit:
        source_path = project_root / source_file.path
        source_code = source_path.read_text(encoding="utf-8")
        public_symbols = [
            str(getattr(symbol, "name"))
            for symbol in symbols
            if getattr(symbol, "name", "")
            and not str(getattr(symbol, "name")).startswith("_")
        ]
        name = source_path.stem
        target_relative_path = self._target_relative_path(
            Path(source_file.path), target_language
        )
        return MigrationUnit(
            unit_id=f"{source_file.module.replace('.', '__')}__file",
            symbol_id=f"{source_file.module}:__file__",
            name=name,
            language=source_file.language,
            target_language=target_language,
            module=source_file.module,
            file_path=str(source_path),
            target_file_path=str(target_path / target_relative_path),
            kind="file",
            source_code=source_code,
            signature=f"file {source_file.module}",
            risk_level=self._risk_level(
                source_file.module, symbols, risk_nodes, entrypoint_modules
            ),
            test_requirements=self._build_test_requirements(
                source_file, public_symbols
            ),
            status=UnitStatus.ANALYZED,
        )

    def _target_relative_path(
        self, source_relative_path: Path, target_language: str
    ) -> Path:
        target_suffix = self._default_suffix_for_language(target_language)
        if not target_suffix:
            return source_relative_path
        return source_relative_path.with_suffix(target_suffix)

    def _default_suffix_for_language(self, language: str) -> str:
        suffix_by_language = {
            "python": ".py",
            "nodejs": ".js",
            "java": ".java",
            "go": ".go",
            "rust": ".rs",
        }
        return suffix_by_language.get(language, "")

    def _build_test_requirements(
        self, source_file: SourceFileRecord, public_symbols: list[str]
    ) -> list[str]:
        requirements = [
            "file imports successfully",
            "public exports preserve source contract",
            "basic dependency interaction",
        ]
        if public_symbols:
            requirements.append(f"cover symbols: {', '.join(public_symbols[:8])}")
        if source_file.module.endswith(("main", "app", "server")):
            requirements.append("entrypoint behavior remains stable")
        return requirements

    def _risk_level(
        self,
        module: str,
        symbols: list[object],
        risk_nodes: list[str],
        entrypoint_modules: set[str],
    ) -> RiskLevel:
        symbol_ids = {
            str(getattr(symbol, "symbol_id"))
            for symbol in symbols
            if getattr(symbol, "symbol_id", None)
        }
        if module in risk_nodes or any(
            symbol_id in risk_nodes for symbol_id in symbol_ids
        ):
            return RiskLevel.HIGH
        if module in entrypoint_modules:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _attach_module_dependencies(
        self,
        module_dependencies: list[ModuleDependency],
        unit_by_module: dict[str, MigrationUnit],
    ) -> None:
        for dependency in module_dependencies:
            source_unit = unit_by_module.get(dependency.source_module)
            target_unit = unit_by_module.get(dependency.target_module)
            self._link_units(source_unit, target_unit)

    def _link_units(
        self,
        source_unit: MigrationUnit | None,
        target_unit: MigrationUnit | None,
    ) -> None:
        if (
            source_unit is None
            or target_unit is None
            or source_unit.unit_id == target_unit.unit_id
        ):
            return
        if target_unit.unit_id not in source_unit.dependencies:
            source_unit.dependencies.append(target_unit.unit_id)
        if source_unit.unit_id not in target_unit.dependents:
            target_unit.dependents.append(source_unit.unit_id)
