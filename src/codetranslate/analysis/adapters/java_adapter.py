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


PACKAGE_RE = re.compile(r"\bpackage\s+([\w.]+)\s*;")
IMPORT_RE = re.compile(r"\bimport\s+(static\s+)?([\w.*]+)\s*;")
CLASS_RE = re.compile(
    r"(?P<annotations>(?:\s*@[^\n]+\n)*)\s*(?:public|protected|private|abstract|final|sealed|non-sealed|static\s+)*"
    r"(?P<kind>class|interface|enum|record)\s+(?P<name>[A-Z][A-Za-z0-9_]*)"
    r"(?:\s+extends\s+(?P<extends>[A-Za-z0-9_., <>]+))?"
    r"(?:\s+implements\s+(?P<implements>[A-Za-z0-9_., <>]+))?",
    re.MULTILINE,
)
METHOD_RE = re.compile(
    r"(?P<annotations>(?:\s*@[^\n]+\n)*)\s*(?P<signature>(?:public|protected|private|static|final|abstract|synchronized|native|default|strictfp|\s)+"
    r"(?:<[\w\s,? extends super]+>\s+)?[\w\[\]<>?, .]+\s+(?P<name>[a-zA-Z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\))\s*(?:throws\s+[\w., ]+)?\s*\{",
    re.MULTILINE,
)
FIELD_RE = re.compile(
    r"(?P<annotations>(?:\s*@[^\n]+\n)*)\s*(?:private|protected|public)?\s*(?:static\s+)?(?:final\s+)?"
    r"(?P<type>[A-Z][\w<>?, .\[\]]+|[a-z][\w<>?, .\[\]]+)\s+(?P<name>[a-zA-Z_][A-Za-z0-9_]*)\s*(?:=[^;]+)?;",
    re.MULTILINE,
)
ANNOTATION_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_.]*)")
STRING_ARG_RE = re.compile(r'"([^"]+)"')
BEAN_CALL_RE = re.compile(
    r"\b(?:applicationContext|getBeanFactory\(\))\.getBean\s*\(([^)]+)\)"
)
REFLECTION_CALL_RE = re.compile(
    r"\b(?:Class\.forName|getDeclaredMethod|getMethod|getDeclaredField|getField|Method\.invoke|Field\.get|Proxy\.newProxyInstance|"
    r"Constructor\.newInstance|newInstance\s*\()"
)
ASYNC_CALL_RE = re.compile(
    r"\b(?:CompletableFuture\.(?:runAsync|supplyAsync|completedFuture|allOf|anyOf)|@Async\b|ExecutorService\.|ScheduledExecutorService|"
    r"ThreadPoolTaskExecutor|TaskExecutor\b|Future<|Mono<|Flux<|KafkaListener\b|RabbitListener\b|JmsListener\b|@Scheduled\b|publishEvent\s*\()"
)
CALL_TOKEN_RE = re.compile(r"\b([a-zA-Z_][A-Za-z0-9_]*)\s*\(")

CLASS_ANNOTATION_KINDS = {
    "Service": "ioc_component",
    "Component": "ioc_component",
    "Repository": "ioc_component",
    "Controller": "ioc_component",
    "RestController": "ioc_component",
    "Configuration": "ioc_configuration",
    "SpringBootApplication": "entrypoint_class",
    "Entity": "entity",
    "Mapper": "mapper",
}
METHOD_ANNOTATION_KINDS = {
    "Bean": "ioc_factory",
    "Async": "async_method",
    "KafkaListener": "async_listener",
    "RabbitListener": "async_listener",
    "JmsListener": "async_listener",
    "Scheduled": "async_scheduler",
    "GetMapping": "http_endpoint",
    "PostMapping": "http_endpoint",
    "PutMapping": "http_endpoint",
    "DeleteMapping": "http_endpoint",
    "PatchMapping": "http_endpoint",
    "RequestMapping": "http_endpoint",
    "EventListener": "event_listener",
}
MIDDLEWARE_HINTS: dict[str, tuple[str, str]] = {
    "KafkaTemplate": ("kafka", "message_producer"),
    "@KafkaListener": ("kafka", "message_consumer"),
    "RabbitTemplate": ("rabbitmq", "message_producer"),
    "@RabbitListener": ("rabbitmq", "message_consumer"),
    "JmsTemplate": ("jms", "message_producer"),
    "@JmsListener": ("jms", "message_consumer"),
    "RedisTemplate": ("redis", "cache_client"),
    "StringRedisTemplate": ("redis", "cache_client"),
    "RedissonClient": ("redis", "distributed_lock"),
    "RestTemplate": ("http", "sync_http_client"),
    "WebClient": ("http", "reactive_http_client"),
    "FeignClient": ("http", "declarative_http_client"),
    "DubboReference": ("rpc", "dubbo_consumer"),
    "DubboService": ("rpc", "dubbo_provider"),
    "ElasticsearchClient": ("elasticsearch", "search_client"),
    "MongoTemplate": ("mongodb", "document_client"),
    "JdbcTemplate": ("database", "jdbc_template"),
    "MyBatis": ("database", "orm_mapper"),
    "JpaRepository": ("database", "orm_repository"),
}

JAVA_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "new",
    "super",
    "this",
    "throw",
    "synchronized",
    "try",
}


@dataclass(slots=True)
class JavaMethodRecord:
    symbol: SymbolRecord
    body: str
    annotations: list[str]
    start_index: int
    end_index: int


@dataclass(slots=True)
class JavaModuleParseResult:
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
    reflection_points: list[dict[str, Any]] = field(default_factory=list)
    dynamic_calls: list[dict[str, Any]] = field(default_factory=list)
    async_flows: list[dict[str, Any]] = field(default_factory=list)
    ioc_components: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)


class JavaAdapter:
    language = "java"

    def detect_file(self, path: Path) -> bool:
        return path.suffix == ".java"

    def scan_file(self, path: Path, project_root: Path) -> ScanObservation:
        relative = path.relative_to(project_root).as_posix()
        source = path.read_text(encoding="utf-8", errors="ignore")
        observation = ScanObservation(
            languages={"java"},
            build_tools={"maven", "gradle"},
            dependency_managers={"maven", "gradle"},
        )
        if (
            path.name in {"Application.java", "Main.java"}
            or "public static void main" in source
        ):
            observation.entrypoints.add(relative)
        annotations = set(_extract_annotations(source))
        frameworks = _detect_frameworks(source, annotations)
        observation.frameworks.update(frameworks)
        if frameworks or annotations.intersection(
            {"SpringBootApplication", "RestController", "Controller"}
        ):
            observation.candidate_entrypoints.add(relative)
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
        result = LanguageAnalysis()

        middleware: list[dict[str, Any]] = []
        reflection_points: list[dict[str, Any]] = []
        dynamic_calls: list[dict[str, Any]] = []
        async_flows: list[dict[str, Any]] = []
        ioc_components: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []

        for parsed in parsed_modules:
            result.source_files.append(parsed.source_file)
            result.entrypoints.extend(parsed.entrypoints)
            result.module_dependencies.extend(parsed.module_dependencies)
            result.symbols.extend(parsed.symbols)
            result.models.extend(parsed.models)
            result.risk_nodes.extend(parsed.risk_nodes)
            middleware.extend(parsed.middleware)
            reflection_points.extend(parsed.reflection_points)
            dynamic_calls.extend(parsed.dynamic_calls)
            async_flows.extend(parsed.async_flows)
            ioc_components.extend(parsed.ioc_components)
            annotations.extend(parsed.annotations)

        result.call_graph.extend(self._resolve_call_edges(parsed_modules, symbol_index))
        result.ir_nodes.extend(self._build_ir_nodes(parsed_modules))
        result.project_insights.update(
            {
                "java_analysis": {
                    "reflection_points": reflection_points,
                    "dynamic_calls": dynamic_calls,
                    "ioc_components": ioc_components,
                    "middleware": middleware,
                    "async_flows": async_flows,
                    "annotations": annotations,
                },
                "summary": self._build_summary(
                    reflection_points,
                    dynamic_calls,
                    ioc_components,
                    middleware,
                    async_flows,
                ),
                "migration_notes": self._build_migration_notes(
                    reflection_points, dynamic_calls, middleware, async_flows
                ),
                "high_risk_files": sorted(
                    {
                        item["path"]
                        for item in reflection_points + async_flows
                        if item.get("path")
                    }
                ),
            }
        )
        return result

    def _parse_modules(self, project_root: Path) -> list[JavaModuleParseResult]:
        parsed_modules: list[JavaModuleParseResult] = []
        for path in project_root.rglob("*.java"):
            if any(
                part.startswith(".git") or part in {"target", "build", ".venv"}
                for part in path.parts
            ):
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            parsed_modules.append(self._analyze_file(project_root, path, source))
        return parsed_modules

    def _analyze_file(
        self, project_root: Path, path: Path, source: str
    ) -> JavaModuleParseResult:
        package_name = self._package_name(source)
        module = f"{package_name}.{path.stem}" if package_name else path.stem
        relative = path.relative_to(project_root).as_posix()
        result = JavaModuleParseResult(
            module=module,
            file_path=str(path),
            source=source,
            source_file=SourceFileRecord(
                path=relative,
                language="java",
                module=module,
                role="test" if self._is_test_file(path) else "source",
            ),
        )

        imports = self._extract_imports(module, source)
        result.module_dependencies.extend(imports)

        class_symbol, class_annotations, class_kind = self._extract_class_symbol(
            module, str(path), source
        )
        if class_symbol is not None:
            result.symbols.append(class_symbol)
            result.annotations.extend(
                {
                    "target": class_symbol.symbol_id,
                    "annotation": annotation,
                    "kind": CLASS_ANNOTATION_KINDS.get(annotation, "annotation"),
                }
                for annotation in class_annotations
            )
            result.ioc_components.extend(
                self._ioc_from_class(class_symbol, class_annotations)
            )
            result.models.extend(self._extract_models(class_symbol, class_kind, source))
            result.middleware.extend(
                self._detect_middleware(
                    str(path), class_symbol.symbol_id, source, class_annotations
                )
            )
            if (
                "SpringBootApplication" in class_annotations
                or "public static void main" in source
            ):
                result.entrypoints.append(
                    EntrypointRecord(
                        path=relative, language="java", kind="bootstrap", module=module
                    )
                )
                result.risk_nodes.append(module)

        method_records = self._extract_methods(module, str(path), source)
        for method in method_records:
            result.symbols.append(method.symbol)
            result.annotations.extend(
                {
                    "target": method.symbol.symbol_id,
                    "annotation": annotation,
                    "kind": METHOD_ANNOTATION_KINDS.get(annotation, "annotation"),
                }
                for annotation in method.annotations
            )
            result.call_graph.extend(self._extract_method_edges(method, module))
            result.middleware.extend(
                self._detect_middleware(
                    str(path), method.symbol.symbol_id, method.body, method.annotations
                )
            )
            result.reflection_points.extend(
                self._detect_reflection(str(path), method.symbol.symbol_id, method.body)
            )
            result.dynamic_calls.extend(
                self._detect_dynamic_calls(
                    str(path), method.symbol.symbol_id, method.body
                )
            )
            result.async_flows.extend(
                self._detect_async_flows(
                    str(path), method.symbol.symbol_id, method.body, method.annotations
                )
            )
            if self._method_is_entrypoint(method.symbol, method.annotations):
                result.entrypoints.append(
                    EntrypointRecord(
                        path=relative, language="java", kind="handler", module=module
                    )
                )

        result.risk_nodes.extend(item["symbol_id"] for item in result.reflection_points)
        result.risk_nodes.extend(item["symbol_id"] for item in result.dynamic_calls)
        result.risk_nodes.extend(item["symbol_id"] for item in result.async_flows)
        if result.middleware:
            result.risk_nodes.append(module)
        return result

    def _extract_imports(self, module: str, source: str) -> list[ModuleDependency]:
        dependencies: list[ModuleDependency] = []
        for match in IMPORT_RE.finditer(source):
            target = match.group(2)
            dependencies.append(
                ModuleDependency(
                    source_module=module,
                    target_module=target.removesuffix(".*"),
                    language="java",
                    import_kind="static_import" if match.group(1) else "import",
                    symbols=[] if target.endswith(".*") else [target.split(".")[-1]],
                )
            )
        return dependencies

    def _extract_class_symbol(
        self, module: str, file_path: str, source: str
    ) -> tuple[SymbolRecord | None, list[str], str | None]:
        match = CLASS_RE.search(source)
        if match is None:
            return None, [], None
        name = match.group("name")
        annotations = _extract_annotations(match.group("annotations") or "")
        bases = []
        if match.group("extends"):
            bases.extend(_split_types(match.group("extends")))
        if match.group("implements"):
            bases.extend(_split_types(match.group("implements")))
        line = source[: match.start()].count("\n") + 1
        kind = match.group("kind")
        symbol = SymbolRecord(
            symbol_id=f"{module}:{name}",
            name=name,
            qualname=f"{module}.{name}",
            kind=kind,
            language="java",
            module=module,
            file_path=file_path,
            line_start=line,
            line_end=line,
            signature=f"{kind} {name}",
            decorators=annotations,
            bases=bases,
        )
        return symbol, annotations, kind

    def _extract_methods(
        self, module: str, file_path: str, source: str
    ) -> list[JavaMethodRecord]:
        methods: list[JavaMethodRecord] = []
        for match in METHOD_RE.finditer(source):
            name = match.group("name")
            if name in {"if", "for", "while", "switch", "catch"}:
                continue
            body_start = match.end() - 1
            body_end = _find_matching_brace(source, body_start)
            body = source[body_start + 1 : body_end] if body_end > body_start else ""
            line = source[: match.start()].count("\n") + 1
            annotations = _extract_annotations(match.group("annotations") or "")
            methods.append(
                JavaMethodRecord(
                    symbol=SymbolRecord(
                        symbol_id=f"{module}:{name}",
                        name=name,
                        qualname=f"{module}.{name}",
                        kind="method",
                        language="java",
                        module=module,
                        file_path=file_path,
                        line_start=line,
                        line_end=line + body.count("\n"),
                        signature=_normalize_space(match.group("signature")),
                        decorators=annotations,
                    ),
                    body=body,
                    annotations=annotations,
                    start_index=body_start,
                    end_index=body_end,
                )
            )
        return methods

    def _extract_method_edges(
        self, method: JavaMethodRecord, module: str
    ) -> list[CallEdge]:
        edges: list[CallEdge] = []
        seen: set[str] = set()
        for target in CALL_TOKEN_RE.findall(method.body):
            if target in JAVA_KEYWORDS:
                continue
            resolved = f"{module}:{target}"
            if resolved in seen:
                continue
            seen.add(resolved)
            kind = (
                "dynamic_call"
                if target in {"invoke", "getBean", "forName", "newProxyInstance"}
                else "call"
            )
            edges.append(
                CallEdge(source=method.symbol.symbol_id, target=resolved, kind=kind)
            )
        return edges

    def _resolve_call_edges(
        self,
        parsed_modules: list[JavaModuleParseResult],
        symbol_index: dict[str, SymbolRecord],
    ) -> list[CallEdge]:
        symbol_ids = set(symbol_index)
        resolved: list[CallEdge] = []
        for parsed in parsed_modules:
            for edge in parsed.call_graph:
                if edge.target in symbol_ids:
                    resolved.append(edge)
                    continue
                short_name = edge.target.split(":", 1)[-1]
                matches = [
                    symbol_id
                    for symbol_id in symbol_ids
                    if symbol_id.endswith(f":{short_name}")
                ]
                resolved.append(
                    CallEdge(
                        source=edge.source,
                        target=matches[0] if len(matches) == 1 else edge.target,
                        kind=edge.kind,
                    )
                )
        return resolved

    def _build_ir_nodes(
        self, parsed_modules: list[JavaModuleParseResult]
    ) -> list[IRNode]:
        nodes: list[IRNode] = []
        for parsed in parsed_modules:
            for symbol in parsed.symbols:
                metadata: dict[str, Any] = {
                    "signature": symbol.signature,
                    "decorators": symbol.decorators,
                    "bases": symbol.bases,
                }
                if symbol.decorators:
                    metadata["java_annotation_analysis"] = True
                nodes.append(
                    IRNode(
                        node_id=symbol.symbol_id,
                        symbol_id=symbol.symbol_id,
                        node_type=symbol.kind,
                        language="java",
                        file_path=symbol.file_path,
                        module=symbol.module,
                        metadata=metadata,
                    )
                )
        return nodes

    def _extract_models(
        self, class_symbol: SymbolRecord, class_kind: str | None, source: str
    ) -> list[DataModelRecord]:
        if class_kind not in {"class", "record"}:
            return []
        looks_like_model = bool(
            set(class_symbol.decorators).intersection(
                {"Entity", "Document", "Table", "Data", "Value", "Embeddable"}
            )
            or any(
                base.endswith(("Repository", "Entity", "DTO", "VO"))
                for base in class_symbol.bases
            )
        )
        fields = self._extract_fields(source)
        if not looks_like_model and not fields:
            return []
        return [
            DataModelRecord(
                model_id=class_symbol.symbol_id,
                name=class_symbol.name,
                language="java",
                module=class_symbol.module,
                file_path=class_symbol.file_path,
                fields=fields,
            )
        ]

    def _extract_fields(self, source: str) -> list[ModelField]:
        fields: list[ModelField] = []
        seen: set[str] = set()
        for match in FIELD_RE.finditer(source):
            name = match.group("name")
            if name in seen:
                continue
            seen.add(name)
            fields.append(
                ModelField(
                    name=name,
                    annotation=_normalize_space(match.group("type")),
                    default=None,
                )
            )
        return fields

    def _ioc_from_class(
        self, symbol: SymbolRecord, annotations: list[str]
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for annotation in annotations:
            kind = CLASS_ANNOTATION_KINDS.get(annotation)
            if kind and annotation in {
                "Service",
                "Component",
                "Repository",
                "Controller",
                "RestController",
                "Configuration",
                "SpringBootApplication",
                "Mapper",
            }:
                entries.append(
                    {
                        "symbol_id": symbol.symbol_id,
                        "name": symbol.name,
                        "module": symbol.module,
                        "annotation": annotation,
                        "kind": kind,
                    }
                )
        return entries

    def _detect_reflection(
        self, path: str, symbol_id: str, body: str
    ) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        if not REFLECTION_CALL_RE.search(body):
            return points
        for token in [
            "Class.forName",
            "getDeclaredMethod",
            "getMethod",
            "Method.invoke",
            "Proxy.newProxyInstance",
            "newInstance",
        ]:
            if token in body:
                points.append(
                    {
                        "path": path,
                        "symbol_id": symbol_id,
                        "mechanism": token,
                        "category": "reflection",
                    }
                )
        return points

    def _detect_dynamic_calls(
        self, path: str, symbol_id: str, body: str
    ) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for match in BEAN_CALL_RE.finditer(body):
            args = _normalize_space(match.group(1))
            calls.append(
                {
                    "path": path,
                    "symbol_id": symbol_id,
                    "mechanism": "ioc_getBean",
                    "details": args,
                }
            )
        if "InvocationHandler" in body or "invoke(" in body and "Method" in body:
            calls.append(
                {
                    "path": path,
                    "symbol_id": symbol_id,
                    "mechanism": "proxy_dispatch",
                    "details": "runtime proxy invocation",
                }
            )
        if "ApplicationEventPublisher" in body or "publishEvent(" in body:
            calls.append(
                {
                    "path": path,
                    "symbol_id": symbol_id,
                    "mechanism": "event_dispatch",
                    "details": "application event publication",
                }
            )
        return calls

    def _detect_async_flows(
        self, path: str, symbol_id: str, body: str, annotations: list[str]
    ) -> list[dict[str, Any]]:
        flows: list[dict[str, Any]] = []
        if not ASYNC_CALL_RE.search(body) and not set(annotations).intersection(
            {
                "Async",
                "KafkaListener",
                "RabbitListener",
                "JmsListener",
                "Scheduled",
                "EventListener",
            }
        ):
            return flows
        if "CompletableFuture" in body:
            flows.append(
                {
                    "path": path,
                    "symbol_id": symbol_id,
                    "mechanism": "completable_future",
                    "kind": "async_chain",
                }
            )
        if any(annotation in annotations for annotation in {"Async", "Scheduled"}):
            flows.append(
                {
                    "path": path,
                    "symbol_id": symbol_id,
                    "mechanism": "spring_async_annotation",
                    "kind": "async_annotation",
                }
            )
        for annotation in {
            "KafkaListener",
            "RabbitListener",
            "JmsListener",
            "EventListener",
        }:
            if annotation in annotations:
                flows.append(
                    {
                        "path": path,
                        "symbol_id": symbol_id,
                        "mechanism": annotation,
                        "kind": "async_listener",
                    }
                )
        if any(
            token in body
            for token in [
                "ExecutorService",
                "TaskExecutor",
                "ThreadPoolTaskExecutor",
                "publishEvent(",
            ]
        ):
            flows.append(
                {
                    "path": path,
                    "symbol_id": symbol_id,
                    "mechanism": "executor_or_event_bus",
                    "kind": "async_dispatch",
                }
            )
        return flows

    def _detect_middleware(
        self, path: str, symbol_id: str, source: str, annotations: list[str]
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        observed_tokens = set(annotations)
        observed_tokens.update(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", source))
        for token, (middleware, role) in MIDDLEWARE_HINTS.items():
            normalized_token = token.removeprefix("@")
            if token in source or normalized_token in observed_tokens:
                hits.append(
                    {
                        "path": path,
                        "symbol_id": symbol_id,
                        "middleware": middleware,
                        "role": role,
                        "evidence": token,
                    }
                )
        return hits

    def _method_is_entrypoint(
        self, symbol: SymbolRecord, annotations: list[str]
    ) -> bool:
        if (
            symbol.name == "main"
            and symbol.signature
            and "String[]" in symbol.signature
        ):
            return True
        return bool(
            set(annotations).intersection(
                {
                    "GetMapping",
                    "PostMapping",
                    "PutMapping",
                    "DeleteMapping",
                    "PatchMapping",
                    "RequestMapping",
                    "KafkaListener",
                    "RabbitListener",
                    "JmsListener",
                    "Scheduled",
                }
            )
        )

    def _package_name(self, source: str) -> str:
        match = PACKAGE_RE.search(source)
        return match.group(1) if match else ""

    def _is_test_file(self, path: Path) -> bool:
        return "src/test/" in path.as_posix() or path.name.endswith("Test.java")

    def _build_summary(
        self,
        reflection_points: list[dict[str, Any]],
        dynamic_calls: list[dict[str, Any]],
        ioc_components: list[dict[str, Any]],
        middleware: list[dict[str, Any]],
        async_flows: list[dict[str, Any]],
    ) -> str:
        return (
            "Java adapter enriched analysis with "
            f"{len(ioc_components)} IoC/annotation components, "
            f"{len(reflection_points)} reflection points, "
            f"{len(dynamic_calls)} dynamic dispatch candidates, "
            f"{len(middleware)} middleware integrations, and "
            f"{len(async_flows)} async flow hints."
        )

    def _build_migration_notes(
        self,
        reflection_points: list[dict[str, Any]],
        dynamic_calls: list[dict[str, Any]],
        middleware: list[dict[str, Any]],
        async_flows: list[dict[str, Any]],
    ) -> list[str]:
        notes: list[str] = []
        if reflection_points:
            notes.append(
                "Java reflection hotspots detected; target migration needs runtime factory or metadata-based replacement."
            )
        if dynamic_calls:
            notes.append(
                "IoC/event/proxy dynamic dispatch exists; preserve bean lookup and runtime routing semantics during migration."
            )
        if middleware:
            notes.append(
                "Middleware dependencies were identified from code symbols and annotations; include adapter mapping for infra semantics, not only API names."
            )
        if async_flows:
            notes.append(
                "Async chains include executor, listener, or scheduled patterns; reconstruct producer/consumer boundaries in the target architecture."
            )
        return notes


def _extract_annotations(text: str) -> list[str]:
    return [item.split(".")[-1] for item in ANNOTATION_RE.findall(text)]


def _split_types(raw: str) -> list[str]:
    return [_normalize_space(item) for item in raw.split(",") if _normalize_space(item)]


def _normalize_space(text: str) -> str:
    return " ".join(text.split())


def _find_matching_brace(source: str, opening_index: int) -> int:
    depth = 0
    for index in range(opening_index, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return opening_index


def _detect_frameworks(source: str, annotations: set[str]) -> set[str]:
    frameworks: set[str] = set()
    if {
        "SpringBootApplication",
        "Service",
        "Repository",
        "RestController",
        "Autowired",
    }.intersection(annotations) or "org.springframework" in source:
        frameworks.add("spring")
    if (
        "javax.persistence" in source
        or "jakarta.persistence" in source
        or "Entity" in annotations
    ):
        frameworks.add("jpa")
    if "Mapper" in annotations or "org.apache.ibatis" in source:
        frameworks.add("mybatis")
    if "lombok" in source or annotations.intersection({"Data", "Builder", "Value"}):
        frameworks.add("lombok")
    if "reactor.core" in source or any(token in source for token in ["Mono<", "Flux<"]):
        frameworks.add("reactor")
    return frameworks
