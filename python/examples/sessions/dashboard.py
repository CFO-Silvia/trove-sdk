"""Ops dashboard — uses an unscoped runtime key to walk every active session.

Unscoped workspace keys can address any namespace by setting X-Namespace at
construction time. That's exactly what you want for billing rollups, capacity
planning, and abuse detection — read-only operations that span tenants.

Don't ship this key to agent processes. They get scoped keys (see provision.py).

    python dashboard.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from trove_sdk import TroveClient

load_dotenv()
REGISTRY = Path(__file__).parent / ".sessions.json"


def main() -> None:
    state = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    if not state:
        print("No active sessions.")
        return

    runtime_key = os.environ["TROVE_RUNTIME_KEY"]  # unscoped workspace key

    print(f"{'session':<24} {'files':>8} {'size':>10}")
    print("-" * 46)

    for session_id, meta in state.items():
        with TroveClient(api_key=runtime_key, namespace=meta["namespace"]) as fs:
            files = fs.exec("find workspace/ -type f | wc -l").strip()
            size  = fs.exec("du -sh workspace/ | cut -f1").strip()
        print(f"{session_id:<24} {files:>8} {size:>10}")


if __name__ == "__main__":
    main()
