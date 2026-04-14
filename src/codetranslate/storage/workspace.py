from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..core.models import (
    AnalysisResult,
    MigrationUnit,
    PipelineState,
    ProjectPaths,
    ProjectScanSummary,
    RepairRecord,
    UnitContext,
    UnitStatus,
    to_jsonable,
)


class WorkspaceManager:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self.root = Path(paths.workspace_root)
        self.target_root = Path(paths.target_root)
        self.analysis_dir = self.root / "analysis"
        self.plan_dir = self.root / "plan"
        self.state_dir = self.root / "state"
        self.contexts_dir = self.root / "contexts"
        self.logs_dir = self.root / "logs"
        self.patches_dir = self.root / "patches"
        self.reports_dir = self.root / "reports"
        self.generated_tests_dir = self.root / "generated_tests"

    def initialize(self) -> None:
        for directory in (
            self.root,
            self.target_root,
            self.analysis_dir,
            self.plan_dir,
            self.state_dir,
            self.contexts_dir,
            self.logs_dir,
            self.patches_dir,
            self.reports_dir,
            self.generated_tests_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def has_file(self, relative_path: str) -> bool:
        return (self.root / relative_path).exists()

    def write_json(self, relative_path: str, data: Any) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(to_jsonable(data), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def read_json(self, relative_path: str) -> Any:
        path = self.root / relative_path
        return json.loads(path.read_text(encoding="utf-8"))

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def copy_file_to_target(
        self, source_path: str | Path, target_relative_path: str | None = None
    ) -> Path:
        source = Path(source_path).resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"source file does not exist: {source}")
        destination = self._resolve_target_destination(source, target_relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def copy_directory_to_target(
        self, source_path: str | Path, target_relative_path: str | None = None
    ) -> Path:
        source = Path(source_path).resolve()
        if not source.exists() or not source.is_dir():
            raise FileNotFoundError(f"source directory does not exist: {source}")
        destination = self._resolve_target_destination(source, target_relative_path)
        shutil.copytree(source, destination, dirs_exist_ok=True)
        return destination

    def stage_related_resources(
        self, related_resources: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        staged: list[dict[str, str]] = []
        source_root = Path(self.paths.source_root).resolve()
        for item in related_resources:
            raw_path = str(item.get("path", "")).strip()
            if not raw_path:
                continue
            source = Path(raw_path)
            if not source.is_absolute():
                source = source_root / source
            source = source.resolve()
            if not source.exists():
                continue
            relative_target = self._best_effort_relative_target(source)
            if source.is_dir():
                destination = self.copy_directory_to_target(source, relative_target)
                staged_kind = "resource_dir"
            else:
                destination = self.copy_file_to_target(source, relative_target)
                staged_kind = item.get("kind", "resource_file")
            staged.append(
                {
                    **item,
                    "path": str(destination),
                    "source_path": str(source),
                    "kind": staged_kind,
                    "staged": "true",
                }
            )
        return staged

    def save_scan(self, scan: ProjectScanSummary) -> None:
        self.write_json("analysis/project_scan.json", scan)
        self.write_json(
            "analysis/entrypoints.json",
            {
                "entrypoints": scan.entrypoints,
                "candidate_entrypoints": scan.candidate_entrypoints,
            },
        )
        self.write_json("analysis/maven_modules.json", scan.maven_modules)
        self.write_json(
            "analysis/project_layout.json",
            {
                "source_directories": scan.source_directories,
                "test_directories": scan.test_directories,
                "resource_directories": scan.resource_directories,
                "test_files": scan.test_files,
                "resource_files": scan.resource_files,
            },
        )

    def save_analysis(self, result: AnalysisResult) -> None:
        self.write_json("analysis/source_files.json", result.source_files)
        self.write_json("analysis/module_dependencies.json", result.module_dependencies)
        self.write_json("analysis/entrypoints_structured.json", result.entrypoints)
        self.write_json("analysis/symbols.json", result.symbols)
        self.write_json("analysis/models.json", result.models)
        self.write_json("analysis/callgraph.json", result.call_graph)
        self.write_json("analysis/ir.json", result.ir)
        self.write_json("analysis/project_insights.json", result.project_insights)
        self.write_json("reports/risk_summary.json", {"risk_nodes": result.risk_nodes})

    def save_units(self, units: list[MigrationUnit]) -> None:
        self.write_json("plan/units.json", units)
        self.write_json(
            "plan/dependency_graph.json",
            {
                unit.unit_id: {
                    "dependencies": unit.dependencies,
                    "dependents": unit.dependents,
                    "cycle_group": unit.cycle_group,
                    "cycle_peers": unit.cycle_peers,
                    "kind": unit.kind,
                    "batch_members": unit.batch_members,
                    "batch_file_paths": unit.batch_file_paths,
                    "batch_target_file_paths": unit.batch_target_file_paths,
                }
                for unit in units
            },
        )
        module_graph: dict[str, list[str]] = {}
        for unit in units:
            module_graph.setdefault(unit.module, []).append(unit.unit_id)
        self.write_json("plan/module_graph.json", module_graph)
        cycle_groups: dict[str, list[str]] = {}
        for unit in units:
            if unit.cycle_group:
                cycle_groups.setdefault(unit.cycle_group, []).append(unit.unit_id)
        self.write_json("plan/cycle_groups.json", cycle_groups)

    def save_context(self, context: UnitContext) -> None:
        self.write_json(f"contexts/{context.unit_id}.json", context)

    def load_context(self, unit_id: str) -> dict[str, Any]:
        return self.read_json(f"contexts/{unit_id}.json")

    def save_pipeline_state(self, state: PipelineState) -> None:
        self.write_json("state/pipeline_state.json", state)

    def save_unit_statuses(self, units: list[MigrationUnit]) -> None:
        self.write_json(
            "state/unit_status.json",
            {
                unit.unit_id: {
                    "status": unit.status,
                    "retry_count": unit.retry_count,
                    "failure_reason": unit.failure_reason,
                }
                for unit in units
            },
        )
        ready_units = [
            unit.unit_id for unit in units if unit.status == UnitStatus.READY
        ]
        self.write_json("state/ready_queue.json", {"ready_units": ready_units})

    def save_repair_record(self, record: RepairRecord) -> None:
        filename = f"patches/{record.unit_id}.attempt-{record.attempt}.json"
        self.write_json(filename, record)

    def log_unit(self, unit_id: str, stage: str, content: str) -> Path:
        return self.write_text(f"logs/{unit_id}.{stage}.log", content)

    def read_unit_log(self, unit_id: str, stage: str) -> str | None:
        path = self.root / f"logs/{unit_id}.{stage}.log"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_report(self, name: str, content: Any) -> None:
        if isinstance(content, str):
            self.write_text(f"reports/{name}", content)
        else:
            self.write_json(f"reports/{name}", content)

    def load_units(self) -> list[MigrationUnit]:
        raw_units = self._safe_read_json("plan/units.json", [])
        statuses = self._safe_read_json("state/unit_status.json", {})
        units: list[MigrationUnit] = []
        for item in raw_units:
            status_row = statuses.get(item["unit_id"], {})
            normalized = {
                **item,
                "risk_level": item.get("risk_level", "low"),
                "target_language": item.get(
                    "target_language", item.get("language", "python")
                ),
                "project_module": item.get("project_module"),
                "cycle_group": item.get("cycle_group"),
                "cycle_peers": item.get("cycle_peers", []),
                "batch_members": item.get("batch_members", []),
                "batch_file_paths": item.get("batch_file_paths", []),
                "batch_target_file_paths": item.get("batch_target_file_paths", []),
                "status": UnitStatus(
                    status_row.get(
                        "status", item.get("status", UnitStatus.DISCOVERED.value)
                    )
                ),
                "retry_count": status_row.get(
                    "retry_count", item.get("retry_count", 0)
                ),
                "failure_reason": status_row.get(
                    "failure_reason", item.get("failure_reason")
                ),
            }
            units.append(MigrationUnit(**normalized))
        return units

    def save_plan_state(self, units: list[MigrationUnit]) -> None:
        state = PipelineState(
            project_root=self.paths.source_root,
            workspace_root=self.paths.workspace_root,
            target_root=self.paths.target_root,
            initialized=True,
            analyzed=True,
            planned=True,
            blocked_units=[
                unit.unit_id for unit in units if unit.status == UnitStatus.BLOCKED
            ],
        )
        self.save_pipeline_state(state)

    def save_run_state(self, units: list[MigrationUnit]) -> PipelineState:
        state = PipelineState(
            project_root=self.paths.source_root,
            workspace_root=self.paths.workspace_root,
            target_root=self.paths.target_root,
            initialized=True,
            analyzed=True,
            planned=True,
            completed_units=sum(unit.status == UnitStatus.VERIFIED for unit in units),
            failed_units=[
                unit.unit_id for unit in units if unit.status == UnitStatus.FAILED
            ],
            blocked_units=[
                unit.unit_id for unit in units if unit.status == UnitStatus.BLOCKED
            ],
        )
        self.save_pipeline_state(state)
        return state

    def _safe_read_json(self, relative_path: str, default: Any) -> Any:
        try:
            return self.read_json(relative_path)
        except FileNotFoundError:
            return default

    def _resolve_target_destination(
        self, source: Path, target_relative_path: str | None
    ) -> Path:
        if target_relative_path:
            destination = (self.target_root / target_relative_path).resolve()
        else:
            destination = (self.target_root / source.name).resolve()
        target_root_resolved = self.target_root.resolve()
        if not (
            destination == target_root_resolved
            or target_root_resolved in destination.parents
        ):
            raise ValueError(f"target path outside target root: {destination}")
        return destination

    def _best_effort_relative_target(self, source: Path) -> str:
        source_root = Path(self.paths.source_root).resolve()
        try:
            return source.relative_to(source_root).as_posix()
        except ValueError:
            anchor = source.anchor.rstrip("\\/")
            parts = [part for part in source.parts if part and part != anchor]
            if not parts:
                return source.name
            return "/".join(["external_sources", *parts])
