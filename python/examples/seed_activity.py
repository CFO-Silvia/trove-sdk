"""Seed varied activity events across a few namespaces so the dashboard has data."""
from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv

from trove_sdk import TroveClient

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

api_key = os.environ["TROVE_API_KEY"].strip()


def seed(namespace: str) -> None:
    print(f"\n--- {namespace} ---")
    with TroveClient(api_key=api_key, namespace=namespace) as t:
        t.write("notes/intro.md", f"# {namespace}\n\nseed run at {time.time():.0f}\n")
        t.write("notes/todo.md", "- [ ] ship\n- [ ] iterate\n")
        t.write("data/sample.json", '{"hello": "world"}\n')
        t.exec("ls -la notes/")
        t.exec("wc -l notes/*.md")
        t.upload("data/blob.bin", b"\x00\x01\x02hello-bytes\n")
        snap = t.create_snapshot(label=f"seed-{int(time.time())}")
        print(f"snapshot {snap.snapshot_id}")
        t.delete("notes/todo.md")
        t.exec("echo done")


for ns in ("demo", "staging", "scratch"):
    seed(ns)

print("\nDone. Check the Activity tab.")
