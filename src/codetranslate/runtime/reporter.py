from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Reporter(Protocol):
    def stage(self, title: str, detail: str = "") -> None: ...

    def tool(self, name: str, target: str, status: str = "ok") -> None: ...

    def model(self, label: str, detail: str = "") -> None: ...

    def progress(self, completed: int, total: int, current: str = "", remaining_chain: str = "") -> None: ...

    def result(self, title: str, status: str, detail: str = "") -> None: ...


@dataclass(slots=True)
class NoOpReporter:
    def stage(self, title: str, detail: str = "") -> None:
        return

    def tool(self, name: str, target: str, status: str = "ok") -> None:
        return

    def model(self, label: str, detail: str = "") -> None:
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
