from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from pydantic import SecretStr

from langchain.agents import create_agent
from langchain.tools import tool
from langchain.tools.tool_node import ToolRuntime
from langchain_openai import ChatOpenAI

from ..core.models import ProjectPaths, UnitContext
from ..core.settings import AppSettings
from ..analysis.sibling_scanner import analyze_java_directory
from .language_runtime import run_test_file as runtime_run_test_file
from .language_runtime import validate_source_file
from .python_import_normalizer import normalize_python_imports
from .reporter import extract_token_usage, get_reporter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMGeneration:
    rationale: str


@dataclass(slots=True)
class AgentContext:
    paths: ProjectPaths
    allowed_write_paths: list[str]


class LLMClient:
    MAX_AGENT_ATTEMPTS = 2
    SYSTEM_PROMPT = (
        "You are a code migration agent.\n"
        "You must complete work by calling tools.\n"
        "Do not return code in the final assistant message.\n"
        "Use tools to inspect files, copy resource files when needed, write files, validate source files, and run test files.\n"
        "Default execution granularity is file-level migration focused on preserving project completion.\n"
        "Preserve the target language of each file and keep generated code runnable in that language.\n"
        "Before finishing, ensure the required file has been written to the exact requested path.\n"
    )

    def __init__(self, settings: AppSettings, paths: ProjectPaths) -> None:
        self.settings = settings
        self.paths = paths
        self.model = self._build_model()
        self.agent = self._build_agent()

    def generate_code(self, context: UnitContext) -> LLMGeneration:
        rationale = self._run_agent(
            task=self._build_migration_task(context),
            required_paths=context.target_file_paths or [context.target_file_path],
        )
        return LLMGeneration(rationale=rationale)

    def generate_tests(self, context: UnitContext, test_path: str) -> str:
        return self._run_agent(
            task=self._build_test_task(context, test_path),
            required_paths=[test_path],
        )

    def repair_artifact(
        self, context: UnitContext, failure_log: str, test_path: str
    ) -> str:
        return self._run_agent(
            task=self._build_repair_task(context, failure_log, test_path),
            required_paths=[
                *(context.target_file_paths or [context.target_file_path]),
                test_path,
            ],
        )

    def _build_model(self) -> ChatOpenAI:
        if not self.settings.has_api_key:
            raise RuntimeError("Missing API key for LangChain agent model.")
        return ChatOpenAI(
            base_url=self.settings.base_url,
            api_key=SecretStr(self.settings.api_key or ""),  # type: ignore[arg-type]
            model=self.settings.model_name,
            temperature=0,
        )

    def _build_agent(self) -> Any:
        return create_agent(
            model=self.model,
            tools=_build_agent_tools(),
            system_prompt=self.SYSTEM_PROMPT,
            context_schema=AgentContext,
        )

    def _run_agent(self, task: str, required_paths: list[str]) -> str:
        logger.info("LLM Request\n%s", _truncate_block(task))
        get_reporter().model("request", task.splitlines()[0] if task else "request")
        latest_error: Exception | None = None
        current_task = task

        for attempt in range(1, self.MAX_AGENT_ATTEMPTS + 1):
            try:
                result = self.agent.invoke(
                    {"messages": [{"role": "user", "content": current_task}]},
                    context=AgentContext(
                        paths=self.paths,
                        allowed_write_paths=required_paths,
                    ),
                )
                missing = [path for path in required_paths if not Path(path).exists()]
                if missing:
                    raise RuntimeError(
                        f"Agent finished without writing required files: {missing}"
                    )
                for path in required_paths:
                    self._normalize_and_validate_required_path(Path(path))

                final_text = self._extract_final_text(result)
                token_usage = extract_token_usage(result)
                logger.info("LLM Response\n%s", _truncate_block(final_text))
                get_reporter().model("response", final_text, token_usage=token_usage)
                return final_text
            except Exception as exc:
                latest_error = exc
                if attempt >= self.MAX_AGENT_ATTEMPTS:
                    break
                logger.warning(
                    "Agent attempt %s/%s failed and will be retried: %s",
                    attempt,
                    self.MAX_AGENT_ATTEMPTS,
                    exc,
                )
                current_task = self._build_retry_task(task, exc, attempt + 1)

        if latest_error is not None:
            self._debug_dump_raw_model_output(current_task, latest_error)
            raise RuntimeError(
                f"LangChain agent invocation failed: {latest_error}"
            )
        raise RuntimeError("LangChain agent invocation failed with an unknown error.")

    def _normalize_and_validate_required_path(self, path: Path) -> None:
        resolved = path.resolve()
        target_root = Path(self.paths.target_root).resolve()
        if resolved == target_root or target_root in resolved.parents:
            normalize_python_imports(resolved, target_root)
        _validate_file_path(resolved)

    def _build_retry_task(
        self, original_task: str, error: Exception, attempt_number: int
    ) -> str:
        return (
            f"{original_task}\n\n"
            "Previous attempt failed. Self-correct and try again using tools.\n"
            f"Retry attempt: {attempt_number}/{self.MAX_AGENT_ATTEMPTS}\n"
            f"Observed error: {error}\n"
            "Correction requirements:\n"
            "- Treat the observed error as authoritative feedback from the runtime.\n"
            "- Re-check exact file paths with exists or list_dir before calling read_file.\n"
            "- Do not reuse the failing path unless the tools confirm it exists.\n"
            "- Never attempt to write dependency files, sibling files, or any path outside the current unit's explicitly required output paths.\n"
            "- If the required file is missing at the end, write it to the exact requested path before finishing.\n"
        )

    def _extract_final_text(self, result: dict[str, Any]) -> str:
        messages = result.get("messages", [])
        for message in reversed(messages):
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if (
                        isinstance(item, dict)
                        and item.get("type") == "text"
                        and item.get("text")
                    ):
                        text_parts.append(str(item["text"]))
                if text_parts:
                    return "\n".join(text_parts).strip()
        return "Agent completed the requested tool-driven file operation."

    def _debug_dump_raw_model_output(self, task: str, cause: Exception) -> None:
        try:
            response = self.model.invoke(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": task},
                ]
            )
        except Exception as debug_exc:
            print("=== CODETRANSLATE RAW DEBUG FAILED ===", file=sys.stderr)
            print(f"original_error: {cause}", file=sys.stderr)
            print(f"debug_error: {debug_exc}", file=sys.stderr)
            print("=== END CODETRANSLATE RAW DEBUG FAILED ===", file=sys.stderr)
            return

        print("=== CODETRANSLATE RAW MODEL RESPONSE BEGIN ===", file=sys.stderr)
        print(f"original_error: {cause}", file=sys.stderr)
        print("--- raw_content ---", file=sys.stderr)
        print(response.content, file=sys.stderr)
        print("--- additional_kwargs ---", file=sys.stderr)
        print(
            json.dumps(
                response.additional_kwargs, indent=2, ensure_ascii=False, default=str
            ),
            file=sys.stderr,
        )
        print("--- response_metadata ---", file=sys.stderr)
        print(
            json.dumps(
                response.response_metadata, indent=2, ensure_ascii=False, default=str
            ),
            file=sys.stderr,
        )
        print("=== CODETRANSLATE RAW MODEL RESPONSE END ===", file=sys.stderr)

    def _build_migration_task(self, context: UnitContext) -> str:
        source_language = context.target_constraints.get("source_language", "python")
        target_language = context.target_constraints.get("language", "python")
        source_fence = self._code_fence_language(source_language)
        language_guardrails = self._language_specific_requirements(
            source_language, target_language
        )
        import_contract = self._python_import_contract(context)
        return (
            "Migrate one source file or one tightly-coupled cycle batch into the target file(s) by using tools.\n"
            f"Unit: {context.unit_id}\n"
            f"Summary: {context.summary}\n"
            f"User-selected source language: {source_language}\n"
            f"User-selected target language: {target_language}\n"
            f"Target path: {context.target_file_path}\n"
            f"Target paths: {json.dumps(context.target_file_paths, ensure_ascii=False)}\n"
            f"Batch sources: {json.dumps([item['path'] for item in context.batch_sources], ensure_ascii=False)}\n"
            f"Decorators: {json.dumps(context.decorators, ensure_ascii=False)}\n"
            f"Module imports: {json.dumps(context.module_imports, ensure_ascii=False)}\n"
            f"Dependency target files: {json.dumps(context.dependency_targets, ensure_ascii=False)}\n"
            f"Dependency summaries: {json.dumps(context.dependency_summaries, ensure_ascii=False)}\n"
            f"Related tests: {json.dumps(context.related_tests, ensure_ascii=False)}\n"
            f"Related resources: {json.dumps(context.related_resources, ensure_ascii=False)}\n"
            f"Build context: {json.dumps(context.build_context, ensure_ascii=False)}\n"
            f"Java-to-Python migration hints: {json.dumps(context.java_migration_hints, ensure_ascii=False)}\n"
            f"Module-level context:\n{context.module_level_context}\n"
            "Requirements:\n"
            f"- You must write every final migrated file listed in {json.dumps(context.target_file_paths, ensure_ascii=False)} using write_file.\n"
            "- Preserve complete file-level behavior and cross-symbol consistency.\n"
            "- If this is a cycle batch, coordinate imports/contracts across all files in the batch before finishing.\n"
            f"- Default project paths are: source_root={self.paths.source_root}, workspace_root={self.paths.workspace_root}, target_root={self.paths.target_root}.\n"
            "- First call exists on each target path before attempting to read it.\n"
            "- Only call read_file on a target path if exists reports exists=true.\n"
            "- If a target file does not exist yet, do not call read_file on it; write the full migrated file directly.\n"
            "- If a target file exists, read it first and merge carefully at file scope.\n"
            "- When source_language is Java and target_language is Python, do not keep Java package/import syntax in the output.\n"
            "- Convert Java dependencies into valid Python imports based on the dependency target files and the actual target project layout.\n"
            "- Do not emit imports like `from net... import ...` unless that exact Python module file already exists and matches the target layout.\n"
            "- Do not put tests or markdown into the target source file.\n"
            "- Preserve semantics covered by related tests and resource fixtures when they exist.\n"
            "- Absolute source paths from other mounted projects may be read when needed.\n"
            "- If you need a related resource fixture in the translated project, first copy it into workspace_root or target_root with copy_path, then prefer the copied path.\n"
            f"{language_guardrails}"
            f"{import_contract}"
            f"- Before finishing, call validate_file on each target path in {json.dumps(context.target_file_paths, ensure_ascii=False)}.\n\n"
            f"Full source file:\n```{source_fence}\n{context.source_file_content}\n```\n\n"
            f"Source code:\n```{source_fence}\n{context.source_code}\n```"
        )

    def _build_test_task(self, context: UnitContext, test_path: str) -> str:
        source_language = context.target_constraints.get("source_language", "python")
        target_language = context.target_constraints.get("language", "python")
        source_fence = self._code_fence_language(source_language)
        return (
            "Generate a test file for one migrated source file by using tools.\n"
            f"Unit: {context.unit_id}\n"
            f"Summary: {context.summary}\n"
            f"User-selected source language: {source_language}\n"
            f"User-selected target language: {target_language}\n"
            f"Target file: {context.target_file_path}\n"
            f"Target files: {json.dumps(context.target_file_paths, ensure_ascii=False)}\n"
            f"Test path: {test_path}\n"
            f"Decorators: {json.dumps(context.decorators, ensure_ascii=False)}\n"
            f"Module imports: {json.dumps(context.module_imports, ensure_ascii=False)}\n"
            f"Dependency target files: {json.dumps(context.dependency_targets, ensure_ascii=False)}\n"
            f"Related tests: {json.dumps(context.related_tests, ensure_ascii=False)}\n"
            f"Related resources: {json.dumps(context.related_resources, ensure_ascii=False)}\n"
            f"Build context: {json.dumps(context.build_context, ensure_ascii=False)}\n"
            f"Module-level context:\n{context.module_level_context}\n"
            f"Test requirements: {json.dumps(context.test_requirements, ensure_ascii=False)}\n"
            "Requirements:\n"
            f"- You must write the test file to {test_path} using write_file.\n"
            f"- The test file must match the source language `{context.target_constraints.get('language')}`.\n"
            f"- Use this test style guidance: {self._test_style_for_language(context.target_constraints.get('language', 'python'))}.\n"
            "- Prefer validating exported file behavior and cross-symbol contracts over isolated fragment behavior.\n"
            f"- Default project paths are: source_root={self.paths.source_root}, workspace_root={self.paths.workspace_root}, target_root={self.paths.target_root}.\n"
            "- Absolute source paths from other mounted projects may be read when needed.\n"
            "- Use the exact target file path given above; do not infer or rewrite it from package names or sibling modules.\n"
            "- Before reading any dependency or migrated target file, verify the exact path with exists or discover it with list_dir.\n"
            "- Do not create synthetic package stubs that hide invalid Java-style Python imports from the migrated file.\n"
            "- If a dependency import is required, derive it from the actual dependency target files and project layout, not from the original Java package string.\n"
            "- If a related resource is needed for the test, use an already staged path when available; otherwise use copy_path to mirror it into target_root or workspace_root before reading it.\n"
            f"- Before finishing, call validate_file on {test_path}.\n\n"
            f"Full source file:\n```{source_fence}\n{context.source_file_content}\n```\n\n"
            f"Source code:\n```{source_fence}\n{context.source_code}\n```"
        )

    def _build_repair_task(
        self, context: UnitContext, failure_log: str, test_path: str
    ) -> str:
        source_language = context.target_constraints.get("source_language", "python")
        target_language = context.target_constraints.get("language", "python")
        source_fence = self._code_fence_language(source_language)
        language_guardrails = self._language_specific_requirements(
            source_language, target_language
        )
        import_contract = self._python_import_contract(context)
        return (
            "Repair the smallest failing file by using tools while preserving file-level source contracts.\n"
            f"Unit: {context.unit_id}\n"
            f"User-selected source language: {source_language}\n"
            f"User-selected target language: {target_language}\n"
            f"Target file: {context.target_file_path}\n"
            f"Target files: {json.dumps(context.target_file_paths, ensure_ascii=False)}\n"
            f"Test file: {test_path}\n"
            f"Decorators: {json.dumps(context.decorators, ensure_ascii=False)}\n"
            f"Module imports: {json.dumps(context.module_imports, ensure_ascii=False)}\n"
            f"Dependency target files: {json.dumps(context.dependency_targets, ensure_ascii=False)}\n"
            f"Related tests: {json.dumps(context.related_tests, ensure_ascii=False)}\n"
            f"Related resources: {json.dumps(context.related_resources, ensure_ascii=False)}\n"
            f"Build context: {json.dumps(context.build_context, ensure_ascii=False)}\n"
            f"Module-level context:\n{context.module_level_context}\n"
            "Requirements:\n"
            "- Read the relevant existing file before editing.\n"
            "- Fix only the current target file or the current test file.\n"
            "- Do not modify dependency files even if the traceback mentions them; adapt the owned file(s) around the dependency behavior instead.\n"
            "- Write the corrected file with write_file.\n"
            f"- Default project paths are: source_root={self.paths.source_root}, workspace_root={self.paths.workspace_root}, target_root={self.paths.target_root}.\n"
            "- Absolute source paths from other mounted projects may be read when needed.\n"
            "- If a resource fixture is needed, use an already staged path when available; otherwise use copy_path to place it in workspace_root or target_root before reading it.\n"
            f"- Keep tests aligned with this guidance: {self._test_style_for_language(context.target_constraints.get('language', 'python'))}.\n"
            f"{language_guardrails}"
            f"{import_contract}"
            "- Validate any source or test file you changed before finishing using validate_file.\n\n"
            f"Failure log:\n```text\n{failure_log[:3000]}\n```\n\n"
            f"Full source file:\n```{source_fence}\n{context.source_file_content}\n```\n\n"
            f"Original source code:\n```{source_fence}\n{context.source_code}\n```"
        )

    def _test_style_for_language(self, language: str) -> str:
        if language == "nodejs":
            return "prefer a standalone Node.js script using node:assert and direct imports; avoid framework-specific runners unless the project already requires one"
        return "prefer a standalone unittest script and avoid pytest unless the project contract explicitly requires it"

    def _code_fence_language(self, language: str) -> str:
        if language == "java":
            return "java"
        if language == "go":
            return "go"
        if language == "nodejs":
            return "typescript"
        return "python"

    def _language_specific_requirements(
        self, source_language: str, target_language: str
    ) -> str:
        if source_language != "java" or target_language != "python":
            return ""
        return (
            "- When migrating Java enums with constructor arguments into Python, preserve unique enum member values; never use shared booleans or integers as the enum value for multiple members.\n"
            "- If an enum carries metadata such as `isConvertible`, store that metadata separately from the member identity, for example with tuple values or `__new__`.\n"
            "- Preserve overloaded Java constructors with explicit Python dispatch that distinguishes argument count and argument types.\n"
            "- Keep Java map/property semantics exact: do not replace unconditional `put` behavior with conditional setters, and do not skip writes that the Java source performs.\n"
            "- If a dependency target path is known, import it using the concrete translated module path instead of fallback import ladders.\n"
            "- Use a single Python import convention: absolute imports rooted at the translated top-level package under target_root, for example `validator_api.net...` or `validator_core.net...`.\n"
            "- Do not emit bare sibling imports like `from AbstractOptions import ...`; convert them to the concrete package-root import path.\n"
            "- Do not generate `importlib.util.spec_from_file_location`, `module_from_spec`, `exec_module`, or `sys.path` mutation as a dependency-bridging strategy.\n"
        )

    def _python_import_contract(self, context: UnitContext) -> str:
        source_language = context.target_constraints.get("source_language", "python")
        target_language = context.target_constraints.get("language", "python")
        if source_language != "java" or target_language != "python":
            return ""
        current_module = _python_module_name(
            Path(context.target_file_path), Path(self.paths.target_root)
        )
        dependency_modules: list[dict[str, str]] = []
        for item in context.dependency_targets:
            target_path = item.get("target_path")
            if not target_path:
                continue
            dependency_modules.append(
                {
                    "name": item.get("name", ""),
                    "target_path": target_path,
                    "module": _python_module_name(
                        Path(target_path), Path(self.paths.target_root)
                    ),
                }
            )
        return (
            f"- Current Python module path must be `{current_module}`.\n"
            "- For Java→Python migrations, imports must resolve through package-root module paths derived from target_root.\n"
            f"- Dependency import map: {json.dumps(dependency_modules, ensure_ascii=False)}.\n"
            "- Never bridge dependencies by loading sibling files with `spec_from_file_location`; import their package module directly.\n"
        )


def _python_module_name(path: Path, target_root: Path) -> str:
    relative_path = path.resolve().relative_to(target_root.resolve())
    return ".".join(relative_path.with_suffix("").parts)


def _build_agent_tools() -> list[Any]:
    @tool
    def list_dir(path: str, runtime: ToolRuntime[AgentContext]) -> str:
        """List entries under a directory path."""
        resolved = _resolve_access_path(path, runtime.context)
        if not resolved.exists():
            payload = json.dumps(
                {"path": str(resolved), "exists": False, "entries": []},
                ensure_ascii=False,
            )
            logger.info("Tool Call `list_dir`\npath=%s\nresult=%s", resolved, payload)
            get_reporter().tool("list_dir", str(resolved), "ok")
            return payload
        if not resolved.is_dir():
            raise ValueError(f"path is not a directory: {resolved}")
        entries = [
            {
                "name": child.name,
                "path": str(child),
                "type": "dir" if child.is_dir() else "file",
            }
            for child in sorted(resolved.iterdir(), key=lambda item: item.name)
        ]
        payload = json.dumps(
            {"path": str(resolved), "exists": True, "entries": entries},
            ensure_ascii=False,
        )
        logger.info(
            "Tool Call `list_dir`\npath=%s\nresult=%s",
            resolved,
            _truncate_block(payload),
        )
        get_reporter().tool("list_dir", str(resolved), "ok")
        return payload

    @tool
    def read_file(path: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Read a UTF-8 text file."""
        resolved = _resolve_access_path(path, runtime.context)
        if not resolved.exists():
            raise FileNotFoundError(f"file does not exist: {resolved}")
        if not resolved.is_file():
            raise ValueError(f"path is not a file: {resolved}")
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        logger.info(
            "Tool Call `read_file`\npath=%s\ncontent=%s",
            resolved,
            _truncate_block(content),
        )
        get_reporter().tool("read_file", str(resolved), "ok")
        return content

    @tool
    def exists(path: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Check whether a path exists."""
        resolved = _resolve_access_path(path, runtime.context)
        exists_flag = resolved.exists()
        payload = json.dumps(
            {"path": str(resolved), "exists": exists_flag}, ensure_ascii=False
        )
        logger.info("Tool Call `exists`\npath=%s\nresult=%s", resolved, payload)
        get_reporter().tool(
            "exists", str(resolved), f"exists={str(exists_flag).lower()}"
        )
        return payload

    @tool
    def mkdir(path: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Create a directory path inside the project, workspace, or target roots."""
        resolved = _resolve_output_dir_path(path, runtime.context)
        resolved.mkdir(parents=True, exist_ok=True)
        logger.info("Tool Call `mkdir`\npath=%s", resolved)
        get_reporter().tool("mkdir", str(resolved), "ok")
        return str(resolved)

    @tool
    def write_file(path: str, content: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Write a UTF-8 text file."""
        resolved = _resolve_output_path(path, runtime.context)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        normalize_python_imports(resolved, Path(runtime.context.paths.target_root))
        logger.info(
            "Tool Call `write_file`\npath=%s\ncontent=%s",
            resolved,
            _truncate_block(content),
        )
        get_reporter().tool("write_file", str(resolved), "ok")
        return str(resolved)

    @tool
    def copy_path(
        source_path: str,
        destination_path: str,
        runtime: ToolRuntime[AgentContext],
    ) -> str:
        """Copy a file or directory from an allowed source root into workspace or target roots."""
        source = _resolve_access_path(source_path, runtime.context)
        destination = _resolve_output_root_path(destination_path, runtime.context)
        if not source.exists():
            raise FileNotFoundError(f"source path does not exist: {source}")
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        payload = json.dumps(
            {
                "source": str(source),
                "destination": str(destination),
                "type": "dir" if source.is_dir() else "file",
            },
            ensure_ascii=False,
        )
        logger.info("Tool Call `copy_path`\nresult=%s", payload)
        get_reporter().tool("copy_path", f"{source} -> {destination}", "ok")
        return payload

    @tool
    def validate_file(path: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Validate a source or test file based on its extension."""
        resolved = _resolve_access_path(path, runtime.context)
        payload = _validate_file_path(resolved)
        logger.info("Tool Call `validate_file`\npath=%s\nresult=%s", resolved, payload)
        get_reporter().tool("validate_file", str(resolved), "ok")
        return payload

    @tool
    def run_test_file(path: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Run a generated test file based on its extension and return stdout/stderr."""
        resolved = _resolve_access_path(path, runtime.context)
        payload = json.dumps(_run_test_path(resolved), ensure_ascii=False)
        logger.info(
            "Tool Call `run_test_file`\npath=%s\nresult=%s",
            resolved,
            _truncate_block(payload),
        )
        get_reporter().tool("run_test_file", str(resolved), "ok")
        return payload

    @tool
    def analyze_java_module(directory: str) -> str:
        """Run full Java dependency analysis on an arbitrary directory.

        Discovers all .java files, extracts imports, symbols (classes,
        methods, fields), and builds the complete module dependency graph.
        Use this when you encounter an import that points to a class not yet
        migrated – the returned analysis shows what symbols the module
        defines and what it depends on, so you can write correct Python
        code instead of guessing.
        """
        result = analyze_java_directory(directory)
        payload = json.dumps(result, ensure_ascii=False, default=str)
        logger.info(
            "Tool Call `analyze_java_module`\ndirectory=%s\nresult=%s",
            directory,
            _truncate_block(payload),
        )
        get_reporter().tool("analyze_java_module", directory, "ok")
        return payload

    return [
        list_dir,
        read_file,
        exists,
        mkdir,
        write_file,
        copy_path,
        validate_file,
        run_test_file,
        analyze_java_module,
    ]


def _resolve_access_path(raw_path: str, agent_context: AgentContext) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(agent_context.paths.source_root) / path
    return path.resolve()


def _resolve_output_path(raw_path: str, agent_context: AgentContext) -> Path:
    resolved = _resolve_output_root_path(raw_path, agent_context)
    allowed_write_paths = {
        Path(path).resolve() for path in agent_context.allowed_write_paths
    }
    if allowed_write_paths and resolved not in allowed_write_paths:
        raise ValueError(f"destination path not owned by current unit: {resolved}")
    return resolved


def _resolve_output_dir_path(raw_path: str, agent_context: AgentContext) -> Path:
    resolved = _resolve_output_root_path(raw_path, agent_context)
    allowed_write_paths = {
        Path(path).resolve() for path in agent_context.allowed_write_paths
    }
    if not allowed_write_paths:
        return resolved
    if any(resolved == path.parent or resolved in path.parents for path in allowed_write_paths):
        return resolved
    raise ValueError(f"destination path not owned by current unit: {resolved}")


def _resolve_output_root_path(raw_path: str, agent_context: AgentContext) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(agent_context.paths.target_root) / path
    resolved = path.resolve()
    allowed_roots = (
        Path(agent_context.paths.workspace_root).resolve(),
        Path(agent_context.paths.target_root).resolve(),
    )
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError(f"destination path outside writable roots: {resolved}")
    return resolved


def _truncate_block(text: str, limit: int = 3000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"


def _validate_file_path(path: Path) -> str:
    suffix = path.suffix.lower()
    language = "nodejs" if suffix in {".js", ".mjs", ".cjs", ".ts"} else "python"
    validate_source_file(path, language)
    return f"validated:{language}:{path}"


def _run_test_path(path: Path) -> dict[str, str | int]:
    suffix = path.suffix.lower()
    language = "nodejs" if suffix in {".js", ".mjs", ".cjs", ".ts"} else "python"
    process = runtime_run_test_file(path, language)
    return {
        "path": str(path),
        "returncode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
    }
