"""Pydantic AI + Trove: basic retrieval example.

A Pydantic AI agent backed by Claude that reads files from a Trove workspace.
Uses @agent.tool_plain (no RunContext) and run_sync for simple synchronous use.

Setup:
    pip install "pydantic-ai[anthropic]" python-dotenv trove-sdk
    cp examples/.env.example examples/.env   # fill in TROVE_API_KEY + ANTHROPIC_API_KEY
    python examples/pydantic_ai_retrieval.py
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

from trove_sdk import TroveClient

load_dotenv(Path(__file__).parent / ".env", override=True)

NAMESPACE = "pydantic-ai-example"
trove = TroveClient(api_key=os.environ["TROVE_API_KEY"], namespace=NAMESPACE)


# ── Seed ──────────────────────────────────────────────────────────────────────

def seed_workspace() -> None:
    trove.write("workspace/notes/q1-revenue.md", """\
# Q1 2025 Revenue

Total revenue:  $1.24M
  Product:      $890K
  Services:     $350K

YoY growth:     +23%
Largest deal:   Acme Corp  ($120K ARR)
""")
    trove.write("workspace/notes/team.md", """\
# Team Roster

Engineering (8):  lead — Alice Chen
Sales (3):        lead — Dan Torres
Total headcount:  11
""")
    print("Workspace seeded.\n")


# ── Agent ─────────────────────────────────────────────────────────────────────

agent = Agent(
    AnthropicModel("claude-opus-4-7"),
    system_prompt=(
        "You are a helpful assistant with access to a persistent workspace. "
        "Use the bash tool to discover and read files before answering. "
        "Start with `ls workspace/notes/` to see what's available."
    ),
)


@agent.tool_plain
def bash(command: str) -> str:
    """Run a POSIX shell command in the agent's persistent workspace.

    Use ls, cat, grep, find to discover and read files. Examples:
      ls workspace/notes/
      cat workspace/notes/q1-revenue.md
      grep -r 'revenue' workspace/
    """
    # exec_detailed so a non-zero shell exit comes back as text the model
    # can read, rather than raising TroveExecError through pydantic-ai's
    # tool wrapper.
    r = trove.exec_detailed(command)
    if r.exit_code == 0:
        return r.stdout
    return f"[exit {r.exit_code}]\n{r.stderr}".rstrip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    seed_workspace()

    questions = [
        "What was Q1 2025 total revenue and YoY growth?",
        "Who leads the engineering team and how many engineers are there?",
    ]
    for q in questions:
        print(f"\n{'='*60}\nQ: {q}\n{'='*60}")
        result = agent.run_sync(q)
        print(f"\nA: {result.output}")


if __name__ == "__main__":
    main()
