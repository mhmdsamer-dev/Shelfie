from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

PYPI_PROJECT_NAME = "shelfie-py"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PROJECT_NAME}/json"


def _numeric_version_parts(version: str) -> tuple[int, ...]:
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", version.strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: str, installed: str) -> bool:
    candidate_parts = _numeric_version_parts(candidate)
    installed_parts = _numeric_version_parts(installed)

    if not candidate_parts or not installed_parts:
        return False

    max_len = max(len(candidate_parts), len(installed_parts))
    candidate_parts += (0,) * (max_len - len(candidate_parts))
    installed_parts += (0,) * (max_len - len(installed_parts))
    return candidate_parts > installed_parts


def fetch_latest_release_version(timeout: float = 2.5) -> str | None:
    request = Request(
        PYPI_JSON_URL,
        headers={"User-Agent": "shelfie-update-checker/1"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None

    latest = payload.get("info", {}).get("version")
    return latest if isinstance(latest, str) and latest.strip() else None


def check_for_newer_release(
    installed_version: str,
    fetch_latest: Callable[[], str | None] = fetch_latest_release_version,
) -> str | None:
    latest = fetch_latest()
    if not latest:
        return None

    if is_newer_version(latest, installed_version):
        return latest

    return None
