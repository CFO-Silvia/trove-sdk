"""LangChain + Trove: basic retrieval example.

Demonstrates how to wire a Trove workspace as the filesystem behind a LangChain
agent. The agent answers questions by reading files via POSIX commands.

Setup:
    pip install "langchain>=1.0" langchain-anthropic python-dotenv trove-sdk
    cp examples/.env.example examples/.env   # fill in TROVE_API_KEY + ANTHROPIC_API_KEY
    python examples/langchain_retrieval.py
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool

from trove_sdk import TroveClient

load_dotenv(Path(__file__).parent / ".env", override=True)

NAMESPACE = "langchain-example"
trove = TroveClient(api_key=os.environ["TROVE_API_KEY"], namespace=NAMESPACE)


# ── Seed a small knowledge base ───────────────────────────────────────────────

def seed_workspace() -> None:
    trove.write("workspace/notes/index.md", """\
# Knowledge Base

- notes/q1-revenue.md  — Q1 2025 revenue summary
- notes/team.md        — current team roster
""")
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

Engineering (8):
  - Alice Chen  (lead)
  - Bob Patel, Carol Wu + 5 others

Sales (3):
  - Dan Torres  (lead) + 2 others

Total headcount: 11
""")
    print("Workspace seeded.\n")


# ── Bash tool ─────────────────────────────────────────────────────────────────

@tool
def bash(command: str) -> str:
    """Run a POSIX shell command in the agent's persistent workspace.

    Use this to discover, read, and search files. Examples:
      ls workspace/notes/
      cat workspace/notes/q1-revenue.md
      grep -r 'revenue' workspace/
    """
    # exec_detailed so a non-zero shell exit returns as text the model
    # can read, rather than raising TroveExecError through LangChain's
    # tool wrapper.
    r = trove.exec_detailed(command)
    if r.exit_code == 0:
        return r.stdout
    return f"[exit {r.exit_code}]\n{r.stderr}".rstrip()


# ── Agent ─────────────────────────────────────────────────────────────────────

def main() -> None:
    seed_workspace()

    llm = ChatAnthropic(
        model="claude-opus-4-7",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )

    agent = create_agent(
        model=llm,
        tools=[bash],
        system_prompt=(
            "You are a helpful assistant with access to a persistent workspace. "
            "Use the bash tool to read files before answering. "
            "Start with `ls workspace/notes/` to orient yourself."
        ),
    )

    questions = [
        "What was Q1 2025 total revenue and YoY growth?",
        "How many engineers are on the team and who is the lead?",
    ]
    for q in questions:
        print(f"\n{'='*60}\nQ: {q}\n{'='*60}")
        result = agent.invoke({"messages": [("human", q)]})
        print(f"\nA: {result['messages'][-1].content}")


if __name__ == "__main__":
    main()
