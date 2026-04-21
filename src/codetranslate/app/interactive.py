from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from ..core.path_utils import normalize_user_path
from ..core.models import MigrationRequest, ProjectPaths
from ..engine.orchestrator import MigrationOrchestrator
from ..runtime.reporter import set_reporter


_HISTORY_FILE = Path.home() / ".codetranslate_history"


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

    def progress(
        self, completed: int, total: int, current: str = "", remaining_chain: str = ""
    ) -> None:
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
    session = _create_prompt_session()
    print("CodeTranslate interactive session")
    project_root = _prompt(session, "Project path", str(Path.cwd()))
    resolved_project_root = normalize_user_path(project_root).resolve()
    default_target_root = (
        resolved_project_root.parent / f"{resolved_project_root.name}_translated"
    )
    target_root = _prompt(session, "Output path", str(default_target_root))
    source_language = _prompt(session, "Source language")
    target_language = _prompt(session, "Target language")
    action = _prompt(session, "Action [analyze|plan|run]", "run").strip().lower()
    workspace_root = str(
        normalize_user_path(target_root).resolve().parent / ".codetranslate-workspace"
    )

    request = MigrationRequest(
        source_language=source_language,
        target_language=target_language,
    )
    paths = ProjectPaths(
        source_root=str(resolved_project_root),
        workspace_root=workspace_root,
        target_root=str(normalize_user_path(target_root).resolve()),
        request=request,
    )

    reporter = ConsoleReporter()
    set_reporter(reporter)
    reporter.stage("Task", f"{source_language} -> {target_language}")
    orchestrator = MigrationOrchestrator(paths)

    try:
        if action == "analyze":
            result = orchestrator.analyze()
            payload = {
                "symbols": len(result.symbols),
                "models": len(result.models),
                "risks": len(result.risk_nodes),
            }
        elif action == "plan":
            units = orchestrator.plan()
            payload = {
                "units": len(units),
                "ready": sum(unit.status.value == "ready" for unit in units),
            }
        else:
            payload = orchestrator.run()
    finally:
        set_reporter(None)

    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _create_prompt_session() -> PromptSession:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(history=FileHistory(str(_HISTORY_FILE)))


def _prompt(
    session: PromptSession, label: str, default: str | None = None
) -> str:
    suffix = f" [{default}]" if default else ""
    value = session.prompt(f"{label}{suffix}: ", default=default or "").strip()
    return value or (default or "")
