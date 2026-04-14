from __future__ import annotations

import re
from pathlib import Path


WINDOWS_DRIVE_RE = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")


def normalize_user_path(raw_path: str) -> Path:
    value = raw_path.strip()
    match = WINDOWS_DRIVE_RE.match(value)
    if match:
        drive = match.group("drive").lower()
        rest = match.group("rest").replace("\\", "/").strip("/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(value).expanduser()
