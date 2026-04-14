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
        return units

    def run(self) -> dict[str, object]:
        analysis = self.analyze()
        units = self._load_or_create_plan(analysis)
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
        analysis = self.analyze()
        units = self._load_or_create_plan(analysis)
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
                    current=unit.file_path,
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

        def visit(unit_id: str) -> list[str]:
            if unit_id in cache:
                return cache[unit_id]
            unit = units_by_id[unit_id]
            candidates = [
                visit(dependent_id)
                for dependent_id in unit.dependents
                if dependent_id in pending
            ]
            best_tail = max(candidates, key=len, default=[])
            cache[unit_id] = [Path(unit.file_path).name] + best_tail
            return cache[unit_id]

        chains = [visit(unit_id) for unit_id in pending]
        if not chains:
            return ""
        return " -> ".join(max(chains, key=len))
