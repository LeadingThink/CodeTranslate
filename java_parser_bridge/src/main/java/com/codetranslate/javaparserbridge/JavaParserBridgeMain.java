package com.codetranslate.javaparserbridge;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.BodyDeclaration;
import com.github.javaparser.ast.body.CallableDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.EnumDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.RecordDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.nodeTypes.NodeWithAnnotations;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public final class JavaParserBridgeMain {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final Set<String> IOC_ANNOTATIONS = Set.of(
        "Service", "Component", "Repository", "Controller", "RestController", "Configuration", "SpringBootApplication", "Mapper"
    );
    private static final Set<String> ENTRYPOINT_METHOD_ANNOTATIONS = Set.of(
        "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping", "KafkaListener", "RabbitListener", "JmsListener", "Scheduled"
    );

    public static void main(String[] args) throws Exception {
        Map<String, String> cli = parseArgs(args);
        Path projectRoot = Path.of(requiredArg(cli, "--project-root")).toAbsolutePath().normalize();
        StaticJavaParser.setConfiguration(new ParserConfiguration().setLanguageLevel(ParserConfiguration.LanguageLevel.BLEEDING_EDGE));
        Map<String, Object> payload = new BridgeAnalyzer(projectRoot).analyze();
        OBJECT_MAPPER.writeValue(System.out, payload);
    }

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> values = new HashMap<>();
        for (int index = 0; index < args.length; index += 2) {
            if (index + 1 >= args.length) {
                throw new IllegalArgumentException("missing value for " + args[index]);
            }
            values.put(args[index], args[index + 1]);
        }
        return values;
    }

    private static String requiredArg(Map<String, String> values, String key) {
        String value = values.get(key);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("missing required argument: " + key);
        }
        return value;
    }

    private static final class BridgeAnalyzer {
        private final Path projectRoot;
        private final Map<String, Set<String>> packageTypeIndex = new HashMap<>();
        private final List<ParsedJavaFile> files = new ArrayList<>();
        private final List<IndexedCompilationUnit> indexedUnits = new ArrayList<>();

        private BridgeAnalyzer(Path projectRoot) {
            this.projectRoot = projectRoot;
        }

        private Map<String, Object> analyze() throws IOException {
            indexFiles();
            List<Map<String, Object>> sourceFiles = new ArrayList<>();
            List<Map<String, Object>> moduleDependencies = new ArrayList<>();
            List<Map<String, Object>> entrypoints = new ArrayList<>();
            List<Map<String, Object>> symbols = new ArrayList<>();
            List<Map<String, Object>> models = new ArrayList<>();
            List<Map<String, Object>> callGraph = new ArrayList<>();
            Set<String> riskNodes = new LinkedHashSet<>();
            Map<String, List<Map<String, Object>>> details = new LinkedHashMap<>();
            details.put("middleware", new ArrayList<>());
            details.put("reflection_points", new ArrayList<>());
            details.put("dynamic_calls", new ArrayList<>());
            details.put("async_flows", new ArrayList<>());
            details.put("ioc_components", new ArrayList<>());
            details.put("annotations", new ArrayList<>());

            for (ParsedJavaFile file : files) {
                sourceFiles.add(file.sourceFile);
                moduleDependencies.addAll(file.moduleDependencies);
                entrypoints.addAll(file.entrypoints);
                symbols.addAll(file.symbols);
                models.addAll(file.models);
                callGraph.addAll(file.callGraph);
                riskNodes.addAll(file.riskNodes);
                details.get("middleware").addAll(file.middleware);
                details.get("reflection_points").addAll(file.reflectionPoints);
                details.get("dynamic_calls").addAll(file.dynamicCalls);
                details.get("async_flows").addAll(file.asyncFlows);
                details.get("ioc_components").addAll(file.iocComponents);
                details.get("annotations").addAll(file.annotations);
            }

            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("source_files", sourceFiles);
            payload.put("module_dependencies", dedupeDependencyMaps(moduleDependencies));
            payload.put("entrypoints", entrypoints);
            payload.put("symbols", symbols);
            payload.put("models", models);
            payload.put("call_graph", resolveCallGraph(callGraph, symbols));
            payload.put("risk_nodes", new ArrayList<>(riskNodes));
            payload.put("details", details);
            return payload;
        }

        private void indexFiles() throws IOException {
            try (Stream<Path> stream = Files.walk(projectRoot)) {
                List<Path> javaFiles = stream
                    .filter(Files::isRegularFile)
                    .filter(path -> path.toString().endsWith(".java"))
                    .filter(path -> !isExcluded(path))
                    .sorted()
                    .toList();

                for (Path path : javaFiles) {
                    CompilationUnit unit = StaticJavaParser.parse(path);
                    String packageName = unit.getPackageDeclaration().map(pkg -> pkg.getNameAsString()).orElse("");
                    indexedUnits.add(new IndexedCompilationUnit(path, unit, packageName));
                    for (TypeDeclaration<?> type : unit.getTypes()) {
                        packageTypeIndex.computeIfAbsent(packageName, ignored -> new HashSet<>()).add(type.getNameAsString());
                    }
                }
                for (IndexedCompilationUnit indexedUnit : indexedUnits) {
                    files.add(parseFile(indexedUnit.path, indexedUnit.unit, indexedUnit.packageName));
                }
            }
        }

        private ParsedJavaFile parseFile(Path path, CompilationUnit unit, String packageName) {
            String relativePath = projectRoot.relativize(path).toString().replace('\\', '/');
            String moduleName = moduleName(packageName, path);
            ParsedJavaFile parsed = new ParsedJavaFile();
            parsed.sourceFile = mapOf(
                "path", relativePath,
                "language", "java",
                "module", moduleName,
                "role", isTestFile(path) ? "test" : "source"
            );

            for (ImportDeclaration importDeclaration : unit.getImports()) {
                String target = importDeclaration.getNameAsString();
                parsed.moduleDependencies.add(mapOf(
                    "source_module", moduleName,
                    "target_module", importDeclaration.isAsterisk() ? target : target,
                    "language", "java",
                    "import_kind", importDeclaration.isStatic() ? "static_import" : "import",
                    "symbols", importDeclaration.isAsterisk()
                        ? List.of()
                        : List.of(target.substring(target.lastIndexOf('.') + 1))
                ));
            }

            Set<String> samePackageDependencies = findSamePackageDependencies(unit, packageName, path.getFileName().toString().replace(".java", ""));
            for (String dependency : samePackageDependencies) {
                parsed.moduleDependencies.add(mapOf(
                    "source_module", moduleName,
                    "target_module", packageName.isBlank() ? dependency : packageName + "." + dependency,
                    "language", "java",
                    "import_kind", "same_package_type",
                    "symbols", List.of(dependency)
                ));
            }

            List<String> compilationAnnotations = collectAnnotations(unit);
            if (compilationAnnotations.contains("SpringBootApplication") || containsMainMethod(unit)) {
                parsed.entrypoints.add(mapOf(
                    "path", relativePath,
                    "language", "java",
                    "kind", "bootstrap",
                    "module", moduleName
                ));
                parsed.riskNodes.add(moduleName);
            }

            for (TypeDeclaration<?> type : unit.getTypes()) {
                parseType(path, relativePath, moduleName, type, parsed);
            }
            parsed.riskNodes.addAll(parsed.reflectionPoints.stream().map(item -> (String) item.get("symbol_id")).toList());
            parsed.riskNodes.addAll(parsed.dynamicCalls.stream().map(item -> (String) item.get("symbol_id")).toList());
            parsed.riskNodes.addAll(parsed.asyncFlows.stream().map(item -> (String) item.get("symbol_id")).toList());
            if (!parsed.middleware.isEmpty()) {
                parsed.riskNodes.add(moduleName);
            }
            return parsed;
        }

        private void parseType(Path path, String relativePath, String moduleName, TypeDeclaration<?> type, ParsedJavaFile parsed) {
            String typeName = type.getNameAsString();
            String symbolId = moduleName + ":" + typeName;
            List<String> annotations = collectAnnotations(type);
            String kind = typeKind(type);
            List<String> bases = extractBases(type);
            parsed.symbols.add(mapSymbol(symbolId, typeName, moduleName, path, kind, type.toString().split("\\R", 2)[0], annotations, bases, type));
            for (String annotation : annotations) {
                parsed.annotations.add(mapOf("target", symbolId, "annotation", annotation, "kind", annotationKind(annotation, true)));
            }
            if (annotations.stream().anyMatch(IOC_ANNOTATIONS::contains)) {
                parsed.iocComponents.add(mapOf(
                    "symbol_id", symbolId,
                    "name", typeName,
                    "module", moduleName,
                    "annotation", annotations.stream().filter(IOC_ANNOTATIONS::contains).findFirst().orElse(""),
                    "kind", "ioc_component"
                ));
            }
            if (looksLikeModel(type, annotations)) {
                parsed.models.add(mapOf(
                    "model_id", symbolId,
                    "name", typeName,
                    "language", "java",
                    "module", moduleName,
                    "file_path", path.toString(),
                    "fields", extractFields(type)
                ));
            }

            if (annotations.contains("SpringBootApplication")) {
                parsed.entrypoints.add(mapOf("path", relativePath, "language", "java", "kind", "bootstrap", "module", moduleName));
            }

            for (BodyDeclaration<?> member : type.getMembers()) {
                if (member instanceof MethodDeclaration method) {
                    parseCallable(path, relativePath, moduleName, method, parsed);
                } else if (member instanceof ConstructorDeclaration constructor) {
                    parseCallable(path, relativePath, moduleName, constructor, parsed);
                }
            }
        }

        private void parseCallable(Path path, String relativePath, String moduleName, CallableDeclaration<?> callable, ParsedJavaFile parsed) {
            String name = callable.getNameAsString();
            String symbolId = moduleName + ":" + name;
            List<String> annotations = collectAnnotations(callable);
            parsed.symbols.add(mapSymbol(symbolId, name, moduleName, path, callable instanceof ConstructorDeclaration ? "constructor" : "method", callable.getDeclarationAsString(false, false, true), annotations, List.of(), callable));
            for (String annotation : annotations) {
                parsed.annotations.add(mapOf("target", symbolId, "annotation", annotation, "kind", annotationKind(annotation, false)));
            }
            if (isEntrypointMethod(callable, annotations)) {
                parsed.entrypoints.add(mapOf("path", relativePath, "language", "java", "kind", "handler", "module", moduleName));
            }
            if (callable.findAll(MethodCallExpr.class).stream().anyMatch(call -> "getBean".equals(call.getNameAsString()))) {
                parsed.dynamicCalls.add(mapOf("path", path.toString(), "symbol_id", symbolId, "mechanism", "ioc_getBean", "details", "ApplicationContext#getBean"));
            }
            if (callable.findAll(MethodCallExpr.class).stream().anyMatch(call -> "publishEvent".equals(call.getNameAsString()))) {
                parsed.dynamicCalls.add(mapOf("path", path.toString(), "symbol_id", symbolId, "mechanism", "event_dispatch", "details", "ApplicationEventPublisher#publishEvent"));
            }
            if (containsReflection(callable)) {
                parsed.reflectionPoints.add(mapOf("path", path.toString(), "symbol_id", symbolId, "mechanism", "reflection", "category", "reflection"));
            }
            if (containsAsync(callable, annotations)) {
                parsed.asyncFlows.add(mapOf("path", path.toString(), "symbol_id", symbolId, "mechanism", "async_flow", "kind", "async_dispatch"));
            }
            detectMiddleware(path, symbolId, callable.toString(), annotations, parsed.middleware);
            for (MethodCallExpr call : callable.findAll(MethodCallExpr.class)) {
                parsed.callGraph.add(mapOf(
                    "source", symbolId,
                    "target", moduleName + ":" + call.getNameAsString(),
                    "kind", dynamicCallKind(call.getNameAsString())
                ));
            }
            for (ObjectCreationExpr creation : callable.findAll(ObjectCreationExpr.class)) {
                String targetType = creation.getType().getNameAsString();
                parsed.callGraph.add(mapOf("source", symbolId, "target", moduleName + ":" + targetType, "kind", "constructor_call"));
            }
        }

        private Set<String> findSamePackageDependencies(CompilationUnit unit, String packageName, String currentTypeName) {
            if (packageName.isBlank()) {
                return Set.of();
            }
            Set<String> declared = packageTypeIndex.getOrDefault(packageName, Set.of());
            if (declared.isEmpty()) {
                return Set.of();
            }
            Set<String> referenced = new LinkedHashSet<>();
            for (ClassOrInterfaceType type : unit.findAll(ClassOrInterfaceType.class)) {
                referenced.add(type.getName().asString());
            }
            referenced.remove(currentTypeName);
            referenced.retainAll(declared);
            return referenced;
        }

        private List<Map<String, Object>> resolveCallGraph(List<Map<String, Object>> rawEdges, List<Map<String, Object>> symbols) {
            Set<String> symbolIds = symbols.stream()
                .map(item -> (String) item.get("symbol_id"))
                .collect(Collectors.toCollection(LinkedHashSet::new));
            List<Map<String, Object>> resolved = new ArrayList<>();
            for (Map<String, Object> edge : rawEdges) {
                String target = (String) edge.get("target");
                if (symbolIds.contains(target)) {
                    resolved.add(edge);
                    continue;
                }
                String shortName = target.substring(target.lastIndexOf(':') + 1);
                List<String> matches = symbolIds.stream()
                    .filter(symbolId -> symbolId.endsWith(":" + shortName))
                    .toList();
                resolved.add(mapOf(
                    "source", edge.get("source"),
                    "target", matches.size() == 1 ? matches.get(0) : target,
                    "kind", edge.get("kind")
                ));
            }
            return resolved;
        }

        private List<Map<String, Object>> dedupeDependencyMaps(List<Map<String, Object>> dependencies) {
            Set<String> seen = new LinkedHashSet<>();
            List<Map<String, Object>> deduped = new ArrayList<>();
            for (Map<String, Object> dependency : dependencies) {
                String key = dependency.get("source_module") + "|" + dependency.get("target_module") + "|" + dependency.get("import_kind");
                if (seen.add(key)) {
                    deduped.add(dependency);
                }
            }
            return deduped;
        }

        private boolean isExcluded(Path path) {
            String normalized = projectRoot.relativize(path).toString().replace('\\', '/');
            return normalized.contains("/target/")
                || normalized.contains("/build/")
                || normalized.contains("/.git/")
                || normalized.contains("/.venv/");
        }
    }

    private static Map<String, Object> mapSymbol(String symbolId, String name, String moduleName, Path filePath, String kind, String signature, List<String> annotations, List<String> bases, Node node) {
        return mapOf(
            "symbol_id", symbolId,
            "name", name,
            "qualname", moduleName + "." + name,
            "kind", kind,
            "language", "java",
            "module", moduleName,
            "file_path", filePath.toString(),
            "line_start", lineStart(node),
            "line_end", lineEnd(node),
            "signature", signature,
            "decorators", annotations,
            "bases", bases,
            "dependencies", List.of(),
            "docstring", null
        );
    }

    private static List<Map<String, Object>> extractFields(TypeDeclaration<?> type) {
        List<Map<String, Object>> fields = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        for (FieldDeclaration fieldDeclaration : type.getFields()) {
            for (var variable : fieldDeclaration.getVariables()) {
                if (!seen.add(variable.getNameAsString())) {
                    continue;
                }
                fields.add(mapOf(
                    "name", variable.getNameAsString(),
                    "annotation", fieldDeclaration.getElementType().asString(),
                    "default", variable.getInitializer().map(Node::toString).orElse(null)
                ));
            }
        }
        return fields;
    }

    private static boolean looksLikeModel(TypeDeclaration<?> type, List<String> annotations) {
        if (type instanceof RecordDeclaration) {
            return true;
        }
        return annotations.stream().anyMatch(item -> Set.of("Entity", "Document", "Table", "Data", "Value", "Embeddable").contains(item))
            || !type.getFields().isEmpty();
    }

    private static List<String> collectAnnotations(Node node) {
        if (!(node instanceof NodeWithAnnotations<?> annotated)) {
            return List.of();
        }
        return annotated.getAnnotations().stream().map(JavaParserBridgeMain::annotationName).toList();
    }

    private static List<String> extractBases(TypeDeclaration<?> type) {
        List<String> bases = new ArrayList<>();
        if (type instanceof ClassOrInterfaceDeclaration classOrInterface) {
            classOrInterface.getExtendedTypes().forEach(item -> bases.add(item.asString()));
            classOrInterface.getImplementedTypes().forEach(item -> bases.add(item.asString()));
        } else if (type instanceof RecordDeclaration recordDeclaration) {
            recordDeclaration.getImplementedTypes().forEach(item -> bases.add(item.asString()));
        } else if (type instanceof EnumDeclaration enumDeclaration) {
            enumDeclaration.getImplementedTypes().forEach(item -> bases.add(item.asString()));
        }
        return bases;
    }

    private static boolean containsMainMethod(CompilationUnit unit) {
        return unit.findAll(MethodDeclaration.class).stream().anyMatch(method -> "main".equals(method.getNameAsString()) && method.isStatic() && method.getType().isVoidType());
    }
    private static boolean isEntrypointMethod(CallableDeclaration<?> callable, Collection<String> annotations) {
        if (callable instanceof MethodDeclaration method) {
            if ("main".equals(method.getNameAsString()) && method.isStatic() && method.getType().isVoidType()) return true;
        }
        return annotations.stream().anyMatch(ENTRYPOINT_METHOD_ANNOTATIONS::contains);
    }

    private static boolean containsReflection(Node node) {
        String text = node.toString();
        return text.contains("Class.forName")
            || text.contains("getDeclaredMethod")
            || text.contains("getMethod(")
            || text.contains("Method.invoke")
            || text.contains("Proxy.newProxyInstance");
    }

    private static boolean containsAsync(Node node, Collection<String> annotations) {
        String text = node.toString();
        return text.contains("CompletableFuture")
            || text.contains("ExecutorService")
            || text.contains("TaskExecutor")
            || annotations.stream().anyMatch(item -> Set.of("Async", "Scheduled", "KafkaListener", "RabbitListener", "JmsListener", "EventListener").contains(item));
    }

    private static void detectMiddleware(Path path, String symbolId, String source, Collection<String> annotations, List<Map<String, Object>> sink) {
        Map<String, List<String>> evidence = Map.of(
            "kafka", List.of("KafkaTemplate", "KafkaListener"),
            "rabbitmq", List.of("RabbitTemplate", "RabbitListener"),
            "jms", List.of("JmsTemplate", "JmsListener"),
            "redis", List.of("RedisTemplate", "StringRedisTemplate", "RedissonClient"),
            "http", List.of("RestTemplate", "WebClient", "FeignClient"),
            "database", List.of("JdbcTemplate", "JpaRepository", "MyBatis"),
            "mongodb", List.of("MongoTemplate")
        );
        for (Map.Entry<String, List<String>> entry : evidence.entrySet()) {
            for (String token : entry.getValue()) {
                if (source.contains(token) || annotations.contains(token)) {
                    sink.add(mapOf(
                        "path", path.toString(),
                        "symbol_id", symbolId,
                        "middleware", entry.getKey(),
                        "role", "integration",
                        "evidence", token
                    ));
                }
            }
        }
    }

    private static String dynamicCallKind(String methodName) {
        return Set.of("invoke", "getBean", "forName", "newProxyInstance").contains(methodName) ? "dynamic_call" : "call";
    }

    private static String moduleName(String packageName, Path path) {
        return packageName == null || packageName.isBlank() ? stripExtension(path.getFileName().toString()) : packageName + "." + stripExtension(path.getFileName().toString());
    }

    private static String stripExtension(String fileName) {
        int dot = fileName.lastIndexOf('.');
        return dot >= 0 ? fileName.substring(0, dot) : fileName;
    }

    private static String typeKind(TypeDeclaration<?> type) {
        if (type instanceof ClassOrInterfaceDeclaration classOrInterface) {
            return classOrInterface.isInterface() ? "interface" : "class";
        }
        if (type instanceof EnumDeclaration) {
            return "enum";
        }
        if (type instanceof RecordDeclaration) {
            return "record";
        }
        return "type";
    }

    private static String annotationName(AnnotationExpr annotation) { return annotation.getName().getIdentifier(); }

    private static String annotationKind(String annotation, boolean classLevel) {
        if (classLevel && IOC_ANNOTATIONS.contains(annotation)) {
            return "ioc_component";
        }
        if (ENTRYPOINT_METHOD_ANNOTATIONS.contains(annotation)) {
            return "entrypoint_annotation";
        }
        return "annotation";
    }

    private static boolean isTestFile(Path path) {
        String normalized = path.toString().replace('\\', '/');
        return normalized.contains("/src/test/") || normalized.endsWith("Test.java");
    }

    private static int lineStart(Node node) {
        return node.getRange().map(range -> range.begin.line).orElse(1);
    }

    private static int lineEnd(Node node) {
        return node.getRange().map(range -> range.end.line).orElse(lineStart(node));
    }

    private static Map<String, Object> mapOf(Object... keyValues) {
        Map<String, Object> value = new LinkedHashMap<>();
        for (int index = 0; index < keyValues.length; index += 2) {
            value.put(String.valueOf(keyValues[index]), keyValues[index + 1]);
        }
        return value;
    }

    private static final class ParsedJavaFile {
        private Map<String, Object> sourceFile;
        private final List<Map<String, Object>> moduleDependencies = new ArrayList<>();
        private final List<Map<String, Object>> entrypoints = new ArrayList<>();
        private final List<Map<String, Object>> symbols = new ArrayList<>();
        private final List<Map<String, Object>> models = new ArrayList<>();
        private final List<Map<String, Object>> callGraph = new ArrayList<>();
        private final Set<String> riskNodes = new LinkedHashSet<>();
        private final List<Map<String, Object>> middleware = new ArrayList<>();
        private final List<Map<String, Object>> reflectionPoints = new ArrayList<>();
        private final List<Map<String, Object>> dynamicCalls = new ArrayList<>();
        private final List<Map<String, Object>> asyncFlows = new ArrayList<>();
        private final List<Map<String, Object>> iocComponents = new ArrayList<>();
        private final List<Map<String, Object>> annotations = new ArrayList<>();
    }

    private record IndexedCompilationUnit(Path path, CompilationUnit unit, String packageName) {}
}
