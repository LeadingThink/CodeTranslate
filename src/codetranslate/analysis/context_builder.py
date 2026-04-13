from __future__ import annotations

from ..core.models import AnalysisResult, MigrationUnit, UnitContext


class UnitContextBuilder:
    def build(
        self,
        unit: MigrationUnit,
        analysis: AnalysisResult,
        units_by_id: dict[str, MigrationUnit],
    ) -> UnitContext:
        models = [model.name for model in analysis.models if model.file_path == unit.file_path]
        dependency_summaries = []
        for dependency_id in unit.dependencies:
            dependency = units_by_id[dependency_id]
            dependency_summaries.append(f"{dependency.name}: migrated to {dependency.target_file_path}")

        return UnitContext(
            unit_id=unit.unit_id,
            source_code=unit.source_code,
            signature=unit.signature,
            summary=f"{unit.language} {unit.kind} {unit.name} from module {unit.module}",
            input_models=models,
            output_models=models,
            direct_dependencies=unit.dependencies,
            dependency_summaries=dependency_summaries,
            target_file_path=unit.target_file_path,
            target_constraints={
                "language": unit.language,
                "strategy": "high-fidelity incremental migration",
                "preserve_behavior": True,
            },
            test_requirements=unit.test_requirements,
            latest_failure_log=unit.failure_reason,
        )
