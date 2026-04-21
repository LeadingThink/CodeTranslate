from __future__ import annotations

import re
from pathlib import Path

from ...runtime.reporter import get_reporter
from .base import LanguageAnalysis, ScanObservation
from .java_bridge import JavaParserBridge
from .java_mapping import map_bridge_payload


FRAMEWORK_TOKENS = {
    "spring": ("SpringBootApplication", "RestController", "Controller", "org.springframework"),
    "jpa": ("javax.persistence", "jakarta.persistence", "@Entity"),
    "mybatis": ("@Mapper", "org.apache.ibatis"),
    "lombok": ("lombok", "@Data", "@Builder", "@Value"),
    "reactor": ("reactor.core", "Mono<", "Flux<"),
}


class JavaAdapter:
    language = "java"

    def __init__(self, bridge: JavaParserBridge | None = None) -> None:
        self.bridge = bridge or JavaParserBridge()

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
        frameworks = {
            name
            for name, tokens in FRAMEWORK_TOKENS.items()
            if any(token in source for token in tokens)
        }
        observation.frameworks.update(frameworks)
        if frameworks or re.search(r"@(SpringBootApplication|RestController|Controller)\b", source):
            observation.candidate_entrypoints.add(relative)
        return observation

    def analyze_project(
        self, project_root: Path, scan
    ) -> LanguageAnalysis:
        get_reporter().tool("javaparser", str(project_root), "run")
        payload = self.bridge.analyze_project(project_root)
        return map_bridge_payload(payload, project_root, scan)
