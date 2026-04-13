from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.logging_utils import configure_logging
from ..core.models import ProjectPaths
from ..engine.orchestrator import MigrationOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codetranslate")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--workspace-root", default=".codetranslate-workspace")
    parser.add_argument("--target-root", default="generated_target")
    parser.add_argument("--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)
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
    paths = ProjectPaths(
        source_root=str(Path(args.project_root).resolve()),
        workspace_root=str(Path(args.workspace_root).resolve()),
        target_root=str(Path(args.target_root).resolve()),
    )
    orchestrator = MigrationOrchestrator(paths)

    match args.command:
        case "analyze":
            result = orchestrator.analyze()
            payload = {"symbols": len(result.symbols), "models": len(result.models), "risks": len(result.risk_nodes)}
        case "plan":
            units = orchestrator.plan()
            payload = {"units": len(units), "ready": sum(unit.status.value == "ready" for unit in units)}
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
