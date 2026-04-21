from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from pathlib import PurePath


WINDOWS_DRIVE_RE = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")
WSL_MOUNT_RE = re.compile(r"^/mnt/(?P<drive>[A-Za-z])/(?P<rest>.*)$")
INVALID_PATH_CHARS_RE = re.compile(r"[^0-9A-Za-z_]+")
MULTIPLE_UNDERSCORES_RE = re.compile(r"_+")


def normalize_user_path(raw_path: str) -> Path:
    value = raw_path.strip()
    wsl_match = WSL_MOUNT_RE.match(value.replace("\\", "/"))
    if _is_windows_host() and wsl_match:
        drive = wsl_match.group("drive").upper()
        rest = wsl_match.group("rest").replace("/", "\\")
        return Path(f"{drive}:\\{rest}")

    match = WINDOWS_DRIVE_RE.match(value)
    if match and _is_wsl_host():
        drive = match.group("drive").lower()
        rest = match.group("rest").replace("\\", "/").strip("/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(value).expanduser()


def sanitize_path_component(value: str, fallback: str = "item") -> str:
    sanitized = INVALID_PATH_CHARS_RE.sub("_", value.strip())
    sanitized = MULTIPLE_UNDERSCORES_RE.sub("_", sanitized).strip("_")
    return sanitized or fallback


def sanitize_target_relative_path(path: str | PurePath) -> Path:
    raw_path = PurePath(path)
    sanitized_parts: list[str] = []
    for part in raw_path.parts:
        if part == raw_path.name:
            break
        sanitized_parts.append(sanitize_path_component(part))
    filename = raw_path.name
    suffix = "".join(Path(filename).suffixes)
    stem = filename.removesuffix(suffix) if suffix else filename
    sanitized_filename = sanitize_path_component(stem)
    return Path(*sanitized_parts, f"{sanitized_filename}{suffix}")


def _is_windows_host() -> bool:
    return os.name == "nt"


def _is_wsl_host() -> bool:
    if os.name == "nt":
        return False
    release = platform.release().lower()
    return "microsoft" in release or "wsl" in release or bool(
        os.environ.get("WSL_DISTRO_NAME")
    )
