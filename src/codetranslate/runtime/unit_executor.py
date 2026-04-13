from __future__ import annotations

import logging
from pathlib import Path

from ..analysis.context_builder import UnitContextBuilder
from ..core.models import AnalysisResult, MigrationUnit, UnitContext, UnitExecutionResult, UnitStatus
from ..storage.workspace import WorkspaceManager
from .migrator import UnitMigrator
from .repairer import Repairer
from .tester import UnitTester
from .verifier import Verifier


logger = logging.getLogger(__name__)


class UnitExecutor:
    def __init__(
        self,
        context_builder: UnitContextBuilder,
        migrator: UnitMigrator,
        tester: UnitTester,
        verifier: Verifier,
        repairer: Repairer,
        workspace: WorkspaceManager,
    ) -> None:
        self.context_builder = context_builder
        self.migrator = migrator
        self.tester = tester
        self.verifier = verifier
        self.repairer = repairer
        self.workspace = workspace

    def execute(
        self,
        unit: MigrationUnit,
        analysis: AnalysisResult,
        units_by_id: dict[str, MigrationUnit],
    ) -> bool:
        logger.info("Processing unit %s", unit.unit_id)
        context = self.context_builder.build(unit, analysis, units_by_id)
        self.workspace.save_context(context)
        self.migrator.migrate(unit, context)

        test_path = self.tester.generate_test(unit, context)
        result = self._run_checks(unit, test_path)
        if result.status == UnitStatus.VERIFIED:
            return True
        return self._repair_until_verified(unit, context, result.log_path, test_path)

    def _repair_until_verified(
        self,
        unit: MigrationUnit,
        context: UnitContext,
        log_path: str | None,
        test_path: Path,
    ) -> bool:
        failure_log = self._read_failure_log(log_path)
        while unit.retry_count <= unit.max_retries and unit.status != UnitStatus.VERIFIED:
            if not self.repairer.repair(unit, context, failure_log, test_path):
                return False
            result = self._run_checks(unit, test_path)
            if result.status == UnitStatus.VERIFIED:
                return True
            failure_log = self._read_failure_log(result.log_path) or unit.failure_reason or failure_log
        unit.status = UnitStatus.FAILED
        return False

    def _run_checks(
        self,
        unit: MigrationUnit,
        test_path: Path,
    ) -> UnitExecutionResult:
        test_result = self.tester.run_test(unit, test_path)
        if test_result.status != UnitStatus.TESTED:
            return test_result

        verify_result = self.verifier.verify_unit(unit)
        return verify_result

    def _read_failure_log(self, log_path: str | None) -> str:
        if not log_path:
            return ""
        try:
            return Path(log_path).read_text(encoding="utf-8")
        except Exception:
            return log_path
