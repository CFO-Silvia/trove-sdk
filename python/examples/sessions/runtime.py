"""Agent runtime — what the worker process actually does in its sandbox.

Receives a scoped key from the provisioner. The TroveClient is hard-isolated
to one namespace: any request with X-Namespace pointing elsewhere is a 403,
regardless of what path the agent tries to reach.

    python runtime.py abc123 "summarize https://example.com"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from trove_sdk import TroveClient

REGISTRY = Path(__file__).parent / ".sessions.json"


def run(session_id: str, task: str) -> None:
    state = json.loads(REGISTRY.read_text())
    session = state[session_id]

    with TroveClient(api_key=session["api_key"], namespace=session["namespace"]) as fs:
        # Persist the task brief so the agent can re-read it across turns.
        fs.write("workspace/task.md", f"# Task\n\n{task}\n")

        # A real agent loop would call an LLM here. We do a representative
        # POSIX flow: fetch a doc, count something, write a result file.
        fs.exec("curl -sf https://example.com/ -o workspace/page.html")
        word_count = fs.exec("wc -w < workspace/page.html").strip()

        fs.write(
            "workspace/result.md",
            f"# Result\n\nFetched page word count: {word_count}\n",
        )

        # Snapshot before the session ends so you can roll back later.
        snap = fs.create_snapshot(label=f"end-of-{session_id}")
        print(f"[{session_id}] {word_count} words · snapshot {snap.snapshot_id}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: runtime.py <session_id> <task>")
    run(sys.argv[1], " ".join(sys.argv[2:]))
