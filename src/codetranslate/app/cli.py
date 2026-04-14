from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .interactive import start_interactive_session
from ..core.logging_utils import configure_logging
from ..core.models import MigrationRequest, ProjectPaths
from ..engine.orchestrator import MigrationOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codetranslate")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--workspace-root", default=".codetranslate-workspace")
    parser.add_argument("--target-root", default="generated_target")
    parser.add_argument("--source-language")
    parser.add_argument("--target-language")
    parser.add_argument("--entry-hint", action="append", default=[])
    parser.add_argument("--include-path", action="append", default=[])
    parser.add_argument("--exclude-path", action="append", default=[])
    parser.add_argument("--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("start")
    subparsers.add_parser("analyze")
    subparsers.add_parser("plan")
    subparsers.add_parser("run")
    run_unit = subparsers.add_parser("run-unit")
    run_unit.add_argument("unit_id")
    subparsers.add_parser("verify")
    repair = subparsers.add_parser("repair")
    repair.add_argument("unit_id")
    subparsers.add_parser("resume")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)
    if args.command == "start":
        logging.getLogger().setLevel(logging.WARNING)
        try:
            start_interactive_session()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出 CodeTranslate。")
        return
    if not args.source_language or not args.target_language:
        parser.error(
            "the following arguments are required for this command: --source-language, --target-language"
        )
    request = MigrationRequest(
        source_language=args.source_language,
        target_language=args.target_language,
        entry_hints=args.entry_hint,
        include_paths=args.include_path,
        exclude_paths=args.exclude_path,
    )
    paths = ProjectPaths(
        source_root=str(Path(args.project_root).resolve()),
        workspace_root=str(Path(args.workspace_root).resolve()),
        target_root=str(Path(args.target_root).resolve()),
        request=request,
    )
    orchestrator = MigrationOrchestrator(paths)

    match args.command:
        case "analyze":
            result = orchestrator.analyze()
            payload = {
                "symbols": len(result.symbols),
                "models": len(result.models),
                "risks": len(result.risk_nodes),
            }
        case "plan":
            units = orchestrator.plan()
            payload = {
                "units": len(units),
                "ready": sum(unit.status.value == "ready" for unit in units),
            }
        case "run":
            payload = orchestrator.run()
        case "run-unit":
            payload = orchestrator.run_unit(args.unit_id)
        case "verify":
            payload = orchestrator.verify()
        case "repair":
            payload = orchestrator.repair(args.unit_id)
        case "resume":
            payload = orchestrator.resume()
        case _:
            raise ValueError(f"Unsupported command: {args.command}")

    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
