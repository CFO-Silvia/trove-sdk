"""Per-session sandbox provisioner — mints a scoped key per agent run.

Run from your backend / orchestrator. Hands the freshly-minted key to the
agent process, then revokes it when the session ends. The admin key never
leaves this layer.

    python provision.py start abc123
    python provision.py end   abc123
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from trove_sdk import TroveAdminClient

load_dotenv()
REGISTRY = Path(__file__).parent / ".sessions.json"


def _load() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}


def _save(state: dict) -> None:
    REGISTRY.write_text(json.dumps(state, indent=2))


def start(session_id: str) -> None:
    """Mint a scoped key bound to this session's namespace."""
    admin = TroveAdminClient(
        api_key=os.environ["TROVE_ADMIN_KEY"],
        workspace_id=os.environ["TROVE_WORKSPACE_ID"],
    )
    namespace = f"session-{session_id}"
    key = admin.create_key(name=f"agent:{session_id}", namespace=namespace)

    state = _load()
    state[session_id] = {
        "namespace": namespace,
        "key_id":    key.key_id,
        "api_key":   key.api_key,  # in production: store in Vault/SSM, not on disk
    }
    _save(state)
    print(f"started session {session_id} → {namespace} (key {key.key_id})")


def end(session_id: str) -> None:
    """Revoke the scoped key. In-flight requests with it will return 401."""
    state = _load()
    if session_id not in state:
        raise SystemExit(f"no active session {session_id}")

    admin = TroveAdminClient(
        api_key=os.environ["TROVE_ADMIN_KEY"],
        workspace_id=os.environ["TROVE_WORKSPACE_ID"],
    )
    admin.revoke_key(state[session_id]["key_id"])
    del state[session_id]
    _save(state)
    print(f"ended session {session_id}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["start", "end"])
    p.add_argument("session_id")
    args = p.parse_args()
    {"start": start, "end": end}[args.action](args.session_id)
