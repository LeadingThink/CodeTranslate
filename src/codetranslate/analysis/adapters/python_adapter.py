from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.models import (
    CallEdge,
    DataModelRecord,
    EntrypointRecord,
    IRNode,
    ModelField,
    ModuleDependency,
    ProjectScanSummary,
    SourceFileRecord,
    SymbolRecord,
)
from .base import LanguageAnalysis, ScanObservation


@dataclass(slots=True)
class ImportBinding:
    local_name: str
    target_module: str
    target_symbol: str | None = None


@dataclass(slots=True)
class ModuleParseResult:
    module: str
    file_path: str
    source: str
    source_file: SourceFileRecord
    entrypoints: list[EntrypointRecord] = field(default_factory=list)
    module_dependencies: list[ModuleDependency] = field(default_factory=list)
    imports: dict[str, ImportBinding] = field(default_factory=dict)
    symbols: list[SymbolRecord] = field(default_factory=list)
    models: list[DataModelRecord] = field(default_factory=list)
    call_graph: list[CallEdge] = field(default_factory=list)
    ir_nodes: list[IRNode] = field(default_factory=list)
    risk_nodes: list[str] = field(default_factory=list)
    middleware: list[dict[str, Any]] = field(default_factory=list)
    dynamic_calls: list[dict[str, Any]] = field(default_factory=list)
    async_flows: list[dict[str, Any]] = field(default_factory=list)
    framework_endpoints: list[dict[str, Any]] = field(default_factory=list)
    reflection_points: list[dict[str, Any]] = field(default_factory=list)


class _ModuleAnalyzer(ast.NodeVisitor):
    def __init__(
        self, source: str, module: str, file_path: str, project_root: Path
    ) -> None:
        self.source = source
        self.module = module
        self.file_path = file_path
        self.project_root = project_root
        self.symbols: list[SymbolRecord] = []
        self.models: list[DataModelRecord] = []
        self.call_edges: list[CallEdge] = []
        self.risk_nodes: list[str] = []
        self.imports: dict[str, ImportBinding] = {}
        self.module_dependencies: list[ModuleDependency] = []
        self.entrypoints: list[EntrypointRecord] = []
        self.current_symbol: SymbolRecord | None = None
        self.class_depth = 0
        self.function_depth = 0
        self.middleware: list[dict[str, Any]] = []
        self.dynamic_calls: list[dict[str, Any]] = []
        self.async_flows: list[dict[str, Any]] = []
        self.framework_endpoints: list[dict[str, Any]] = []
        self.reflection_points: list[dict[str, Any]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".")[0]
            self.imports[local_name] = ImportBinding(
                local_name=local_name, target_module=alias.name
            )
            self.module_dependencies.append(
                ModuleDependency(
                    source_module=self.module,
                    target_module=alias.name,
                    language="python",
                    import_kind="import",
                    symbols=[],
                )
            )
            self._track_import_side_effects(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        target_module = self._resolve_import_from(node.module, node.level)
        imported_symbols: list[str] = []
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            self.imports[local_name] = ImportBinding(
                local_name=local_name,
                target_module=target_module,
                target_symbol=alias.name,
            )
            imported_symbols.append(alias.name)
        self.module_dependencies.append(
            ModuleDependency(
                source_module=self.module,
                target_module=target_module,
                language="python",
                import_kind="from_import",
                symbols=imported_symbols,
            )
        )
        self._track_import_side_effects(target_module, node.lineno)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, "async_function")
        if self.class_depth == 0 and self.function_depth == 0:
            self.risk_nodes.append(f"{self.module}:{node.name}")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol = SymbolRecord(
            symbol_id=f"{self.module}:{node.name}",
            name=node.name,
            qualname=f"{self.module}.{node.name}",
            kind="class",
            language="python",
            module=self.module,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
            signature=f"class {node.name}",
            decorators=[self._expr_to_str(item) for item in node.decorator_list],
            bases=[self._expr_to_str(item) for item in node.bases],
            docstring=ast.get_docstring(node),
        )
        self.symbols.append(symbol)
        if self._looks_like_model(node):
            self.models.append(
                DataModelRecord(
                    model_id=symbol.symbol_id,
                    name=node.name,
                    language="python",
                    module=self.module,
                    file_path=self.file_path,
                    fields=self._extract_model_fields(node),
                )
            )
        previous = self.current_symbol
        self.current_symbol = symbol
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1
        self.current_symbol = previous

    def visit_Call(self, node: ast.Call) -> None:
        if self.current_symbol is not None:
            callee = self._expr_to_str(node.func)
            self.call_edges.append(
                CallEdge(source=self.current_symbol.symbol_id, target=callee)
            )
            if callee in {"getattr", "setattr", "hasattr", "__import__"}:
                self.risk_nodes.append(self.current_symbol.symbol_id)
                self.dynamic_calls.append(
                    {
                        "module": self.module,
                        "symbol": self.current_symbol.symbol_id,
                        "path": Path(self.file_path)
                        .relative_to(self.project_root)
                        .as_posix(),
                        "line": node.lineno,
                        "call": callee,
                        "category": "dynamic_runtime_access",
                    }
                )
            if callee in {
                "getattr",
                "setattr",
                "hasattr",
                "importlib.import_module",
                "__import__",
            }:
                self.reflection_points.append(
                    {
                        "module": self.module,
                        "symbol": self.current_symbol.symbol_id,
                        "path": Path(self.file_path)
                        .relative_to(self.project_root)
                        .as_posix(),
                        "line": node.lineno,
                        "call": callee,
                    }
                )
            if callee in {
                "asyncio.create_task",
                "asyncio.gather",
                "asyncio.run",
                "create_task",
                "gather",
            }:
                self.async_flows.append(
                    {
                        "module": self.module,
                        "symbol": self.current_symbol.symbol_id,
                        "path": Path(self.file_path)
                        .relative_to(self.project_root)
                        .as_posix(),
                        "line": node.lineno,
                        "call": callee,
                        "kind": "async_runtime",
                    }
                )
        self.generic_visit(node)

    def finalize(self) -> ModuleParseResult:
        relative = Path(self.file_path).relative_to(self.project_root).as_posix()
        source_file = SourceFileRecord(
            path=relative,
            language="python",
            module=self.module,
            role="test" if "test" in relative else "source",
        )
        if self._has_main_guard():
            self.entrypoints.append(
                EntrypointRecord(
                    path=relative,
                    language="python",
                    kind="main_guard",
                    module=self.module,
                )
            )
        ir_nodes = [
            IRNode(
                node_id=symbol.symbol_id,
                symbol_id=symbol.symbol_id,
                node_type=symbol.kind,
                language="python",
                file_path=symbol.file_path,
                module=symbol.module,
                metadata={"signature": symbol.signature},
            )
            for symbol in self.symbols
        ]
        return ModuleParseResult(
            module=self.module,
            file_path=self.file_path,
            source=self.source,
            source_file=source_file,
            entrypoints=self.entrypoints,
            module_dependencies=self.module_dependencies,
            imports=self.imports,
            symbols=self.symbols,
            models=self.models,
            call_graph=self.call_edges,
            ir_nodes=ir_nodes,
            risk_nodes=self.risk_nodes,
            middleware=self.middleware,
            dynamic_calls=self.dynamic_calls,
            async_flows=self.async_flows,
            framework_endpoints=self.framework_endpoints,
            reflection_points=self.reflection_points,
        )

    def _handle_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str
    ) -> None:
        if self.class_depth > 0 or self.function_depth > 0:
            self.function_depth += 1
            self.generic_visit(node)
            self.function_depth -= 1
            return
        symbol = SymbolRecord(
            symbol_id=f"{self.module}:{node.name}",
            name=node.name,
            qualname=f"{self.module}.{node.name}",
            kind=kind,
            language="python",
            module=self.module,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
            signature=self._signature_to_str(node),
            decorators=[self._expr_to_str(item) for item in node.decorator_list],
            docstring=ast.get_docstring(node),
        )
        previous = self.current_symbol
        self.current_symbol = symbol
        self.symbols.append(symbol)
        self._track_function_semantics(node, symbol)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1
        self.current_symbol = previous

    def _track_function_semantics(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, symbol: SymbolRecord
    ) -> None:
        relative = Path(self.file_path).relative_to(self.project_root).as_posix()
        decorators = [self._expr_to_str(item) for item in node.decorator_list]
        route_decorators = [
            decorator
            for decorator in decorators
            if any(
                token in decorator
                for token in (
                    ".get",
                    ".post",
                    ".put",
                    ".delete",
                    ".patch",
                    "app.route",
                    "router.",
                )
            )
        ]
        if route_decorators:
            self.framework_endpoints.append(
                {
                    "module": self.module,
                    "symbol": symbol.symbol_id,
                    "path": relative,
                    "line": node.lineno,
                    "decorators": route_decorators,
                }
            )
            self.risk_nodes.append(symbol.symbol_id)
        if isinstance(node, ast.AsyncFunctionDef):
            self.async_flows.append(
                {
                    "module": self.module,
                    "symbol": symbol.symbol_id,
                    "path": relative,
                    "line": node.lineno,
                    "call": node.name,
                    "kind": "async_function",
                }
            )
        if any(
            token in decorator
            for decorator in decorators
            for token in ("middleware", "before_request", "after_request")
        ):
            self.middleware.append(
                {
                    "module": self.module,
                    "symbol": symbol.symbol_id,
                    "path": relative,
                    "line": node.lineno,
                    "kind": "framework_middleware",
                    "decorators": decorators,
                }
            )

    def _track_import_side_effects(self, imported_module: str, line: int) -> None:
        relative = Path(self.file_path).relative_to(self.project_root).as_posix()
        middleware_map = {
            "fastapi": ("http", "fastapi_app"),
            "flask": ("http", "flask_app"),
            "django": ("http", "django_app"),
            "sqlalchemy": ("database", "orm"),
            "celery": ("queue", "task_queue"),
            "asyncio": ("async", "runtime"),
        }
        for prefix, (technology, kind) in middleware_map.items():
            if imported_module == prefix or imported_module.startswith(prefix + "."):
                self.middleware.append(
                    {
                        "module": self.module,
                        "path": relative,
                        "line": line,
                        "technology": technology,
                        "kind": kind,
                        "import": imported_module,
                    }
                )
                break

    def _resolve_import_from(self, module: str | None, level: int) -> str:
        if level <= 0:
            return module or ""
        parts = self.module.split(".")
        base = parts[:-level]
        if module:
            base.extend(module.split("."))
        return ".".join(base)

    def _signature_to_str(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = [arg.arg for arg in node.args.args]
        return f"{node.name}({', '.join(args)})"

    def _looks_like_model(self, node: ast.ClassDef) -> bool:
        base_names = {self._expr_to_str(base) for base in node.bases}
        decorator_names = {self._expr_to_str(item) for item in node.decorator_list}
        if "BaseModel" in base_names or "dataclass" in decorator_names:
            return True
        return any(isinstance(stmt, ast.AnnAssign) for stmt in node.body)

    def _extract_model_fields(self, node: ast.ClassDef) -> list[ModelField]:
        fields: list[ModelField] = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.append(
                    ModelField(
                        name=stmt.target.id,
                        annotation=self._expr_to_str(stmt.annotation)
                        if stmt.annotation
                        else None,
                        default=self._expr_to_str(stmt.value) if stmt.value else None,
                    )
                )
        return fields

    def _expr_to_str(self, node: ast.AST | None) -> str:
        if node is None:
            return "None"
        try:
            return ast.unparse(node)
        except Exception:
            return node.__class__.__name__

    def _has_main_guard(self) -> bool:
        return 'if __name__ == "__main__":' in self.source


class PythonAdapter:
    language = "python"

    def detect_file(self, path: Path) -> bool:
        return path.suffix == ".py"

    def scan_file(self, path: Path, project_root: Path) -> ScanObservation:
        relative = path.relative_to(project_root).as_posix()
        observation = ScanObservation(
            languages={"python"},
            build_tools={"uv"},
            dependency_managers={"uv"},
        )
        if path.name in {"manage.py", "main.py", "app.py", "__main__.py"}:
            observation.entrypoints.add(relative)
        source = path.read_text(encoding="utf-8", errors="ignore")
        if 'if __name__ == "__main__":' in source:
            observation.candidate_entrypoints.add(relative)
        if path.name == "manage.py":
            observation.frameworks.add("django")
        return observation

    def analyze_project(
        self, project_root: Path, scan: ProjectScanSummary
    ) -> LanguageAnalysis:
        parsed_modules = self._parse_modules(project_root)
        symbol_index = {
            symbol.symbol_id: symbol
            for parsed in parsed_modules
            for symbol in parsed.symbols
        }
        resolved_edges = self._resolve_call_edges(parsed_modules, symbol_index)
        result = LanguageAnalysis()

        middleware: list[dict[str, Any]] = []
        dynamic_calls: list[dict[str, Any]] = []
        async_flows: list[dict[str, Any]] = []
        framework_endpoints: list[dict[str, Any]] = []
        reflection_points: list[dict[str, Any]] = []

        for parsed in parsed_modules:
            result.source_files.append(parsed.source_file)
            result.entrypoints.extend(parsed.entrypoints)
            result.module_dependencies.extend(parsed.module_dependencies)
            result.symbols.extend(parsed.symbols)
            result.models.extend(parsed.models)
            result.ir_nodes.extend(parsed.ir_nodes)
            result.risk_nodes.extend(parsed.risk_nodes)
            middleware.extend(parsed.middleware)
            dynamic_calls.extend(parsed.dynamic_calls)
            async_flows.extend(parsed.async_flows)
            framework_endpoints.extend(parsed.framework_endpoints)
            reflection_points.extend(parsed.reflection_points)
        result.call_graph.extend(resolved_edges)
        result.project_insights.update(
            {
                "language_insights": {
                    "python": {
                        "summary": self._build_summary(
                            middleware,
                            dynamic_calls,
                            async_flows,
                            framework_endpoints,
                            reflection_points,
                        ),
                        "migration_notes": self._build_migration_notes(
                            middleware, dynamic_calls, async_flows, framework_endpoints
                        ),
                        "high_risk_files": sorted(
                            {
                                item["path"]
                                for item in dynamic_calls
                                + async_flows
                                + framework_endpoints
                                + reflection_points
                                if item.get("path")
                            }
                        ),
                        "details": {
                            "middleware": middleware,
                            "dynamic_calls": dynamic_calls,
                            "async_flows": async_flows,
                            "framework_endpoints": framework_endpoints,
                            "reflection_points": reflection_points,
                        },
                    }
                },
            }
        )
        return result

    def _build_summary(
        self,
        middleware: list[dict[str, Any]],
        dynamic_calls: list[dict[str, Any]],
        async_flows: list[dict[str, Any]],
        framework_endpoints: list[dict[str, Any]],
        reflection_points: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        if framework_endpoints:
            parts.append(
                f"Detected {len(framework_endpoints)} framework endpoint handlers"
            )
        if middleware:
            parts.append(f"Observed {len(middleware)} middleware/runtime integrations")
        if async_flows:
            parts.append(f"Found {len(async_flows)} async execution hints")
        if dynamic_calls or reflection_points:
            parts.append(
                f"Marked {len(dynamic_calls) + len(reflection_points)} dynamic/reflection-heavy sites"
            )
        return (
            "; ".join(parts)
            if parts
            else "Python static analysis completed with baseline symbol extraction."
        )

    def _build_migration_notes(
        self,
        middleware: list[dict[str, Any]],
        dynamic_calls: list[dict[str, Any]],
        async_flows: list[dict[str, Any]],
        framework_endpoints: list[dict[str, Any]],
    ) -> list[str]:
        notes: list[str] = []
        if framework_endpoints:
            notes.append(
                "Preserve decorator-based routing semantics and request lifecycle hooks during migration."
            )
        if async_flows:
            notes.append(
                "Review asyncio task scheduling and async entrypoints to keep concurrency behavior aligned."
            )
        if dynamic_calls:
            notes.append(
                "Dynamic attribute/import access may need manual mapping in stricter target languages."
            )
        if any(item.get("technology") == "database" for item in middleware):
            notes.append(
                "ORM integration boundaries should be migrated before endpoint-level logic."
            )
        return notes

    def _parse_modules(self, project_root: Path) -> list[ModuleParseResult]:
        parsed_modules: list[ModuleParseResult] = []
        for path in project_root.rglob("*.py"):
            if any(
                part in {".venv", "__pycache__"} or part.startswith(".git")
                for part in path.parts
            ):
                continue
            source = path.read_text(encoding="utf-8")
            module = self._module_name(path, project_root)
            try:
                tree = ast.parse(source)
            except SyntaxError:
                parsed_modules.append(
                    self._syntax_error_result(project_root, path, module, source)
                )
                continue
            analyzer = _ModuleAnalyzer(source, module, str(path), project_root)
            analyzer.visit(tree)
            parsed_modules.append(analyzer.finalize())
        return parsed_modules

    def _resolve_call_edges(
        self,
        parsed_modules: list[ModuleParseResult],
        symbol_index: dict[str, SymbolRecord],
    ) -> list[CallEdge]:
        resolved: list[CallEdge] = []
        symbol_ids = set(symbol_index)
        for parsed in parsed_modules:
            for edge in parsed.call_graph:
                target = self._resolve_target(edge.target, parsed, symbol_ids)
                resolved.append(
                    CallEdge(
                        source=edge.source, target=target or edge.target, kind=edge.kind
                    )
                )
        return resolved

    def _resolve_target(
        self,
        raw_target: str,
        parsed: ModuleParseResult,
        symbol_ids: set[str],
    ) -> str | None:
        local = f"{parsed.module}:{raw_target}"
        if local in symbol_ids:
            return local
        root_name = raw_target.split(".", 1)[0]
        binding = parsed.imports.get(root_name)
        if binding is None:
            matches = [
                symbol_id
                for symbol_id in symbol_ids
                if symbol_id.endswith(f":{raw_target}")
            ]
            return matches[0] if len(matches) == 1 else None
        remainder = raw_target.split(".", 1)[1] if "." in raw_target else ""
        if binding.target_symbol and remainder:
            nested_candidate = (
                f"{binding.target_module}.{binding.target_symbol}:{remainder}"
            )
            if nested_candidate in symbol_ids:
                return nested_candidate
        symbol_name = binding.target_symbol or remainder
        if not symbol_name:
            return None
        candidate = f"{binding.target_module}:{symbol_name}"
        if candidate in symbol_ids:
            return candidate
        package_candidate = f"{binding.target_module}.{symbol_name}"
        package_matches = [
            symbol_id
            for symbol_id in symbol_ids
            if symbol_id.startswith(f"{package_candidate}:")
        ]
        if len(package_matches) == 1 and not remainder:
            return package_matches[0]
        matches = [
            symbol_id
            for symbol_id in symbol_ids
            if symbol_id.endswith(f":{symbol_name}")
        ]
        return matches[0] if len(matches) == 1 else None

    def _module_name(self, path: Path, project_root: Path) -> str:
        relative = path.relative_to(project_root)
        if path.name == "__init__.py":
            parent = relative.parent.as_posix().replace("/", ".")
            return parent or "__root__"
        return relative.with_suffix("").as_posix().replace("/", ".")

    def _syntax_error_result(
        self,
        project_root: Path,
        path: Path,
        module: str,
        source: str,
    ) -> ModuleParseResult:
        relative = path.relative_to(project_root).as_posix()
        return ModuleParseResult(
            module=module,
            file_path=str(path),
            source=source,
            source_file=SourceFileRecord(
                path=relative,
                language="python",
                module=module,
                role="test" if "test" in relative else "source",
            ),
            risk_nodes=[module],
        )
