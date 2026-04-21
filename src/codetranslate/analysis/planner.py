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
from ..core.path_utils import sanitize_target_relative_path


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
        units = self._merge_cycle_batches(units)

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
        # Sibling Maven modules live next to project_root, not under it.
        # Their source_file.path is relative to project_root.parent.
        if not source_path.exists():
            candidate = project_root.parent / source_file.path
            if candidate.exists():
                source_path = candidate
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
            project_module=source_file.project_module,
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
        target_path = (
            source_relative_path.with_suffix(target_suffix)
            if target_suffix
            else source_relative_path
        )
        return sanitize_target_relative_path(target_path)

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

    def _merge_cycle_batches(self, units: list[MigrationUnit]) -> list[MigrationUnit]:
        units_by_id = {unit.unit_id: unit for unit in units}
        graph = {
            unit.unit_id: [
                dependency_id
                for dependency_id in unit.dependencies
                if dependency_id in units_by_id
            ]
            for unit in units
        }
        components = self._strongly_connected_components(graph)
        component_by_unit: dict[str, tuple[str, ...]] = {}
        for component in components:
            frozen = tuple(sorted(component))
            for unit_id in frozen:
                component_by_unit[unit_id] = frozen

        rebuilt_units: list[MigrationUnit] = []
        representative_by_component: dict[tuple[str, ...], MigrationUnit] = {}

        for position, component in enumerate(components, start=1):
            frozen = tuple(sorted(component))
            if len(frozen) == 1:
                unit = units_by_id[frozen[0]]
                unit.batch_members = [unit.unit_id]
                unit.batch_file_paths = [unit.file_path]
                unit.batch_target_file_paths = [unit.target_file_path]
                representative_by_component[frozen] = unit
                rebuilt_units.append(unit)
                continue

            members = [units_by_id[unit_id] for unit_id in frozen]
            cycle_group = f"cycle_group_{position:03d}"
            batch_unit = self._build_cycle_batch_unit(members, cycle_group)
            representative_by_component[frozen] = batch_unit
            rebuilt_units.append(batch_unit)

        for component, batch_unit in representative_by_component.items():
            dependency_ids: set[str] = set()
            dependent_ids: set[str] = set()
            component_members = set(component)
            for member_id in component_members:
                for dependency_id in graph.get(member_id, []):
                    dependency_component = component_by_unit[dependency_id]
                    if dependency_component == component:
                        continue
                    dependency_ids.add(
                        representative_by_component[dependency_component].unit_id
                    )
                for other_unit in units:
                    if member_id in other_unit.dependencies:
                        dependent_component = component_by_unit[other_unit.unit_id]
                        if dependent_component == component:
                            continue
                        dependent_ids.add(
                            representative_by_component[dependent_component].unit_id
                        )
            batch_unit.dependencies = sorted(dependency_ids)
            batch_unit.dependents = sorted(dependent_ids)

        return rebuilt_units

    def _build_cycle_batch_unit(
        self, members: list[MigrationUnit], cycle_group: str
    ) -> MigrationUnit:
        sorted_members = sorted(members, key=lambda unit: unit.file_path)
        lead = sorted_members[0]
        member_names = [Path(unit.file_path).name for unit in sorted_members]
        test_requirements = list(
            dict.fromkeys(
                [
                    requirement
                    for unit in sorted_members
                    for requirement in unit.test_requirements
                ]
                + [
                    "batch imports successfully across cyclic members",
                    "cross-file cyclic contracts remain stable after migration",
                ]
            )
        )
        return MigrationUnit(
            unit_id=cycle_group,
            symbol_id=f"{cycle_group}:batch",
            name=f"cycle batch {'/'.join(Path(unit.file_path).stem for unit in sorted_members)}",
            language=lead.language,
            target_language=lead.target_language,
            module=lead.module,
            project_module=lead.project_module,
            file_path=lead.file_path,
            target_file_path=lead.target_file_path,
            kind="cycle_batch",
            source_code="\n\n".join(
                f"// FILE: {unit.file_path}\n{unit.source_code}"
                for unit in sorted_members
            ),
            signature=f"cycle batch for {', '.join(member_names)}",
            cycle_group=cycle_group,
            cycle_peers=member_names,
            batch_members=[unit.unit_id for unit in sorted_members],
            batch_file_paths=[unit.file_path for unit in sorted_members],
            batch_target_file_paths=[unit.target_file_path for unit in sorted_members],
            risk_level=RiskLevel.HIGH,
            test_requirements=test_requirements,
            status=UnitStatus.ANALYZED,
        )

    def _strongly_connected_components(
        self, graph: dict[str, list[str]]
    ) -> list[list[str]]:
        index = 0
        indices: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        components: list[list[str]] = []

        def strong_connect(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for neighbor in graph.get(node, []):
                if neighbor not in indices:
                    strong_connect(neighbor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
                elif neighbor in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[neighbor])

            if lowlinks[node] != indices[node]:
                return

            component: list[str] = []
            while stack:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

        for node in graph:
            if node not in indices:
                strong_connect(node)

        return components

    def _escalate_risk(self, current: RiskLevel, minimum: RiskLevel) -> RiskLevel:
        order = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
        }
        return current if order[current] >= order[minimum] else minimum
