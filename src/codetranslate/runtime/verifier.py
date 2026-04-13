from __future__ import annotations

import sys
from pathlib import Path

from .language_runtime import run_test_file, validate_source_file
from ..core.models import MigrationUnit, UnitExecutionResult, UnitStatus
from ..storage.workspace import WorkspaceManager


class Verifier:
    def __init__(self, workspace: WorkspaceManager) -> None:
        self.workspace = workspace

    def verify_unit(self, unit: MigrationUnit) -> UnitExecutionResult:
        target_path = Path(unit.target_file_path)
        try:
            validate_source_file(target_path, unit.target_language)
        except Exception as exc:
            unit.failure_reason = str(exc)
            unit.status = UnitStatus.REPAIRING
            log_path = self.workspace.log_unit(unit.unit_id, "verify", str(exc))
            return UnitExecutionResult(unit_id=unit.unit_id, status=unit.status, log_path=str(log_path))

        unit.status = UnitStatus.VERIFIED
        log_path = self.workspace.log_unit(unit.unit_id, "verify", "verification passed")
        return UnitExecutionResult(unit_id=unit.unit_id, status=UnitStatus.VERIFIED, log_path=str(log_path))

    def verify_module(self, module: str, units: list[MigrationUnit]) -> dict[str, str]:
        compile_errors = self._validate_units(units)
        status = "passed" if not compile_errors and all(unit.status == UnitStatus.VERIFIED for unit in units) else "pending"
        report = {
            "module": module,
            "status": status,
            "verified_units": [unit.unit_id for unit in units if unit.status == UnitStatus.VERIFIED],
            "compile_errors": compile_errors,
        }
        self.workspace.write_report(f"module-verify-{module.replace('.', '_')}.json", report)
        return {"module": module, "status": status}

    def verify_system(self, units: list[MigrationUnit]) -> dict[str, str]:
        compile_errors = self._validate_units([unit for unit in units if Path(unit.target_file_path).exists()])
        generated_tests = sorted(self.workspace.generated_tests_dir.glob("test_*"))
        test_results = self._run_generated_tests(generated_tests)
        status = "passed"
        if any(unit.status != UnitStatus.VERIFIED for unit in units) or compile_errors:
            status = "failed"
        if any(result["returncode"] != 0 for result in test_results):
            status = "failed"
        result = {
            "system_status": status,
            "verified_units": str(sum(unit.status == UnitStatus.VERIFIED for unit in units)),
            "compile_errors": compile_errors,
            "generated_tests": test_results,
        }
        self.workspace.write_report("final_system_verify.json", result)
        return result

    def _validate_units(self, units: list[MigrationUnit]) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        for unit in units:
            path = Path(unit.target_file_path)
            if not path.exists():
                errors.append({"path": str(path), "error": "missing target file"})
                continue
            try:
                validate_source_file(path, unit.target_language)
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
        return errors

    def _run_generated_tests(self, test_paths: list[Path]) -> list[dict[str, str | int]]:
        results: list[dict[str, str | int]] = []
        for test_path in test_paths:
            language = "nodejs" if test_path.suffix in {".js", ".mjs", ".cjs", ".ts"} else "python"
            process = run_test_file(test_path, language)
            results.append(
                {
                    "path": str(test_path),
                    "returncode": process.returncode,
                    "stdout": process.stdout.strip(),
                    "stderr": process.stderr.strip(),
                }
            )
        return results
