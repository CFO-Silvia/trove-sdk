"""Personal assistant on Trove — namespace-locked key pattern.

WHAT THIS DEMONSTRATES
──────────────────────
A workspace-scope key LOCKED to a single namespace. Every request from this
key is forced into that namespace by the server — even if the SDK tries to
talk to another namespace, the API rejects it. This is the right pattern for
giving a persistent agent access to ONE user's private files (notes, todos,
preferences) with no risk of cross-tenant leakage.

Compare with `shopping_agent.py`, which uses an unlocked key that picks a
namespace per request.

THE SCENARIO
────────────
A personal assistant agent for a single user. The user's notes, profile, and
todos live in their namespace. The agent reads them and answers questions
across two turns of conversation, demonstrating that state persists between
calls (the workspace is durable; the agent process is not).

We also prove the namespace lock by trying to access a different namespace
and showing the API refuses.

SETUP
─────
    pip install anthropic python-dotenv trove-sdk
    cp examples/.env.example examples/.env
    # fill in TROVE_PERSONAL_KEY, TROVE_PERSONAL_NAMESPACE, ANTHROPIC_API_KEY
    python examples/personal_assistant.py

SDK SURFACE USED
────────────────
    TroveClient(api_key, namespace)         # construct (namespace must match the key's lock)
    client.write(path, content)             # seed user files
    client.exec(command)                    # agent's tool: run shell
    TroveError                              # raised when the lock blocks a call
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from trove_sdk import TroveClient, TroveError

# Make print() safe for Windows consoles when Claude returns emoji/em-dashes.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(Path(__file__).parent / ".env", override=True)

USER_NAMESPACE = os.environ["TROVE_PERSONAL_NAMESPACE"]  # the key is locked to this

trove = TroveClient(
    api_key=os.environ["TROVE_PERSONAL_KEY"],
    namespace=USER_NAMESPACE,
)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ── 1. Seed the user's private files ──────────────────────────────────────────
def seed_user_files() -> None:
    trove.write(
        "workspace/profile.md",
        "# Profile\n\n"
        "- Name: Alex\n"
        "- Timezone: America/New_York\n"
        "- Diet: vegetarian, no nuts (allergy)\n"
        "- Coffee: oat milk, no sugar\n",
    )
    trove.write(
        "workspace/todos.md",
        "# Todos\n\n"
        "- [ ] Renew passport (expires August)\n"
        "- [ ] Book dentist for cleaning\n"
        "- [x] Pay credit card bill\n"
        "- [ ] Reply to Sam about dinner Friday\n",
    )
    trove.write(
        "workspace/notes/2026-04-28-team-offsite.md",
        "# Team offsite — Apr 28\n\n"
        "Decided Q3 theme: 'reliability'. Sam is owning the SLO doc, "
        "due May 15. I owe Priya a draft of the on-call rotation by next Monday.\n",
    )
    print(f"Seeded files into namespace '{USER_NAMESPACE}'.\n")


# ── 2. Prove the namespace lock — the API rejects other namespaces ────────────
def demonstrate_namespace_lock() -> None:
    print("Verifying namespace lock...")
    try:
        TroveClient(
            api_key=os.environ["TROVE_PERSONAL_KEY"],
            namespace="some-other-user",
        ).exec("ls /")
    except TroveError as e:
        print(f"  [ok] rejected access to 'some-other-user' (status {e.status_code}): {e}\n")
    else:
        print("  [!!] unexpected: the API allowed cross-namespace access.\n")


# ── 3. Bash tool the agent can call ───────────────────────────────────────────
BASH_TOOL = {
    "name": "bash",
    "description": (
        "Run a POSIX shell command in the user's persistent workspace. "
        "Use this to read or update the user's files. Examples: "
        "`ls workspace/`, `cat workspace/todos.md`, "
        "`grep -ri 'dentist' workspace/`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}


# ── 4. Agent loop — Claude, with bash, across multiple user turns ─────────────
SYSTEM_PROMPT = (
    "You are Alex's personal assistant. Their notes, profile, and todos live "
    "in `workspace/`. Always read the relevant files BEFORE answering a "
    "question — don't guess. Be concise. When the user asks you to add or "
    "update something, edit the file with bash and confirm what changed."
)


def chat(messages: list[dict]) -> tuple[str, list[dict]]:
    """One full turn: keep calling tools until Claude stops. Returns (final_text, updated_messages)."""
    while True:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[BASH_TOOL],
            messages=messages,
        )

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[claude] {block.text.strip()}")
            elif block.type == "tool_use":
                print(f"[bash]   $ {block.input['command']}")

        if response.stop_reason != "tool_use":
            text = next((b.text for b in response.content if b.type == "text"), "")
            messages.append({"role": "assistant", "content": response.content})
            return text, messages

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": response.content})
        # exec_detailed gives us exit_code/stdout/stderr separately so a
        # failing command flows back to the model as is_error=True instead
        # of raising TroveExecError and crashing the loop.
        tool_results = []
        for tu in tool_uses:
            r = trove.exec_detailed(tu.input["command"])
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": r.stdout if r.exit_code == 0 else (
                    f"[exit {r.exit_code}]\n{r.stderr}".rstrip()
                ),
                "is_error": r.exit_code != 0,
            })
        messages.append({"role": "user", "content": tool_results})


# ── 5. Tie it together ────────────────────────────────────────────────────────
def main() -> None:
    seed_user_files()
    demonstrate_namespace_lock()

    history: list[dict] = []

    for user_message in [
        "What dietary stuff do I need to remember when ordering food?",
        "Add 'Draft on-call rotation for Priya' to my todos.",
    ]:
        print(f"\n=== user: {user_message} ===")
        _, history = chat(history + [{"role": "user", "content": user_message}])

    print("\n=== Final state of workspace/todos.md ===")
    print(trove.exec("cat workspace/todos.md"))


if __name__ == "__main__":
    main()
