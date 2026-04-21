from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.tools import tool
from langchain.tools.tool_node import ToolRuntime
from langchain_openai import ChatOpenAI

from ..core.models import AnalysisResult, MigrationRequest
from ..core.settings import AppSettings
from ..runtime.reporter import extract_token_usage, get_reporter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AnalysisAgentContext:
    project_root: str


class ProjectIntelligenceAnalyzer:
    SYSTEM_PROMPT = (
        "You are a project understanding agent.\n"
        "Use tools to inspect the repository and infer real entrypoints, startup chains, deployment scripts, and migration priorities.\n"
        "Prefer evidence from package manifests, shell scripts, docker files, CI config, and bootstrap modules.\n"
        "Return a JSON object only with keys: summary, inferred_entrypoints, startup_files, high_risk_files, migration_notes.\n"
        "Each of inferred_entrypoints, startup_files, high_risk_files, migration_notes must be arrays of strings.\n"
    )

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.model = self._build_model()
        self.agent = self._build_agent() if self.model is not None else None

    def enrich(
        self, analysis: AnalysisResult, request: MigrationRequest
    ) -> dict[str, Any]:
        if self.agent is None:
            return {}
        prompt = self._build_prompt(analysis, request)
        logger.info("Analysis LLM Request\n%s", _truncate_block(prompt))
        get_reporter().stage(
            "Analyze Project", "Inferring entrypoints and startup chain"
        )
        try:
            result = self.agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                context=AnalysisAgentContext(project_root=analysis.project_root),
            )
        except Exception as exc:
            logger.warning("Analysis agent failed: %s", exc)
            get_reporter().result(
                "Analyze Project",
                "warning",
                f"LLM project understanding unavailable: {exc}",
            )
            return {}
        content = self._extract_final_text(result)
        token_usage = extract_token_usage(result)
        logger.info("Analysis LLM Response\n%s", _truncate_block(content))
        get_reporter().model(
            "analysis-response", content, token_usage=token_usage
        )
        return self._parse_insights(content)

    def _build_model(self) -> ChatOpenAI | None:
        if not self.settings.has_api_key:
            return None
        try:
            return ChatOpenAI(
                base_url=self.settings.base_url,
                api_key=self.settings.api_key,
                model=self.settings.model_name,
                temperature=0,
            )
        except Exception:
            return None

    def _build_agent(self) -> Any:
        return create_agent(
            model=self.model,
            tools=_build_analysis_tools(),
            system_prompt=self.SYSTEM_PROMPT,
            context_schema=AnalysisAgentContext,
        )

    def _build_prompt(self, analysis: AnalysisResult, request: MigrationRequest) -> str:
        source_files = [record.path for record in analysis.source_files[:80]]
        config_files = analysis.scan.config_files[:40]
        candidates = analysis.scan.candidate_entrypoints[:40]
        entrypoints = analysis.scan.entrypoints[:40]
        languages = analysis.scan.languages
        frameworks = analysis.scan.frameworks
        build_tools = analysis.scan.build_tools
        maven_modules = [
            {
                "name": module.name,
                "relative_path": module.relative_path,
                "packaging": module.packaging,
                "dependencies": module.dependencies,
                "source_roots": module.source_roots,
                "test_roots": module.test_roots,
                "resource_roots": module.resource_roots,
            }
            for module in analysis.scan.maven_modules[:20]
        ]
        return (
            f"Project root: {analysis.project_root}\n"
            f"User-selected source language: {request.source_language}\n"
            f"User-selected target language: {request.target_language}\n"
            f"User entry hints: {request.entry_hints}\n"
            f"User include paths: {request.include_paths}\n"
            f"User exclude paths: {request.exclude_paths}\n"
            f"Languages: {languages}\n"
            f"Frameworks: {frameworks}\n"
            f"Build tools: {build_tools}\n"
            f"Maven modules: {json.dumps(maven_modules, ensure_ascii=False)}\n"
            f"Static entrypoints: {entrypoints}\n"
            f"Candidate entrypoints: {candidates}\n"
            f"Config files: {config_files}\n"
            f"Test files sample: {analysis.scan.test_files[:40]}\n"
            f"Resource files sample: {analysis.scan.resource_files[:40]}\n"
            f"Source files sample: {source_files}\n"
            "Use tools to inspect the real project structure and infer startup chain and migration priorities for the user-selected source language only. "
            "Check scripts, manifests, shell files, and runtime bootstrap files when relevant."
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
        return ""

    def _parse_insights(self, content: str) -> dict[str, Any]:
        payload = _parse_json_object(content)
        if payload is None:
            return {
                "summary": content,
                "inferred_entrypoints": [],
                "startup_files": [],
                "high_risk_files": [],
                "migration_notes": [],
            }
        return {
            "summary": str(payload.get("summary", "")),
            "inferred_entrypoints": _normalize_string_list(
                payload.get("inferred_entrypoints")
            ),
            "startup_files": _normalize_string_list(payload.get("startup_files")),
            "high_risk_files": _normalize_string_list(payload.get("high_risk_files")),
            "migration_notes": _normalize_string_list(payload.get("migration_notes")),
        }


def _build_analysis_tools() -> list[Any]:
    @tool
    def list_dir(path: str, runtime: ToolRuntime[AnalysisAgentContext]) -> str:
        """List entries under a directory path."""
        resolved = _resolve_analysis_path(path, runtime.context)
        if not resolved.exists():
            return json.dumps(
                {"path": str(resolved), "exists": False, "entries": []},
                ensure_ascii=False,
            )
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
            "Analysis Tool `list_dir`\npath=%s\nresult=%s",
            resolved,
            _truncate_block(payload),
        )
        get_reporter().tool("list_dir", str(resolved), "ok")
        return payload

    @tool
    def read_file(path: str, runtime: ToolRuntime[AnalysisAgentContext]) -> str:
        """Read a UTF-8 text file."""
        resolved = _resolve_analysis_path(path, runtime.context)
        content = resolved.read_text(encoding="utf-8", errors="ignore")
        logger.info(
            "Analysis Tool `read_file`\npath=%s\ncontent=%s",
            resolved,
            _truncate_block(content),
        )
        get_reporter().tool("read_file", str(resolved), "ok")
        return content

    @tool
    def exists(path: str, runtime: ToolRuntime[AnalysisAgentContext]) -> str:
        """Check whether a path exists."""
        resolved = _resolve_analysis_path(path, runtime.context)
        payload = json.dumps(
            {"path": str(resolved), "exists": resolved.exists()}, ensure_ascii=False
        )
        logger.info("Analysis Tool `exists`\npath=%s\nresult=%s", resolved, payload)
        get_reporter().tool("exists", str(resolved), "ok")
        return payload

    @tool
    def search_text(pattern: str, runtime: ToolRuntime[AnalysisAgentContext]) -> str:
        """Search for a plain text pattern in project files and return matching paths with line snippets."""
        root = Path(runtime.context.project_root).resolve()
        matches: list[dict[str, str | int]] = []
        for path in root.rglob("*"):
            if len(matches) >= 25:
                break
            if not path.is_file():
                continue
            if any(
                part.startswith(".git") or part == "__pycache__" or part == ".venv"
                for part in path.parts
            ):
                continue
            try:
                for index, line in enumerate(
                    path.read_text(encoding="utf-8", errors="ignore").splitlines(),
                    start=1,
                ):
                    if pattern in line:
                        matches.append(
                            {"path": str(path), "line": index, "text": line.strip()}
                        )
                        if len(matches) >= 25:
                            break
            except Exception:
                continue
        payload = json.dumps(
            {"pattern": pattern, "matches": matches}, ensure_ascii=False
        )
        logger.info(
            "Analysis Tool `search_text`\npattern=%s\nresult=%s",
            pattern,
            _truncate_block(payload),
        )
        get_reporter().tool("search_text", pattern, "ok")
        return payload

    return [list_dir, read_file, exists, search_text]


def _resolve_analysis_path(raw_path: str, context: AnalysisAgentContext) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(context.project_root) / path
    resolved = path.resolve()
    root = Path(context.project_root).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path outside project root: {resolved}")
    return resolved


def _parse_json_object(content: str) -> dict[str, Any] | None:
    candidates = [content.strip()]
    stripped = content.strip()
    if "```json" in stripped:
        candidates.append(stripped.split("```json", 1)[1].split("```", 1)[0].strip())
    elif "```" in stripped:
        candidates.append(stripped.split("```", 1)[1].split("```", 1)[0].strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _truncate_block(text: str, limit: int = 3000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"
