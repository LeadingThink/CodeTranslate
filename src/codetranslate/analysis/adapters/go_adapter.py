from __future__ import annotations

import re
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


PACKAGE_RE = re.compile(r"\bpackage\s+([A-Za-z_][A-Za-z0-9_]*)")
IMPORT_BLOCK_RE = re.compile(r"import\s*\((?P<body>.*?)\)", re.DOTALL)
IMPORT_LINE_RE = re.compile(r'"([^"]+)"|`([^`]+)`')
SINGLE_IMPORT_RE = re.compile(
    r'import\s+(?:[A-Za-z_][A-Za-z0-9_]*\s+)?["`]([^"`]+)["`]'
)
FUNC_RE = re.compile(
    r"func\s+(?:\((?P<receiver>[^)]+)\)\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
TYPE_STRUCT_RE = re.compile(
    r"type\s+([A-Z][A-Za-z0-9_]*)\s+struct\s*\{(?P<body>.*?)\}", re.DOTALL
)
INTERFACE_RE = re.compile(
    r"type\s+([A-Z][A-Za-z0-9_]*)\s+interface\s*\{(?P<body>.*?)\}", re.DOTALL
)
ROUTE_RE = re.compile(
    r"\b(?:router|r|mux|app)\.(GET|POST|PUT|DELETE|PATCH|Handle|HandleFunc|Group)\s*\(|"
    r"\bhttp\.HandleFunc\s*\(|\bhttp\.ListenAndServe\s*\(|\bgin\.Default\s*\(|\bfiber\.New\s*\(",
    re.MULTILINE,
)
MIDDLEWARE_RE = re.compile(
    r"\b(?:router|r|app|engine)\.(?:Use|Group)\s*\(|\bgin\.Default\s*\(|\bfiber\.New\s*\(",
    re.MULTILINE,
)
ASYNC_RE = re.compile(
    r"\bgo\s+[A-Za-z_][A-Za-z0-9_.]*\s*\(|\bchan\b|\bselect\s*\{|\bWaitGroup\b"
)
DYNAMIC_RE = re.compile(r"\breflect\.|\bplugin\.Open\s*\(|\bunsafe\.")


@dataclass(slots=True)
class GoModuleParseResult:
    module: str
    file_path: str
    source: str
    source_file: SourceFileRecord
    entrypoints: list[EntrypointRecord] = field(default_factory=list)
    module_dependencies: list[ModuleDependency] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    models: list[DataModelRecord] = field(default_factory=list)
    call_graph: list[CallEdge] = field(default_factory=list)
    ir_nodes: list[IRNode] = field(default_factory=list)
    risk_nodes: list[str] = field(default_factory=list)
    middleware: list[dict[str, Any]] = field(default_factory=list)
    dynamic_calls: list[dict[str, Any]] = field(default_factory=list)
    async_flows: list[dict[str, Any]] = field(default_factory=list)
    framework_endpoints: list[dict[str, Any]] = field(default_factory=list)


class GoAdapter:
    language = "go"

    def detect_file(self, path: Path) -> bool:
        return path.suffix == ".go"

    def scan_file(self, path: Path, project_root: Path) -> ScanObservation:
        relative = path.relative_to(project_root).as_posix()
        source = path.read_text(encoding="utf-8", errors="ignore")
        observation = ScanObservation(
            languages={"go"},
            build_tools={"go"},
            dependency_managers={"go"},
        )
        if path.name == "main.go" or "package main" in source:
            observation.entrypoints.add(relative)
        frameworks = self._detect_frameworks(source)
        observation.frameworks.update(frameworks)
        if frameworks or "http.ListenAndServe" in source:
            observation.candidate_entrypoints.add(relative)
        return observation

    def analyze_project(
        self, project_root: Path, scan: ProjectScanSummary
    ) -> LanguageAnalysis:
        parsed_modules = self._parse_modules(project_root)
        result = LanguageAnalysis()
        middleware: list[dict[str, Any]] = []
        dynamic_calls: list[dict[str, Any]] = []
        async_flows: list[dict[str, Any]] = []
        framework_endpoints: list[dict[str, Any]] = []

        for parsed in parsed_modules:
            result.source_files.append(parsed.source_file)
            result.entrypoints.extend(parsed.entrypoints)
            result.module_dependencies.extend(parsed.module_dependencies)
            result.symbols.extend(parsed.symbols)
            result.models.extend(parsed.models)
            result.call_graph.extend(parsed.call_graph)
            result.ir_nodes.extend(parsed.ir_nodes)
            result.risk_nodes.extend(parsed.risk_nodes)
            middleware.extend(parsed.middleware)
            dynamic_calls.extend(parsed.dynamic_calls)
            async_flows.extend(parsed.async_flows)
            framework_endpoints.extend(parsed.framework_endpoints)

        result.project_insights.update(
            {
                "language_insights": {
                    "go": {
                        "summary": self._build_summary(
                            middleware, dynamic_calls, async_flows, framework_endpoints
                        ),
                        "migration_notes": self._build_migration_notes(
                            dynamic_calls, async_flows, framework_endpoints
                        ),
                        "high_risk_files": sorted(
                            {
                                item["path"]
                                for item in dynamic_calls
                                + async_flows
                                + framework_endpoints
                                if item.get("path")
                            }
                        ),
                        "details": {
                            "middleware": middleware,
                            "dynamic_calls": dynamic_calls,
                            "async_flows": async_flows,
                            "framework_endpoints": framework_endpoints,
                        },
                    }
                },
            }
        )
        return result

    def _parse_modules(self, project_root: Path) -> list[GoModuleParseResult]:
        parsed_modules: list[GoModuleParseResult] = []
        for path in project_root.rglob("*.go"):
            if any(
                part.startswith(".git") or part in {"vendor", ".venv", "bin"}
                for part in path.parts
            ):
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            parsed_modules.append(self._analyze_file(project_root, path, source))
        return parsed_modules

    def _analyze_file(
        self, project_root: Path, path: Path, source: str
    ) -> GoModuleParseResult:
        relative = path.relative_to(project_root).as_posix()
        package_name = self._package_name(source)
        module = (
            f"{relative[:-3].replace('/', '.')}"
            if relative.endswith(".go")
            else relative.replace("/", ".")
        )
        result = GoModuleParseResult(
            module=module,
            file_path=str(path),
            source=source,
            source_file=SourceFileRecord(
                path=relative,
                language="go",
                module=module,
                role="test" if path.name.endswith("_test.go") else "source",
            ),
        )
        if path.name == "main.go" or package_name == "main":
            result.entrypoints.append(
                EntrypointRecord(
                    path=relative, language="go", kind="entrypoint", module=module
                )
            )

        result.module_dependencies.extend(self._extract_imports(module, source))

        symbols = self._extract_symbols(module, str(path), source)
        result.symbols.extend(symbols)
        result.ir_nodes.extend(
            IRNode(
                node_id=symbol.symbol_id,
                symbol_id=symbol.symbol_id,
                node_type=symbol.kind,
                language="go",
                file_path=symbol.file_path,
                module=symbol.module,
                metadata={"signature": symbol.signature},
            )
            for symbol in symbols
        )

        result.models.extend(self._extract_models(module, str(path), source))
        result.call_graph.extend(self._extract_call_edges(module, source, symbols))
        result.middleware.extend(self._extract_middleware(module, relative, source))
        result.dynamic_calls.extend(
            self._extract_dynamic_calls(module, relative, source)
        )
        result.async_flows.extend(self._extract_async_flows(module, relative, source))
        result.framework_endpoints.extend(
            self._extract_framework_endpoints(module, relative, source)
        )

        if result.dynamic_calls or result.async_flows or result.framework_endpoints:
            result.risk_nodes.append(module)
        return result

    def _package_name(self, source: str) -> str:
        match = PACKAGE_RE.search(source)
        return match.group(1) if match else "main"

    def _extract_imports(self, module: str, source: str) -> list[ModuleDependency]:
        dependencies: list[ModuleDependency] = []
        for match in IMPORT_BLOCK_RE.finditer(source):
            for line_match in IMPORT_LINE_RE.finditer(match.group("body")):
                target = line_match.group(1) or line_match.group(2)
                dependencies.append(
                    ModuleDependency(
                        source_module=module,
                        target_module=target,
                        language="go",
                        import_kind="import",
                        symbols=[],
                    )
                )
        for match in SINGLE_IMPORT_RE.finditer(source):
            dependencies.append(
                ModuleDependency(
                    source_module=module,
                    target_module=match.group(1),
                    language="go",
                    import_kind="import",
                    symbols=[],
                )
            )
        return dependencies

    def _extract_symbols(
        self, module: str, file_path: str, source: str
    ) -> list[SymbolRecord]:
        symbols: list[SymbolRecord] = []
        for match in FUNC_RE.finditer(source):
            receiver = match.group("receiver")
            name = match.group("name")
            line = source[: match.start()].count("\n") + 1
            kind = "method" if receiver else "function"
            signature = f"func {name}({match.group('params').strip()})"
            dependencies = []
            if receiver:
                receiver_type = receiver.split()[-1].lstrip("*")
                dependencies.append(receiver_type)
            symbols.append(
                SymbolRecord(
                    symbol_id=f"{module}:{name}",
                    name=name,
                    qualname=f"{module}.{name}",
                    kind=kind,
                    language="go",
                    module=module,
                    file_path=file_path,
                    line_start=line,
                    line_end=line,
                    signature=signature,
                    dependencies=dependencies,
                )
            )
        return symbols

    def _extract_models(
        self, module: str, file_path: str, source: str
    ) -> list[DataModelRecord]:
        models: list[DataModelRecord] = []
        for match in TYPE_STRUCT_RE.finditer(source):
            fields: list[ModelField] = []
            for raw_line in match.group("body").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("//"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[0][:1].isalpha():
                    fields.append(
                        ModelField(name=parts[0], annotation=parts[1], default=None)
                    )
            models.append(
                DataModelRecord(
                    model_id=f"{module}:{match.group(1)}",
                    name=match.group(1),
                    language="go",
                    module=module,
                    file_path=file_path,
                    fields=fields,
                )
            )
        for match in INTERFACE_RE.finditer(source):
            models.append(
                DataModelRecord(
                    model_id=f"{module}:{match.group(1)}",
                    name=match.group(1),
                    language="go",
                    module=module,
                    file_path=file_path,
                    fields=[
                        ModelField(
                            name="interface", annotation="contract", default=None
                        )
                    ],
                )
            )
        return models

    def _extract_call_edges(
        self, module: str, source: str, symbols: list[SymbolRecord]
    ) -> list[CallEdge]:
        edges: list[CallEdge] = []
        symbol_names = {symbol.name for symbol in symbols}
        for symbol in symbols:
            body = self._extract_function_body(source, symbol.name)
            if not body:
                continue
            for target in symbol_names:
                if target == symbol.name:
                    continue
                if re.search(rf"\b{re.escape(target)}\s*\(", body):
                    edges.append(
                        CallEdge(source=symbol.symbol_id, target=f"{module}:{target}")
                    )
        return edges

    def _extract_function_body(self, source: str, func_name: str) -> str:
        match = re.search(
            rf"func\s+(?:\([^)]+\)\s+)?{re.escape(func_name)}\s*\([^)]*\)[^{{]*\{{",
            source,
        )
        if match is None:
            return ""
        start_index = source.find("{", match.start())
        return self._extract_block(source, start_index)

    def _extract_middleware(
        self, module: str, relative: str, source: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for match in MIDDLEWARE_RE.finditer(source):
            items.append(
                {
                    "module": module,
                    "path": relative,
                    "line": source[: match.start()].count("\n") + 1,
                    "category": "middleware_or_router_setup",
                }
            )
        return items

    def _extract_dynamic_calls(
        self, module: str, relative: str, source: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for match in DYNAMIC_RE.finditer(source):
            items.append(
                {
                    "module": module,
                    "path": relative,
                    "line": source[: match.start()].count("\n") + 1,
                    "category": "reflection_or_unsafe",
                }
            )
        return items

    def _extract_async_flows(
        self, module: str, relative: str, source: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for match in ASYNC_RE.finditer(source):
            items.append(
                {
                    "module": module,
                    "path": relative,
                    "line": source[: match.start()].count("\n") + 1,
                    "category": "goroutine_or_channel",
                }
            )
        return items

    def _extract_framework_endpoints(
        self, module: str, relative: str, source: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for match in ROUTE_RE.finditer(source):
            items.append(
                {
                    "module": module,
                    "path": relative,
                    "line": source[: match.start()].count("\n") + 1,
                    "category": "http_endpoint",
                }
            )
        return items

    def _extract_block(self, source: str, start_index: int) -> str:
        if start_index < 0 or start_index >= len(source) or source[start_index] != "{":
            return ""
        depth = 0
        for index in range(start_index, len(source)):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[start_index + 1 : index]
        return source[start_index + 1 :]

    def _detect_frameworks(self, source: str) -> set[str]:
        frameworks: set[str] = set()
        if "gin." in source or '"github.com/gin-gonic/gin"' in source:
            frameworks.add("gin")
        if "fiber." in source or '"github.com/gofiber/fiber"' in source:
            frameworks.add("fiber")
        if "echo." in source or '"github.com/labstack/echo"' in source:
            frameworks.add("echo")
        return frameworks

    def _build_summary(
        self,
        middleware: list[dict[str, Any]],
        dynamic_calls: list[dict[str, Any]],
        async_flows: list[dict[str, Any]],
        framework_endpoints: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        if framework_endpoints:
            parts.append(
                f"Detected {len(framework_endpoints)} HTTP/router registration sites"
            )
        if middleware:
            parts.append(f"Observed {len(middleware)} middleware/router setup points")
        if async_flows:
            parts.append(
                f"Found {len(async_flows)} goroutine/channel concurrency hints"
            )
        if dynamic_calls:
            parts.append(f"Flagged {len(dynamic_calls)} reflection/unsafe usages")
        return (
            "; ".join(parts)
            if parts
            else "Go static analysis completed with package/import/function heuristics."
        )

    def _build_migration_notes(
        self,
        dynamic_calls: list[dict[str, Any]],
        async_flows: list[dict[str, Any]],
        framework_endpoints: list[dict[str, Any]],
    ) -> list[str]:
        notes: list[str] = []
        if framework_endpoints:
            notes.append(
                "Preserve router registration order and handler wiring when migrating Go services."
            )
        if async_flows:
            notes.append(
                "Map goroutines, channels, and synchronization points carefully to equivalent concurrency primitives."
            )
        if dynamic_calls:
            notes.append(
                "Reflection, plugin loading, and unsafe operations may require manual redesign in target languages."
            )
        return notes
