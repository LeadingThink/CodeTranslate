from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from ..core.models import MavenModuleRecord

MAVEN_NS = {"m": "http://maven.apache.org/POM/4.0.0"}


@dataclass(slots=True)
class JavaBaselineResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


class MavenProjectAnalyzer:
    def analyze(self, project_root: Path) -> list[MavenModuleRecord]:
        root_pom = project_root / "pom.xml"
        if not root_pom.exists():
            return []
        visited: dict[str, MavenModuleRecord] = {}
        self._collect_module(project_root, project_root, None, visited)
        return sorted(
            visited.values(), key=lambda item: (item.relative_path, item.name)
        )

    def _collect_module(
        self,
        project_root: Path,
        module_root: Path,
        parent: str | None,
        visited: dict[str, MavenModuleRecord],
    ) -> None:
        pom_path = module_root / "pom.xml"
        if not pom_path.exists():
            return
        module = self._parse_module(project_root, pom_path, parent)
        key = module.relative_path or "."
        if key in visited:
            return
        visited[key] = module
        for child in self._child_modules(pom_path):
            child_root = (module_root / child).resolve()
            self._collect_module(project_root, child_root, module.name, visited)

    def _parse_module(
        self, project_root: Path, pom_path: Path, parent: str | None
    ) -> MavenModuleRecord:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        artifact_id = self._text(root, "m:artifactId") or pom_path.parent.name
        packaging = self._text(root, "m:packaging") or "jar"
        relative_path = pom_path.parent.relative_to(project_root).as_posix()
        source_roots = self._existing_dirs(pom_path.parent, ["src/main/java"])
        test_roots = self._existing_dirs(pom_path.parent, ["src/test/java"])
        resource_roots = self._existing_dirs(
            pom_path.parent,
            ["src/main/resources", "src/test/resources", "properties"],
        )
        dependencies = self._internal_dependencies(root)
        return MavenModuleRecord(
            name=artifact_id,
            relative_path=relative_path,
            pom_path=pom_path.relative_to(project_root).as_posix(),
            packaging=packaging,
            parent=parent,
            dependencies=dependencies,
            source_roots=source_roots,
            test_roots=test_roots,
            resource_roots=resource_roots,
        )

    def _child_modules(self, pom_path: Path) -> list[str]:
        root = ET.parse(pom_path).getroot()
        modules = root.find("m:modules", MAVEN_NS)
        if modules is None:
            return []
        return [
            element.text.strip()
            for element in modules.findall("m:module", MAVEN_NS)
            if element.text and element.text.strip()
        ]

    def _internal_dependencies(self, root: ET.Element) -> list[str]:
        dependencies: list[str] = []
        for dependency in root.findall("m:dependencies/m:dependency", MAVEN_NS):
            artifact_id = self._text(dependency, "m:artifactId")
            group_id = self._text(dependency, "m:groupId")
            if not artifact_id:
                continue
            if group_id and (
                group_id.startswith("${project.groupId}")
                or group_id.startswith("net.pinnacle21")
                or group_id.startswith("${")
            ):
                dependencies.append(artifact_id)
        return sorted(set(dependencies))

    def _text(self, root: ET.Element, path: str) -> str | None:
        node = root.find(path, MAVEN_NS)
        if node is None or node.text is None:
            return None
        value = node.text.strip()
        return value or None

    def _existing_dirs(self, base: Path, candidates: list[str]) -> list[str]:
        found: list[str] = []
        for candidate in candidates:
            path = base / candidate
            if path.exists() and path.is_dir():
                found.append(candidate)
        return found


class JavaBaselineRunner:
    def run(
        self, project_root: Path, modules: list[MavenModuleRecord]
    ) -> dict[str, object]:
        if not (project_root / "pom.xml").exists():
            return {"build_system": "unknown", "modules": [], "commands": []}

        commands: list[dict[str, object]] = []
        aggregate_compile = self._run_command(
            project_root, ["mvn", "-q", "-DskipTests", "compile"]
        )
        commands.append(
            {"scope": "project", **self._serialize_result(aggregate_compile)}
        )

        module_results: list[dict[str, object]] = []
        for module in modules:
            if not module.relative_path or module.packaging == "pom":
                continue
            test_result = self._run_command(
                project_root, ["mvn", "-q", "-pl", module.relative_path, "test"]
            )
            serialized = {
                "module": module.name,
                "relative_path": module.relative_path,
                **self._serialize_result(test_result),
            }
            module_results.append(serialized)
            commands.append(
                {
                    "scope": f"module:{module.name}",
                    **self._serialize_result(test_result),
                }
            )

        return {
            "build_system": "maven",
            "modules": module_results,
            "commands": commands,
            "project_compile_passed": aggregate_compile.returncode == 0,
        }

    def _run_command(self, workdir: Path, command: list[str]) -> JavaBaselineResult:
        try:
            process = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                check=False,
                timeout=240,
            )
        except FileNotFoundError as exc:
            return JavaBaselineResult(
                command=command,
                returncode=127,
                stdout="",
                stderr=str(exc),
            )
        return JavaBaselineResult(
            command=command,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )

    def _serialize_result(self, result: JavaBaselineResult) -> dict[str, object]:
        return {
            "command": result.command,
            "returncode": result.returncode,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-8000:],
            "passed": result.returncode == 0,
        }
