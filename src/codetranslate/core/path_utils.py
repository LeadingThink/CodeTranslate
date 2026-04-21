from __future__ import annotations

import os
import platform
import re
from pathlib import Path


WINDOWS_DRIVE_RE = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")
WSL_MOUNT_RE = re.compile(r"^/mnt/(?P<drive>[A-Za-z])/(?P<rest>.*)$")


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


def _is_windows_host() -> bool:
    return os.name == "nt"


def _is_wsl_host() -> bool:
    if os.name == "nt":
        return False
    release = platform.release().lower()
    return "microsoft" in release or "wsl" in release or bool(
        os.environ.get("WSL_DISTRO_NAME")
    )
