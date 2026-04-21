from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class Reporter(Protocol):
    def stage(self, title: str, detail: str = "") -> None: ...

    def tool(self, name: str, target: str, status: str = "ok") -> None: ...

    def model(
        self,
        label: str,
        detail: str = "",
        token_usage: dict[str, int] | None = None,
    ) -> None: ...

    def progress(self, completed: int, total: int, current: str = "", remaining_chain: str = "") -> None: ...

    def result(self, title: str, status: str, detail: str = "") -> None: ...


@dataclass(slots=True)
class NoOpReporter:
    def stage(self, title: str, detail: str = "") -> None:
        return

    def tool(self, name: str, target: str, status: str = "ok") -> None:
        return

    def model(
        self,
        label: str,
        detail: str = "",
        token_usage: dict[str, int] | None = None,
    ) -> None:
        return

    def progress(self, completed: int, total: int, current: str = "", remaining_chain: str = "") -> None:
        return

    def result(self, title: str, status: str, detail: str = "") -> None:
        return


_ACTIVE_REPORTER: Reporter = NoOpReporter()


def set_reporter(reporter: Reporter | None) -> None:
    global _ACTIVE_REPORTER
    _ACTIVE_REPORTER = reporter or NoOpReporter()


def get_reporter() -> Reporter:
    return _ACTIVE_REPORTER


def extract_token_usage(result: dict[str, Any]) -> dict[str, int] | None:
    messages = result.get("messages", [])
    for message in reversed(messages):
        usage = _message_token_usage(message)
        if usage is not None:
            return usage
    return None


def _message_token_usage(message: Any) -> dict[str, int] | None:
    usage_metadata = getattr(message, "usage_metadata", None)
    usage = _normalize_token_usage(usage_metadata)
    if usage is not None:
        return usage

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        usage = _normalize_token_usage(response_metadata.get("token_usage"))
        if usage is not None:
            return usage

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        usage = _normalize_token_usage(additional_kwargs.get("token_usage"))
        if usage is not None:
            return usage
    return None


def _normalize_token_usage(raw_usage: Any) -> dict[str, int] | None:
    if not isinstance(raw_usage, dict):
        return None

    input_tokens = _as_int(
        raw_usage.get("input_tokens", raw_usage.get("prompt_tokens"))
    )
    output_tokens = _as_int(
        raw_usage.get("output_tokens", raw_usage.get("completion_tokens"))
    )
    total_tokens = _as_int(raw_usage.get("total_tokens"))

    if total_tokens == 0 and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    if total_tokens == 0:
        return None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
