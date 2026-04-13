from __future__ import annotations

from pathlib import Path

from ..core.models import AnalysisResult, MigrationUnit, ModuleDependency, RiskLevel, UnitStatus


class MigrationPlanner:
    def build_units(self, analysis: AnalysisResult, target_root: str) -> list[MigrationUnit]:
        target_path = Path(target_root)
        symbol_lookup = {symbol.symbol_id: symbol for symbol in analysis.symbols}
        module_to_units: dict[str, list[MigrationUnit]] = {}
        units: list[MigrationUnit] = []
        unit_by_symbol: dict[str, MigrationUnit] = {}
        project_root = Path(analysis.project_root)
        entrypoint_modules = {entrypoint.module for entrypoint in analysis.entrypoints}

        for symbol in analysis.symbols:
            source_lines = Path(symbol.file_path).read_text(encoding="utf-8").splitlines()
            snippet = "\n".join(source_lines[symbol.line_start - 1 : symbol.line_end])
            relative_path = Path(symbol.file_path).relative_to(project_root)
            unit = MigrationUnit(
                unit_id=symbol.symbol_id.replace(":", "__"),
                symbol_id=symbol.symbol_id,
                name=symbol.name,
                language=symbol.language,
                module=symbol.module,
                file_path=symbol.file_path,
                target_file_path=str(target_path / relative_path),
                kind=symbol.kind,
                source_code=snippet,
                signature=symbol.signature,
                risk_level=self._initial_risk_level(symbol.symbol_id, symbol.module, analysis.risk_nodes, entrypoint_modules),
                test_requirements=[
                    "normal path",
                    "boundary case",
                    "exception path if applicable",
                ],
                status=UnitStatus.ANALYZED,
            )
            units.append(unit)
            unit_by_symbol[symbol.symbol_id] = unit
            module_to_units.setdefault(unit.module, []).append(unit)

        self._attach_call_dependencies(analysis, symbol_lookup, unit_by_symbol)
        self._attach_module_dependencies(analysis.module_dependencies, module_to_units)

        for unit in units:
            if unit.risk_level == RiskLevel.HIGH:
                unit.status = UnitStatus.BLOCKED if unit.dependencies else UnitStatus.READY
            elif not unit.dependencies:
                unit.status = UnitStatus.READY

        return units

    def _initial_risk_level(
        self,
        symbol_id: str,
        module: str,
        risk_nodes: list[str],
        entrypoint_modules: set[str],
    ) -> RiskLevel:
        if symbol_id in risk_nodes or module in risk_nodes:
            return RiskLevel.HIGH
        if module in entrypoint_modules:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _attach_call_dependencies(
        self,
        analysis: AnalysisResult,
        symbol_lookup: dict[str, object],
        unit_by_symbol: dict[str, MigrationUnit],
    ) -> None:
        for edge in analysis.call_graph:
            source_unit = unit_by_symbol.get(edge.source)
            current_module = source_unit.module if source_unit is not None else ""
            target_symbol = self._resolve_target_symbol(edge.target, symbol_lookup, current_module)
            target_unit = unit_by_symbol.get(target_symbol) if target_symbol else None
            self._link_units(source_unit, target_unit)

    def _attach_module_dependencies(
        self,
        module_dependencies: list[ModuleDependency],
        module_to_units: dict[str, list[MigrationUnit]],
    ) -> None:
        for dependency in module_dependencies:
            source_units = module_to_units.get(dependency.source_module, [])
            target_units = module_to_units.get(dependency.target_module, [])
            if not source_units or not target_units:
                continue
            filtered_targets = self._filter_targets_by_symbols(target_units, dependency.symbols)
            for source_unit in source_units:
                for target_unit in filtered_targets:
                    self._link_units(source_unit, target_unit)

    def _filter_targets_by_symbols(
        self,
        target_units: list[MigrationUnit],
        symbols: list[str],
    ) -> list[MigrationUnit]:
        if not symbols:
            return target_units
        filtered = [unit for unit in target_units if unit.name in symbols]
        return filtered or target_units

    def _link_units(
        self,
        source_unit: MigrationUnit | None,
        target_unit: MigrationUnit | None,
    ) -> None:
        if source_unit is None or target_unit is None or source_unit.unit_id == target_unit.unit_id:
            return
        if target_unit.unit_id not in source_unit.dependencies:
            source_unit.dependencies.append(target_unit.unit_id)
        if source_unit.unit_id not in target_unit.dependents:
            target_unit.dependents.append(source_unit.unit_id)

    def _resolve_target_symbol(self, raw_target: str, symbol_lookup: dict[str, object], current_module: str) -> str | None:
        direct = f"{current_module}:{raw_target}"
        if direct in symbol_lookup:
            return direct
        matches = [symbol_id for symbol_id in symbol_lookup if symbol_id.endswith(f":{raw_target}")]
        if len(matches) == 1:
            return matches[0]
        return None
