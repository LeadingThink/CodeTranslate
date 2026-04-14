from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..analysis.analyzer import ProjectAnalyzer
from ..analysis.context_builder import UnitContextBuilder
from ..analysis.planner import MigrationPlanner
from ..analysis.project_intelligence import ProjectIntelligenceAnalyzer
from ..analysis.scanner import ProjectScanner
from ..core.models import (
    AnalysisResult,
    MigrationUnit,
    PipelineState,
    ProjectPaths,
    UnitStatus,
)
from ..core.settings import AppSettings
from ..runtime.llm import LLMClient
from ..runtime.migrator import UnitMigrator
from ..runtime.repairer import Repairer
from ..runtime.reporter import get_reporter
from ..runtime.tester import UnitTester
from ..runtime.unit_executor import UnitExecutor
from ..runtime.unit_state import UnitStateMachine
from ..runtime.verifier import Verifier
from ..storage.workspace import WorkspaceManager


class MigrationOrchestrator:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self.settings = AppSettings.from_env()
        self.workspace = WorkspaceManager(paths)
        self.scanner = ProjectScanner()
        self.project_intelligence = ProjectIntelligenceAnalyzer(self.settings)
        self.analyzer = ProjectAnalyzer(intelligence=self.project_intelligence)
        self.planner = MigrationPlanner()
        self.context_builder = UnitContextBuilder()
        self.llm = LLMClient(self.settings, paths)
        self.migrator = UnitMigrator(self.llm, self.workspace)
        self.tester = UnitTester(self.llm, self.workspace)
        self.verifier = Verifier(self.workspace)
        self.repairer = Repairer(self.llm, self.workspace)
        self.state_machine = UnitStateMachine()
        self.unit_executor = UnitExecutor(
            context_builder=self.context_builder,
            migrator=self.migrator,
            tester=self.tester,
            verifier=self.verifier,
            repairer=self.repairer,
            workspace=self.workspace,
        )

    def analyze(self) -> AnalysisResult:
        self.workspace.initialize()
        get_reporter().stage("Scan Project", self.paths.source_root)
        scan = self.scanner.scan(self.paths.source_root, self.paths.request)
        self.workspace.save_scan(scan)
        get_reporter().stage(
            "Build Analysis",
            f"source={self.paths.request.source_language} target={self.paths.request.target_language}",
        )
        analysis = self.analyzer.analyze(
            self.paths.source_root, scan, self.paths.request
        )
        self.workspace.save_analysis(analysis)
        state = PipelineState(
            project_root=self.paths.source_root,
            workspace_root=self.paths.workspace_root,
            target_root=self.paths.target_root,
            initialized=True,
            analyzed=True,
        )
        self.workspace.save_pipeline_state(state)
        return analysis

    def plan(self, analysis: AnalysisResult | None = None) -> list[MigrationUnit]:
        analysis = analysis or self.analyze()
        get_reporter().stage("Plan Migration", "Building file dependency graph")
        units = self.planner.build_units(
            analysis, self.paths.target_root, self.paths.request.target_language
        )
        self.workspace.save_units(units)
        self.workspace.save_unit_statuses(units)
        self.workspace.save_plan_state(units)
        self._report_cycle_batches(units)
        return units

    def run(self) -> dict[str, object]:
        analysis = self.analyze()
        units = self._load_or_create_plan(analysis)
        self._report_cycle_batches(units)
        get_reporter().stage("Run Migration", f"files={len(units)}")
        return self._run_with_analysis(analysis, units)

    def run_unit(self, unit_id: str) -> dict[str, str]:
        analysis = self.analyze()
        units = self._load_or_create_plan(analysis)
        unit = next((item for item in units if item.unit_id == unit_id), None)
        if unit is None:
            raise ValueError(f"Unknown unit_id: {unit_id}")

        units_by_id = {item.unit_id: item for item in units}
        if not self.state_machine.can_run_as_single_unit(unit, units_by_id):
            unit.status = UnitStatus.BLOCKED
            return {
                "unit_id": unit_id,
                "status": "blocked",
                "reason": "dependencies not yet verified",
            }

        unit.status = UnitStatus.READY
        self.unit_executor.execute(unit, analysis, units_by_id)
        self.workspace.save_unit_statuses(units)
        return {"unit_id": unit_id, "status": unit.status.value}

    def verify(self) -> dict[str, str]:
        return self.verifier.verify_system(self.workspace.load_units())

    def repair(self, unit_id: str) -> dict[str, str]:
        analysis = self.analyze()
        units = self._load_or_create_plan(analysis)
        units_by_id = {item.unit_id: item for item in units}
        unit = units_by_id[unit_id]
        repaired = self.unit_executor.execute(unit, analysis, units_by_id)
        self.workspace.save_unit_statuses(units)
        return {
            "unit_id": unit_id,
            "repaired": str(repaired).lower(),
            "status": unit.status.value,
        }

    def resume(self) -> dict[str, object]:
        analysis = self._load_or_skip_analysis()
        units = self._load_or_create_plan(analysis)
        self._reset_failed_units(units)
        self._report_cycle_batches(units)
        self._report_resume_state(units)
        self.workspace.save_unit_statuses(units)
        return self._run_with_analysis(analysis, units)

    def _run_with_analysis(
        self,
        analysis: AnalysisResult,
        units: list[MigrationUnit],
    ) -> dict[str, object]:
        units_by_id = {unit.unit_id: unit for unit in units}
        completed_in_batch = 0

        while True:
            ready_units = self.state_machine.refresh_ready_units(units)
            if not ready_units:
                self._write_blocked_report_if_needed(units)
                break

            for unit in ready_units:
                get_reporter().progress(
                    completed=sum(item.status == UnitStatus.VERIFIED for item in units),
                    total=len(units),
                    current=(
                        f"{unit.file_path} [{unit.kind}]"
                        if unit.kind == "cycle_batch"
                        else unit.file_path
                    ),
                    remaining_chain=self._critical_chain(units, units_by_id),
                )
                if not self.unit_executor.execute(unit, analysis, units_by_id):
                    self.workspace.save_unit_statuses(units)
                    get_reporter().result("Execute File", "failed", unit.file_path)
                    continue

                completed_in_batch += 1
                get_reporter().result("Execute File", "success", unit.file_path)
                if completed_in_batch >= 3:
                    self._run_module_checks(units)
                    completed_in_batch = 0

                self.state_machine.unlock_dependents(unit, units_by_id)
                self.workspace.save_unit_statuses(units)

        final_state = self.workspace.save_run_state(units)
        system_verify = self.verifier.verify_system(units)
        summary = {
            "completed_units": final_state.completed_units,
            "failed_units": final_state.failed_units,
            "blocked_units": final_state.blocked_units,
            "system_verify": system_verify,
        }
        self.workspace.write_report("final_migration_summary.json", summary)
        get_reporter().progress(
            completed=final_state.completed_units,
            total=len(units),
            current="",
            remaining_chain=self._critical_chain(units, units_by_id),
        )
        get_reporter().result(
            "Migration",
            system_verify.get("system_status", "unknown"),
            json.dumps(summary, ensure_ascii=False),
        )
        return summary

    def _load_or_create_plan(self, analysis: AnalysisResult) -> list[MigrationUnit]:
        units = self.workspace.load_units()
        if units:
            return units
        return self.plan(analysis)

    def _load_or_skip_analysis(self) -> AnalysisResult:
        pipeline_state = self.workspace.load_pipeline_state()
        if pipeline_state and pipeline_state.analyzed and pipeline_state.initialized:
            get_reporter().stage("Resume", "reusing cached analysis")
            scan = self.workspace.load_scan()
            source_files = self.workspace.read_json("analysis/source_files.json")
            module_dependencies = self.workspace.read_json(
                "analysis/module_dependencies.json"
            )
            entrypoints = self.workspace.read_json(
                "analysis/entrypoints_structured.json"
            )
            symbols = self.workspace.read_json("analysis/symbols.json")
            models = self.workspace.read_json("analysis/models.json")
            call_graph = self.workspace.read_json("analysis/callgraph.json")
            ir_data = self.workspace.read_json("analysis/ir.json")
            risk_nodes = self.workspace.read_json("reports/risk_summary.json").get(
                "risk_nodes", []
            )
            project_insights = self.workspace.read_json(
                "analysis/project_insights.json"
            )
            return AnalysisResult(
                project_root=pipeline_state.project_root,
                scan=scan,
                source_files=source_files,
                module_dependencies=module_dependencies,
                entrypoints=entrypoints,
                symbols=symbols,
                models=models,
                call_graph=call_graph,
                ir=ir_data,
                risk_nodes=risk_nodes,
                project_insights=project_insights,
            )
        return self.analyze()

    def _reset_failed_units(self, units: list[MigrationUnit]) -> int:
        failed_units = [unit for unit in units if unit.status == UnitStatus.FAILED]
        for unit in failed_units:
            unit.status = UnitStatus.DISCOVERED
            unit.retry_count = 0
            unit.failure_reason = None
        return len(failed_units)

    def _report_resume_state(self, units: list[MigrationUnit]) -> None:
        verified = sum(1 for unit in units if unit.status == UnitStatus.VERIFIED)
        failed_reset = sum(
            1
            for unit in units
            if unit.status == UnitStatus.DISCOVERED and unit.retry_count == 0
        )
        pending = sum(
            1
            for unit in units
            if unit.status in {UnitStatus.ANALYZED, UnitStatus.DISCOVERED}
            and unit.retry_count > 0
        )
        get_reporter().stage(
            "Resume State",
            f"verified={verified} pending={pending + failed_reset} total={len(units)}",
        )

    def _report_cycle_batches(self, units: list[MigrationUnit]) -> None:
        cycle_batches = [unit for unit in units if unit.kind == "cycle_batch"]
        summary = {
            "cycle_batch_count": len(cycle_batches),
            "batched_files": sum(len(unit.batch_members) for unit in cycle_batches),
            "batches": [
                {
                    "unit_id": unit.unit_id,
                    "members": unit.batch_members,
                    "files": unit.batch_file_paths,
                    "target_files": unit.batch_target_file_paths,
                    "dependencies": unit.dependencies,
                }
                for unit in cycle_batches
            ],
        }
        self.workspace.write_report("cycle_batch_summary.json", summary)
        if not cycle_batches:
            get_reporter().stage("Cycle Batches", "none detected")
            return

        get_reporter().stage(
            "Cycle Batches",
            f"detected={len(cycle_batches)} batched_files={summary['batched_files']}",
        )
        for unit in cycle_batches:
            members = ", ".join(Path(path).name for path in unit.batch_file_paths)
            dependencies = ", ".join(unit.dependencies) if unit.dependencies else "none"
            get_reporter().stage(
                f"Batch {unit.unit_id}",
                f"members=[{members}] deps=[{dependencies}]",
            )

    def _write_blocked_report_if_needed(self, units: list[MigrationUnit]) -> None:
        unfinished = [
            unit
            for unit in units
            if unit.status
            not in {UnitStatus.VERIFIED, UnitStatus.FAILED, UnitStatus.BLOCKED}
        ]
        if not unfinished:
            return
        report = self.state_machine.build_blocked_report(units)
        self.workspace.write_report("blocked_units_report.json", report)

    def _run_module_checks(self, units: list[MigrationUnit]) -> None:
        grouped: dict[str, list[MigrationUnit]] = defaultdict(list)
        for unit in units:
            grouped[unit.module].append(unit)
        for module, module_units in grouped.items():
            if all(unit.status == UnitStatus.VERIFIED for unit in module_units):
                self.verifier.verify_module(module, module_units)

    def _critical_chain(
        self, units: list[MigrationUnit], units_by_id: dict[str, MigrationUnit]
    ) -> str:
        pending = {
            unit.unit_id
            for unit in units
            if unit.status not in {UnitStatus.VERIFIED, UnitStatus.FAILED}
        }
        cache: dict[str, list[str]] = {}
        visiting: set[str] = set()

        def visit(unit_id: str) -> list[str]:
            if unit_id in cache:
                return cache[unit_id]
            if unit_id in visiting:
                return [f"{Path(units_by_id[unit_id].file_path).name}(cycle)"]
            visiting.add(unit_id)
            unit = units_by_id[unit_id]
            candidates = [
                visit(dependent_id)
                for dependent_id in unit.dependents
                if dependent_id in pending
            ]
            best_tail = max(candidates, key=len, default=[])
            cache[unit_id] = [Path(unit.file_path).name] + best_tail
            visiting.remove(unit_id)
            return cache[unit_id]

        chains = [visit(unit_id) for unit_id in pending]
        if not chains:
            return ""
        return " -> ".join(max(chains, key=len))
