"""Bootstrap pattern: agents that remember themselves across sessions.

Each invocation simulates one "session" — a separate process picking up the
same Trove namespace. The killer move is `client.bootstrap()`: one call
returns recent files, the active init.sh, and the handoff note that the
*previous* session wrote. Render it into the model's system prompt and the
agent orients before its first tool call instead of probing with `ls`.

The cross-session handoff slot is `workspace/.trove/agent.md` — just a file,
written with the normal `client.write(...)`. The runtime doesn't parse it;
agents pick whatever format works.

Run twice in the same namespace to see the effect:

    python bootstrap.py session-1   # writes a note
    python bootstrap.py session-1   # reads it back via bootstrap
"""
from __future__ import annotations

import os
import sys

from trove_sdk import TroveClient


def session_one(client: TroveClient) -> None:
    """First-time work: produces some files and leaves a note."""
    print("=== session 1: cold start ===\n")
    bs = client.bootstrap()
    print(bs.as_system_prompt_block(), "\n")

    # Do real work
    client.write("workspace/data.csv", "name,score\nalice,0.9\nbob,0.7\n")
    client.exec("awk -F, 'NR>1{sum+=$2} END{print sum/(NR-1)}' workspace/data.csv > workspace/avg.txt")

    # Leave a handoff note for the next session — just a write to the
    # convention path. No special API.
    client.write("workspace/.trove/agent.md", (
        "## Session 1 handoff\n"
        "- Loaded `workspace/data.csv` (2 rows, headers: name,score)\n"
        "- Wrote average score to `workspace/avg.txt`\n"
        "- Next: try a weighted average if more rows arrive\n"
    ))
    print("session 1 finished — note left at workspace/.trove/agent.md\n")


def session_two(client: TroveClient) -> None:
    """Picks up where session 1 left off, surfaced via bootstrap()."""
    print("=== session 2: warm start ===\n")
    bs = client.bootstrap()
    block = bs.as_system_prompt_block()
    print(block, "\n")

    # The block above is what you'd inject into the model's system message.
    # Below is what the wrapping app might do programmatically with the same data.
    if bs.agent_memory:
        print("Found a handoff note. Resuming work with that context.")
    if any(f.path == "workspace/avg.txt" for f in bs.recent_files):
        avg = client.read_text("workspace/avg.txt").strip()
        print(f"Previous session computed avg = {avg}; not recomputing.")


def main() -> None:
    api_key = os.environ.get("TROVE_API_KEY")
    if not api_key:
        sys.exit("set TROVE_API_KEY (run `trove login` first to provision one)")
    namespace = sys.argv[1] if len(sys.argv) > 1 else "bootstrap-demo"

    with TroveClient(api_key=api_key, namespace=namespace) as client:
        # If a handoff note exists, this is a warm start. Otherwise cold start.
        if client.bootstrap().agent_memory:
            session_two(client)
        else:
            session_one(client)


if __name__ == "__main__":
    main()
