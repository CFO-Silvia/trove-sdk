"""Drive Trove the way an LLM agent would: explore, retrieve, aggregate.
Times each /exec round-trip so I can describe what it feels like."""
from __future__ import annotations

import time
from pathlib import Path

from trove_sdk import TroveClient


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def timed(client: TroveClient, label: str, command: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    out = client.exec(command)
    dt = (time.perf_counter() - t0) * 1000
    print(f"\n[{dt:6.0f} ms] {label}")
    print(f"$ {command}")
    print(out.rstrip() if out else "(empty)")
    return out, dt


def main() -> int:
    env = load_env(Path(__file__).resolve().parents[2] / ".env")
    api_key = env["TROVE_API_KEY"]
    times: list[float] = []

    print("═" * 60)
    print("  Discovery — what namespaces does this workspace have?")
    print("═" * 60)

    # The SDK doesn't expose a list-namespaces endpoint; the agent has to
    # know its namespace upfront. So I'll try a few I know are populated.
    for ns in ["agno-example", "langgraph-example", "multimodal-proof"]:
        with TroveClient(api_key=api_key, namespace=ns) as c:
            print(f"\n── namespace: {ns} ──")
            _, dt = timed(c, "list root", "ls -la workspace/ 2>&1 | head -20")
            times.append(dt)

    print("\n" + "═" * 60)
    print("  Retrieval task #1: 'What's the team like?'")
    print("═" * 60)
    with TroveClient(api_key=api_key, namespace="agno-example") as c:
        _, dt = timed(c, "find any 'team' file", "find workspace/ -iname '*team*' -type f")
        times.append(dt)
        out, dt = timed(c, "read team note", "cat workspace/notes/team.md")
        times.append(dt)

    print("\n" + "═" * 60)
    print("  Retrieval task #2: 'Anything about Q1 revenue?'")
    print("═" * 60)
    with TroveClient(api_key=api_key, namespace="agno-example") as c:
        _, dt = timed(c, "grep across all notes",
                      "grep -ril 'revenue\\|Q1' workspace/ 2>/dev/null")
        times.append(dt)
        _, dt = timed(c, "show matching lines with context",
                      "grep -B 1 -A 3 -i 'revenue' workspace/notes/*.md")
        times.append(dt)

    print("\n" + "═" * 60)
    print("  Retrieval task #3: 'Extract text from the contract PDF'")
    print("═" * 60)
    with TroveClient(api_key=api_key, namespace="multimodal-proof") as c:
        _, dt = timed(c, "pdf metadata first",
                      "pdfinfo workspace/contract.pdf | head -5")
        times.append(dt)
        _, dt = timed(c, "extract & search for 'breach'",
                      "pdftotext workspace/contract.pdf - | grep -B 0 -A 1 -i breach")
        times.append(dt)

    print("\n" + "═" * 60)
    print("  Retrieval task #4: 'Aggregate sales by region'")
    print("═" * 60)
    with TroveClient(api_key=api_key, namespace="multimodal-proof") as c:
        _, dt = timed(c, "csv preview", "head -3 workspace/sales.csv")
        times.append(dt)
        _, dt = timed(c, "sum by region",
                      "awk -F, 'NR>1{r[$3]+=$2} END{for (k in r) print k, r[k]}' "
                      "workspace/sales.csv | sort -k2 -rn")
        times.append(dt)

    print("\n" + "═" * 60)
    print("  Multi-step: 'Find PDFs, extract first line of each'")
    print("═" * 60)
    with TroveClient(api_key=api_key, namespace="multimodal-proof") as c:
        _, dt = timed(c, "find + xargs pdftotext + head",
                      "find workspace/ -name '*.pdf' -exec sh -c 'echo \"== $1 ==\"; "
                      "pdftotext \"$1\" - | head -1' _ {} \\;")
        times.append(dt)

    print("\n" + "═" * 60)
    print(f"  Latency: min {min(times):.0f}ms  median {sorted(times)[len(times)//2]:.0f}ms  "
          f"max {max(times):.0f}ms  ({len(times)} calls)")
    print("═" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
