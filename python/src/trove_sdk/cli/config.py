"""Local CLI config at ~/.trove/config.json.

JSON instead of TOML so we don't pull tomli for Python 3.10. Profiles let one
machine target prod and staging without re-logging in. The file is chmod 600
on POSIX since it holds the API key.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".trove"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_BASE_URL = "https://api.trovefiles.dev"


@dataclass
class Profile:
    api_key: str
    workspace_id: str
    base_url: str = DEFAULT_BASE_URL
    namespace: Optional[str] = None  # default ns for runtime cmds (run/ls/cat/...)


def _read_all() -> dict:
    if not CONFIG_FILE.exists():
        return {"profiles": {}}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"profiles": {}}


def _write_all(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if os.name == "posix":
        os.chmod(CONFIG_FILE, 0o600)


def list_profiles() -> list[str]:
    return list(_read_all().get("profiles", {}).keys())


def get_profile(name: str) -> Optional[Profile]:
    p = _read_all().get("profiles", {}).get(name)
    if not p:
        return None
    return Profile(
        api_key=p["api_key"],
        workspace_id=p["workspace_id"],
        base_url=p.get("base_url", DEFAULT_BASE_URL),
        namespace=p.get("namespace"),
    )


def save_profile(name: str, profile: Profile) -> None:
    data = _read_all()
    entry = {
        "api_key": profile.api_key,
        "workspace_id": profile.workspace_id,
        "base_url": profile.base_url,
    }
    if profile.namespace:
        entry["namespace"] = profile.namespace
    data.setdefault("profiles", {})[name] = entry
    _write_all(data)


def delete_profile(name: str) -> bool:
    data = _read_all()
    if name in data.get("profiles", {}):
        del data["profiles"][name]
        _write_all(data)
        return True
    return False


def resolve(name: Optional[str]) -> tuple[str, Profile]:
    """Pick a profile by name; fall back to env vars; otherwise raise.

    Returns (profile_name, Profile). Env vars take precedence only when no
    profile is explicitly named — keeps `--profile staging` predictable.
    """
    if name is None:
        env_key = os.environ.get("TROVE_API_KEY")
        env_ws = os.environ.get("TROVE_WORKSPACE_ID")
        if env_key and env_ws:
            return "env", Profile(
                api_key=env_key,
                workspace_id=env_ws,
                base_url=os.environ.get("TROVE_BASE_URL", DEFAULT_BASE_URL),
                namespace=os.environ.get("TROVE_NAMESPACE"),
            )
        name = "default"

    p = get_profile(name)
    if not p:
        raise LookupError(f"No profile '{name}' configured. Run `trove login` first.")
    return name, p


def resolve_namespace(profile: Profile, override: Optional[str]) -> Optional[str]:
    """Pick the namespace for a runtime command.

    Order: explicit `--namespace` > $TROVE_NAMESPACE > profile.namespace > None.
    Returns None when none is set; callers raise if their endpoint needs one.
    """
    if override:
        return override
    env = os.environ.get("TROVE_NAMESPACE")
    if env:
        return env
    return profile.namespace
