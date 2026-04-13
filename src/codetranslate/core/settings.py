from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppSettings:
    api_key: str | None = None
    base_url: str | None = None
    model_name: str = "gpt-4o"
    env_file: str | None = None

    @classmethod
    def from_env(cls) -> "AppSettings":
        env_file = cls._load_local_env()
        api_key = os.getenv("CODETRANSLATE_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("CODETRANSLATE_BASE_URL")
        model_name = os.getenv("CODETRANSLATE_MODEL", "gpt-4o")
        return cls(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            env_file=str(env_file) if env_file else None,
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _load_local_env() -> Path | None:
        env_path = Path.cwd() / ".env"
        if not env_path.exists():
            return None

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
        return env_path
