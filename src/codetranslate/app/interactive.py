from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.models import MigrationRequest, ProjectPaths
from ..engine.orchestrator import MigrationOrchestrator
from ..runtime.reporter import Reporter, set_reporter


@dataclass(slots=True)
class ConsoleReporter:
    width: int = 24

    def stage(self, title: str, detail: str = "") -> None:
        print(f"[stage] {title}" + (f" | {detail}" if detail else ""))

    def tool(self, name: str, target: str, status: str = "ok") -> None:
        print(f"[tool] {name} | {target} | {status}")

    def model(self, label: str, detail: str = "") -> None:
        summary = detail.strip().splitlines()[0] if detail.strip() else ""
        print(f"[model] {label}" + (f" | {summary[:160]}" if summary else ""))

    def progress(self, completed: int, total: int, current: str = "", remaining_chain: str = "") -> None:
        total = max(total, 1)
        filled = int(self.width * completed / total)
        bar = "#" * filled + "-" * (self.width - filled)
        message = f"[progress] [{bar}] {completed}/{total}"
        if current:
            message += f" | current={Path(current).name}"
        if remaining_chain:
            message += f" | chain={remaining_chain}"
        print(message)

    def result(self, title: str, status: str, detail: str = "") -> None:
        print(f"[result] {title} | {status}" + (f" | {detail[:220]}" if detail else ""))


def start_interactive_session() -> None:
    print("CodeTranslate interactive session")
    project_root = _prompt("Project path", str(Path.cwd()))
    source_language = _prompt("Source language")
    target_language = _prompt("Target language")
    workspace_root = _prompt("Workspace path", str(Path(project_root).resolve() / ".codetranslate-workspace"))
    target_root = _prompt("Target output path", str(Path(project_root).resolve() / "generated_target"))
    entry_hints = _split_csv(_prompt("Entry hints (comma separated, optional)", ""))
    include_paths = _split_csv(_prompt("Include paths (comma separated, optional)", ""))
    exclude_paths = _split_csv(_prompt("Exclude paths (comma separated, optional)", ""))
    action = _prompt("Action [analyze|plan|run]", "run").strip().lower()

    request = MigrationRequest(
        source_language=source_language,
        target_language=target_language,
        entry_hints=entry_hints,
        include_paths=include_paths,
        exclude_paths=exclude_paths,
    )
    paths = ProjectPaths(
        source_root=str(Path(project_root).resolve()),
        workspace_root=str(Path(workspace_root).resolve()),
        target_root=str(Path(target_root).resolve()),
        request=request,
    )

    reporter = ConsoleReporter()
    set_reporter(reporter)
    reporter.stage("Task", f"{source_language} -> {target_language}")
    orchestrator = MigrationOrchestrator(paths)

    try:
        if action == "analyze":
            result = orchestrator.analyze()
            payload = {"symbols": len(result.symbols), "models": len(result.models), "risks": len(result.risk_nodes)}
        elif action == "plan":
            units = orchestrator.plan()
            payload = {"units": len(units), "ready": sum(unit.status.value == "ready" for unit in units)}
        else:
            payload = orchestrator.run()
    finally:
        set_reporter(None)

    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def _split_csv(raw_value: str) -> list[str]:
    if not raw_value.strip():
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]
