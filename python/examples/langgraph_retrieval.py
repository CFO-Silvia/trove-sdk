"""LangGraph + Trove: basic retrieval example.

Builds a ReAct agent graph manually using LangGraph's StateGraph — a model node
that calls Claude and a tool node that dispatches bash commands to Trove.

Setup:
    pip install langgraph langchain-anthropic python-dotenv trove-sdk
    cp examples/.env.example examples/.env   # fill in TROVE_API_KEY + ANTHROPIC_API_KEY
    python examples/langgraph_retrieval.py
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from trove_sdk import TroveClient

load_dotenv(Path(__file__).parent / ".env", override=True)

NAMESPACE = "langgraph-example"
trove = TroveClient(api_key=os.environ["TROVE_API_KEY"], namespace=NAMESPACE)

SYSTEM = SystemMessage(content=(
    "You are a helpful assistant with access to a persistent workspace. "
    "Use the bash tool to read files before answering. "
    "Start with `ls workspace/notes/` to orient yourself."
))


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


# ── Tool ──────────────────────────────────────────────────────────────────────

@tool
def bash(command: str) -> str:
    """Run a POSIX shell command in the agent's persistent workspace.

    Use ls, cat, grep, find to discover and read files. Examples:
      ls workspace/notes/
      cat workspace/notes/q1-revenue.md
      grep -r 'revenue' workspace/
    """
    return trove.exec(command)


# ── Graph ─────────────────────────────────────────────────────────────────────

class State(TypedDict):
    messages: Annotated[list, add_messages]


def build_graph():
    tools = [bash]
    llm = ChatAnthropic(
        model="claude-opus-4-7",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    ).bind_tools(tools)

    def call_model(state: State) -> dict:
        messages = [SYSTEM, *state["messages"]]
        return {"messages": [llm.invoke(messages)]}

    graph = StateGraph(State)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    seed_workspace()
    agent = build_graph()

    questions = [
        "What was Q1 2025 total revenue and YoY growth?",
        "Who leads the engineering team?",
    ]
    for q in questions:
        print(f"\n{'='*60}\nQ: {q}\n{'='*60}")
        result = agent.invoke({"messages": [("human", q)]})
        print(f"\nA: {result['messages'][-1].content}")


if __name__ == "__main__":
    main()
