"""End-to-end smoke test for the new /v1/snapshots endpoints against prod."""
from __future__ import annotations

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


def step(label: str) -> None:
    print(f"\n── {label} ──")


def main() -> int:
    env = load_env(Path(__file__).resolve().parents[2] / ".env")
    api_key = env["TROVE_API_KEY"]
    namespace = "snapshot-smoke"

    with TroveClient(api_key=api_key, namespace=namespace) as c:
        # Clean slate
        c.exec("rm -rf workspace/* 2>/dev/null; ls workspace/")

        step("Stage two files")
        c.write("workspace/important.txt", "the launch goes well\n")
        c.write("workspace/other.txt",     "supporting context\n")
        print(c.exec("ls -la workspace/"))

        step("Create snapshot")
        snap = c.create_snapshot(label="before-deletion")
        print(f"snapshot_id = {snap.snapshot_id}")
        print(f"size_bytes  = {snap.size_bytes}")
        print(f"label       = {snap.label}")
        print(f"created_at  = {snap.created_at}")

        step("List snapshots")
        for s in c.list_snapshots():
            print(f"  {s.snapshot_id}  {s.size_bytes:>6} B  {s.created_at}")

        step("Simulate disaster: rm important.txt")
        print(c.exec("rm workspace/important.txt && ls -la workspace/").rstrip())

        step("Restore from snapshot")
        files_restored = c.restore_snapshot(snap.snapshot_id)
        print(f"files_restored = {files_restored}")
        print(c.exec("ls -la workspace/").rstrip())

        step("Verify content survived round-trip")
        out = c.exec("cat workspace/important.txt").rstrip()
        print(f"important.txt: {out!r}")
        assert out == "the launch goes well", f"content mismatch: {out!r}"

        step("Cleanup")
        c.delete_snapshot(snap.snapshot_id)
        c.exec("rm -rf workspace/*")

    print("\nSmoke test PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
