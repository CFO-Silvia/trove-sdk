"""Shopping agent on Trove — no-namespace key pattern.

WHAT THIS DEMONSTRATES
──────────────────────
A workspace-scope key with NO namespace restriction. The caller picks the
namespace at request time. Use this pattern when one process serves many
short-lived sessions (one shopping cart per shopper, one sandbox per CI run,
etc.) and you don't want to mint a fresh key per session.

Compare with `personal_assistant.py`, which uses a key locked to one namespace.

THE SCENARIO
────────────
A shopping agent helps a customer pick a wireless mouse from a product catalog.
The agent reads the catalog with shell commands, filters by the customer's
constraints, then writes a short recommendation back to the workspace.

We snapshot the workspace before the agent runs so the cart can be rolled back.

SETUP
─────
    pip install anthropic python-dotenv trove-sdk
    cp examples/.env.example examples/.env   # fill in TROVE_SHOPPING_KEY + ANTHROPIC_API_KEY
    python examples/shopping_agent.py

SDK SURFACE USED
────────────────
    TroveClient(api_key, namespace)         # construct
    client.write(path, content)             # seed catalog
    client.exec_detailed(command)           # agent's tool: run shell, branch
                                            # on exit_code so failures flow
                                            # back to the model as is_error
    client.create_snapshot(label=...)       # checkpoint before agent runs
    client.list_snapshots()                 # show what was created
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from trove_sdk import TroveClient

# Make print() safe for Windows consoles when Claude returns emoji/em-dashes.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(Path(__file__).parent / ".env", override=True)

# ── 1. Pick a per-session namespace ───────────────────────────────────────────
# The shopping key isn't locked to a namespace, so we choose one per shopper.
# In a real app this would be the customer's session id.
SESSION_ID = f"shopper-{uuid.uuid4().hex[:8]}"

trove = TroveClient(
    api_key=os.environ["TROVE_SHOPPING_KEY"],
    namespace=SESSION_ID,
)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ── 2. Seed a tiny product catalog ────────────────────────────────────────────
def seed_catalog() -> None:
    catalog_csv = (
        "sku,name,price,battery_hours,noise_db,rating\n"
        "M-001,Logitech MX Master 3S,99.99,70,0,4.8\n"
        "M-002,Logitech M720,39.99,24,5,4.5\n"
        "M-003,Razer Pro Click Mini,79.99,725,5,4.4\n"
        "M-004,Anker 2.4G Wireless,15.99,18,12,4.1\n"
        "M-005,Microsoft Sculpt Ergonomic,49.99,12,8,4.3\n"
        "M-006,Keychron M3 Mini,49.00,80,3,4.6\n"
        "M-007,Apple Magic Mouse,99.00,30,4,4.2\n"
    )
    trove.write("workspace/catalog.csv", catalog_csv)
    trove.write(
        "workspace/customer_request.md",
        "# Customer request\n\n"
        "Quiet wireless mouse, budget under $50, rating at least 4.4.\n"
        "Battery life matters — prefers 50+ hours per charge.\n",
    )
    print(f"Seeded catalog into namespace '{SESSION_ID}'.\n")


# ── 3. Define the bash tool the agent can call ────────────────────────────────
BASH_TOOL = {
    "name": "bash",
    "description": (
        "Run a POSIX shell command in the customer's persistent workspace. "
        "Use this to read and filter files. Examples: "
        "`ls workspace/`, `cat workspace/catalog.csv`, "
        "`awk -F, 'NR>1 && $3<50' workspace/catalog.csv`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    """Execute a tool call. Returns (content, is_error).

    Uses ``exec_detailed`` rather than ``exec`` so a non-zero shell exit
    flows back to the model as an error tool result instead of raising
    ``TroveExecError`` and crashing the loop. Letting the model see
    `[exit N]` + stderr lets it self-correct (try a different path,
    quote the filename, etc.) on the next turn.
    """
    if name == "bash":
        r = trove.exec_detailed(args["command"])
        if r.exit_code == 0:
            return r.stdout, False
        return f"[exit {r.exit_code}]\n{r.stderr}".rstrip(), True
    raise ValueError(f"unknown tool: {name}")


# ── 4. Agent loop — Claude, with bash, until it stops calling tools ───────────
def run_agent(user_prompt: str) -> str:
    messages = [{"role": "user", "content": user_prompt}]
    while True:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=(
                "You are a shopping assistant. The customer's request and the "
                "product catalog live in `workspace/`. Read them, then pick the "
                "single best match and explain why in 2-3 sentences. "
                "When you've chosen, write your recommendation to "
                "`workspace/recommendation.md` using bash + heredoc, then stop."
            ),
            tools=[BASH_TOOL],
            messages=messages,
        )

        # Print every assistant turn so the demo is easy to follow.
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[claude] {block.text.strip()}")
            elif block.type == "tool_use":
                print(f"[bash]   $ {block.input['command']}")

        if response.stop_reason != "tool_use":
            return next(
                (b.text for b in response.content if b.type == "text"), ""
            )

        # Feed tool results back and continue the loop.
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tu in tool_uses:
            content, is_error = run_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": content,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": tool_results})


# ── 5. Tie it together ────────────────────────────────────────────────────────
def main() -> None:
    seed_catalog()

    snap = trove.create_snapshot(label="before-recommendation")
    print(f"Snapshot: {snap.snapshot_id} ({snap.size_bytes} bytes)\n")

    print("--- agent run ---")
    final = run_agent(
        "Read workspace/customer_request.md and workspace/catalog.csv, then "
        "pick the best mouse for the customer."
    )
    print("--- agent done ---\n")

    print("=== Final answer ===")
    print(final or "(no text)")
    print()

    print("=== workspace/recommendation.md ===")
    print(trove.exec("cat workspace/recommendation.md"))

    print(f"\nSnapshots in namespace '{SESSION_ID}':")
    for s in trove.list_snapshots():
        print(f"  - {s.snapshot_id}  label={s.label!r}  bytes={s.size_bytes}")


if __name__ == "__main__":
    main()
