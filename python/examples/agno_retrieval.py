"""Agno + Trove: basic retrieval example.

An Agno agent backed by Claude that reads files from a Trove workspace.
Plain Python functions are passed directly as tools — no decorator needed.

Setup:
    pip install agno anthropic python-dotenv trove-sdk
    cp examples/.env.example examples/.env   # fill in TROVE_API_KEY + ANTHROPIC_API_KEY
    python examples/agno_retrieval.py
"""
from __future__ import annotations

import os
from pathlib import Path

from agno.agent import Agent
from agno.models.anthropic import Claude
from dotenv import load_dotenv

from trove_sdk import TroveClient

load_dotenv(Path(__file__).parent / ".env", override=True)

NAMESPACE = "agno-example"
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


# ── Tool — plain function, docstring becomes the tool description ──────────────

def bash(command: str) -> str:
    """Run a POSIX shell command in the agent's persistent workspace.

    Use ls, cat, grep, find to discover and read files. Examples:
      ls workspace/notes/
      cat workspace/notes/q1-revenue.md
      grep -r 'revenue' workspace/

    Args:
        command: Shell command to execute.

    Returns:
        Command stdout/stderr as plain text.
    """
    return trove.exec(command)


# ── Agent ─────────────────────────────────────────────────────────────────────

def main() -> None:
    seed_workspace()

    agent = Agent(
        model=Claude(id="claude-opus-4-7", api_key=os.environ["ANTHROPIC_API_KEY"]),
        tools=[bash],
        instructions=(
            "You have access to a persistent workspace via the bash tool. "
            "Always read the relevant files before answering — start with "
            "`ls workspace/notes/` to see what's available."
        ),
        markdown=True,
        debug_mode=False,
    )

    questions = [
        "What was Q1 2025 total revenue and YoY growth?",
        "How many people are on the team in total?",
    ]
    for q in questions:
        print(f"\n{'='*60}\nQ: {q}\n{'='*60}")
        agent.print_response(q)


if __name__ == "__main__":
    main()
