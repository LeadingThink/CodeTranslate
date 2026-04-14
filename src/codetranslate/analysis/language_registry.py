from __future__ import annotations

from pathlib import Path

from .adapters.base import LanguageAdapter
from .adapters.generic import GenericAdapter
from .adapters.go_adapter import GoAdapter
from .adapters.java_adapter import JavaAdapter
from .adapters.nodejs_adapter import NodeJsAdapter
from .adapters.python_adapter import PythonAdapter
from .language_specs import detect_language_by_suffix


class LanguageRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, LanguageAdapter] = {
            "python": PythonAdapter(),
            "java": JavaAdapter(),
            "go": GoAdapter(),
            "rust": GenericAdapter("rust"),
            "nodejs": NodeJsAdapter(),
        }

    def adapters(self) -> list[LanguageAdapter]:
        return list(self._adapters.values())

    def adapter_for_language(self, language: str) -> LanguageAdapter | None:
        return self._adapters.get(language)

    def adapter_for_path(self, path: Path) -> LanguageAdapter | None:
        language = detect_language_by_suffix(path.name)
        if language is None:
            return None
        return self.adapter_for_language(language)
