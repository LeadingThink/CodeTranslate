from __future__ import annotations

import atexit
import json
from dataclasses import dataclass
from pathlib import Path

try:
    import readline
except ImportError:  # pragma: no cover - platform dependent
    readline = None

from ..core.path_utils import normalize_user_path
from ..core.models import MigrationRequest, ProjectPaths
from ..engine.orchestrator import MigrationOrchestrator
from ..runtime.reporter import Reporter, set_reporter


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
    _configure_history()
    print("CodeTranslate interactive session")
    project_root = _prompt("Project path", str(Path.cwd()))
    resolved_project_root = normalize_user_path(project_root).resolve()
    default_target_root = (
        resolved_project_root.parent / f"{resolved_project_root.name}_translated"
    )
    target_root = _prompt("Output path", str(default_target_root))
    source_language = _prompt("Source language")
    target_language = _prompt("Target language")
    action = _prompt("Action [analyze|plan|run|resume]", "run").strip().lower()
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
        elif action == "resume":
            payload = orchestrator.resume()
        else:
            payload = orchestrator.run()
    finally:
        set_reporter(None)

    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _configure_history() -> None:
    if readline is None:
        return

    try:
        readline.read_history_file(_HISTORY_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        return

    readline.set_history_length(1000)
    if hasattr(readline, "set_auto_history"):
        readline.set_auto_history(False)
    atexit.register(_save_history)


def _save_history() -> None:
    if readline is None:
        return

    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(_HISTORY_FILE)
    except OSError:
        pass


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    _add_history_entry(value or (default or ""))
    return value or (default or "")


def _add_history_entry(entry: str) -> None:
    if readline is None or not entry:
        return

    last_index = readline.get_current_history_length()
    if last_index > 0 and readline.get_history_item(last_index) == entry:
        return

    readline.add_history(entry)
