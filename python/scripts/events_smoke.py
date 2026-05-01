"""Smoke-test the new /v1/events activity feed against prod."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from trove_sdk import TroveClient


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def main() -> int:
    env = load_env(Path(__file__).resolve().parents[2] / ".env")
    api_key   = env["TROVE_API_KEY"]
    admin_key = env["TROVE_ADMIN_KEY"]
    ws_id     = "ws-d69634745de7ce97"
    ns        = "events-smoke"

    # Generate some activity
    print("→ Generating events…")
    with TroveClient(api_key=api_key, namespace=ns) as c:
        c.exec("rm -rf workspace/* 2>/dev/null")
        c.write("workspace/note.txt", "hello activity")
        c.exec("ls -la workspace/")
        c.delete("workspace/note.txt")
        snap = c.create_snapshot(label="smoke-checkpoint")
        c.delete_snapshot(snap.snapshot_id)

    # Wait briefly for the async log writes to flush
    print("→ Waiting 2s for log writes…")
    time.sleep(2)

    # Query events via the management API directly (mirrors what the dashboard does)
    print("→ Fetching events…")
    resp = httpx.get(
        f"https://api.trovefiles.dev/v1/workspaces/{ws_id}/events",
        params={"namespace": ns, "limit": 20},
        headers={"Authorization": f"Bearer {admin_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()

    events = body["events"]
    print(f"\n  Got {len(events)} events:\n")
    for e in events:
        ts = e["created_at"][:19]
        ns_label = e.get("namespace") or "—"
        print(f"  {ts}  {e['type']:<22}  ns={ns_label:<14}  {e.get('event_id', '')}")

    if len(events) >= 5:
        print("\nSmoke test PASSED")
        return 0
    print(f"\nSmoke test FAILED — expected ≥5 events, got {len(events)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
