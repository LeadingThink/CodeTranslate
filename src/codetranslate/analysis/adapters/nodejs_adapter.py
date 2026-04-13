from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

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


IMPORT_RE = re.compile(
    r"""(?:
        import\s+(?:type\s+)?(?P<import_bindings>[\w*\s{},]+)\s+from\s+['"](?P<import_from>[^'"]+)['"] |
        import\s+['"](?P<side_effect_import>[^'"]+)['"] |
        (?:const|let|var)\s+(?P<require_bindings>[\w{}\s,*]+)\s*=\s*require\(\s*['"](?P<require_from>[^'"]+)['"]\s*\)
    )""",
    re.VERBOSE,
)
EXPORT_FUNCTION_RE = re.compile(r"export\s+(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(")
EXPORT_CLASS_RE = re.compile(r"export\s+class\s+([A-Za-z_]\w*)")
EXPORT_CONST_RE = re.compile(r"export\s+(?:const|let|var)\s+([A-Za-z_]\w*)")
MODULE_EXPORTS_RE = re.compile(r"module\.exports\s*=\s*{([^}]*)}", re.DOTALL)
ROUTE_RE = re.compile(r"\b(?:app|router)\.(get|post|put|delete|patch|use)\s*\(")
DATA_MODEL_RE = re.compile(r"\b(?:interface|type)\s+([A-Z][A-Za-z0-9_]*)\b|\bz\.object\s*\(|Schema\b|DTO\b")


@dataclass(slots=True)
class NodeModuleParseResult:
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


class NodeJsAdapter:
    language = "nodejs"
    SUPPORTED_SUFFIXES = {".js", ".mjs", ".cjs", ".ts"}
    ENTRYPOINT_NAMES = {
        "index.js",
        "main.js",
        "app.js",
        "server.js",
        "index.ts",
        "main.ts",
        "app.ts",
        "server.ts",
    }

    def detect_file(self, path: Path) -> bool:
        return path.suffix in self.SUPPORTED_SUFFIXES

    def scan_file(self, path: Path, project_root: Path) -> ScanObservation:
        relative = path.relative_to(project_root).as_posix()
        observation = ScanObservation(
            languages={"nodejs"},
            build_tools={"npm", "pnpm", "yarn"},
            dependency_managers={"npm", "pnpm", "yarn"},
        )
        source = path.read_text(encoding="utf-8", errors="ignore")
        if path.name in self.ENTRYPOINT_NAMES:
            observation.entrypoints.add(relative)
        if any(token in source for token in ("express(", "koa(", "@nestjs", "createServer(", "fastify(")):
            observation.frameworks.update(self._detect_frameworks(source))
            observation.candidate_entrypoints.add(relative)
        if path.name.endswith((".sh", ".bash")) or "process.argv" in source or "require.main === module" in source:
            observation.candidate_entrypoints.add(relative)
        return observation

    def analyze_project(self, project_root: Path, scan: ProjectScanSummary) -> LanguageAnalysis:
        parsed_modules = self._parse_modules(project_root)
        result = LanguageAnalysis()

        for parsed in parsed_modules:
            result.source_files.append(parsed.source_file)
            result.entrypoints.extend(parsed.entrypoints)
            result.module_dependencies.extend(parsed.module_dependencies)
            result.symbols.extend(parsed.symbols)
            result.models.extend(parsed.models)
            result.call_graph.extend(parsed.call_graph)
            result.ir_nodes.extend(parsed.ir_nodes)
            result.risk_nodes.extend(parsed.risk_nodes)
        return result

    def _parse_modules(self, project_root: Path) -> list[NodeModuleParseResult]:
        parsed_modules: list[NodeModuleParseResult] = []
        for path in project_root.rglob("*"):
            if not path.is_file() or path.suffix not in self.SUPPORTED_SUFFIXES:
                continue
            if any(part.startswith(".git") or part == "node_modules" or part == ".venv" for part in path.parts):
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            module = self._module_name(path, project_root)
            parsed_modules.append(self._analyze_file(project_root, path, module, source))
        return parsed_modules

    def _analyze_file(self, project_root: Path, path: Path, module: str, source: str) -> NodeModuleParseResult:
        relative = path.relative_to(project_root).as_posix()
        result = NodeModuleParseResult(
            module=module,
            file_path=str(path),
            source=source,
            source_file=SourceFileRecord(
                path=relative,
                language="nodejs",
                module=module,
                role="test" if self._is_test_file(path) else "source",
            ),
        )
        if self._is_entrypoint(path, source):
            result.entrypoints.append(EntrypointRecord(path=relative, language="nodejs", kind="entrypoint", module=module))

        for dependency in self._extract_imports(module, source):
            result.module_dependencies.append(dependency)

        for symbol in self._extract_symbols(module, str(path), source):
            result.symbols.append(symbol)
            result.ir_nodes.append(
                IRNode(
                    node_id=symbol.symbol_id,
                    symbol_id=symbol.symbol_id,
                    node_type=symbol.kind,
                    language="nodejs",
                    file_path=symbol.file_path,
                    module=symbol.module,
                    metadata={"signature": symbol.signature},
                )
            )

        result.models.extend(self._extract_models(module, str(path), source))
        result.call_graph.extend(self._extract_call_edges(module, source, result.symbols))
        result.risk_nodes.extend(self._extract_risks(module, source))
        return result

    def _extract_imports(self, module: str, source: str) -> list[ModuleDependency]:
        dependencies: list[ModuleDependency] = []
        for match in IMPORT_RE.finditer(source):
            target = match.group("import_from") or match.group("side_effect_import") or match.group("require_from")
            if not target:
                continue
            symbols = self._parse_import_symbols(match.group("import_bindings") or match.group("require_bindings") or "")
            dependencies.append(
                ModuleDependency(
                    source_module=module,
                    target_module=self._normalize_import_target(module, target),
                    language="nodejs",
                    import_kind="import",
                    symbols=symbols,
                )
            )
        return dependencies

    def _extract_symbols(self, module: str, file_path: str, source: str) -> list[SymbolRecord]:
        symbols: list[SymbolRecord] = []
        seen: set[str] = set()
        for regex, kind in (
            (EXPORT_FUNCTION_RE, "function"),
            (EXPORT_CLASS_RE, "class"),
            (EXPORT_CONST_RE, "variable"),
        ):
            for match in regex.finditer(source):
                name = match.group(1)
                if name in seen:
                    continue
                seen.add(name)
                line = source[: match.start()].count("\n") + 1
                signature = f"{name}(...)" if kind == "function" else name
                symbols.append(
                    SymbolRecord(
                        symbol_id=f"{module}:{name}",
                        name=name,
                        qualname=f"{module}.{name}",
                        kind=kind,
                        language="nodejs",
                        module=module,
                        file_path=file_path,
                        line_start=line,
                        line_end=line,
                        signature=signature,
                    )
                )

        exports_match = MODULE_EXPORTS_RE.search(source)
        if exports_match:
            for raw_name in exports_match.group(1).split(","):
                name = raw_name.strip().split(":")[0].strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                line = source[: exports_match.start()].count("\n") + 1
                symbols.append(
                    SymbolRecord(
                        symbol_id=f"{module}:{name}",
                        name=name,
                        qualname=f"{module}.{name}",
                        kind="export",
                        language="nodejs",
                        module=module,
                        file_path=file_path,
                        line_start=line,
                        line_end=line,
                        signature=name,
                    )
                )
        return symbols

    def _extract_models(self, module: str, file_path: str, source: str) -> list[DataModelRecord]:
        models: list[DataModelRecord] = []
        for match in re.finditer(r"\b(?:interface|type)\s+([A-Z][A-Za-z0-9_]*)", source):
            name = match.group(1)
            models.append(
                DataModelRecord(
                    model_id=f"{module}:{name}",
                    name=name,
                    language="nodejs",
                    module=module,
                    file_path=file_path,
                    fields=[],
                )
            )
        if "z.object(" in source or "Schema" in source:
            models.append(
                DataModelRecord(
                    model_id=f"{module}:__schema__",
                    name=f"{Path(file_path).stem}Schema",
                    language="nodejs",
                    module=module,
                    file_path=file_path,
                    fields=[ModelField(name="schema", annotation="object", default=None)],
                )
            )
        return models

    def _extract_call_edges(self, module: str, source: str, symbols: list[SymbolRecord]) -> list[CallEdge]:
        edges: list[CallEdge] = []
        symbol_names = {symbol.name for symbol in symbols}
        for symbol in symbols:
            body_match = re.search(rf"{re.escape(symbol.name)}\s*[=:]?\s*(?:async\s*)?(?:function)?[^(]*\(", source)
            if body_match is None:
                continue
            source_name = symbol.symbol_id
            for target in symbol_names:
                if target == symbol.name:
                    continue
                if re.search(rf"\b{re.escape(target)}\s*\(", source):
                    edges.append(CallEdge(source=source_name, target=f"{module}:{target}"))
        return edges

    def _extract_risks(self, module: str, source: str) -> list[str]:
        risks: list[str] = []
        risky_markers = [
            "eval(",
            "child_process",
            "process.env",
            "fs.readFileSync",
            "dynamic import(",
            "require(",
        ]
        if any(marker in source for marker in risky_markers):
            risks.append(module)
        if ROUTE_RE.search(source):
            risks.append(module)
        return risks

    def _normalize_import_target(self, current_module: str, raw_target: str) -> str:
        if not raw_target.startswith("."):
            return raw_target
        parts = current_module.split(".")[:-1]
        while raw_target.startswith("../"):
            raw_target = raw_target[3:]
            if parts:
                parts.pop()
        if raw_target.startswith("./"):
            raw_target = raw_target[2:]
        if raw_target:
            parts.append(raw_target.replace("/", "."))
        return ".".join(part for part in parts if part)

    def _parse_import_symbols(self, raw_bindings: str) -> list[str]:
        bindings = raw_bindings.strip()
        if not bindings:
            return []
        bindings = bindings.strip("{}")
        return [item.strip().split(" as ")[0] for item in bindings.split(",") if item.strip() and item.strip() != "*"]

    def _is_test_file(self, path: Path) -> bool:
        return any(token in path.name.lower() for token in ("test", "spec")) or "__tests__" in path.parts

    def _is_entrypoint(self, path: Path, source: str) -> bool:
        return path.name in self.ENTRYPOINT_NAMES or "require.main === module" in source or bool(ROUTE_RE.search(source))

    def _detect_frameworks(self, source: str) -> set[str]:
        frameworks: set[str] = set()
        if "express" in source:
            frameworks.add("express")
        if "koa" in source:
            frameworks.add("koa")
        if "@nestjs" in source:
            frameworks.add("nestjs")
        if "fastify" in source:
            frameworks.add("fastify")
        return frameworks

    def _module_name(self, path: Path, project_root: Path) -> str:
        relative = path.relative_to(project_root)
        return relative.with_suffix("").as_posix().replace("/", ".")
