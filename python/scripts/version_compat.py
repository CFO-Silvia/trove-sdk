"""Phase 1 — verify S3 Files writes/deletes flow through bucket versioning cleanly.

Steps:
  1. Create a dev workspace via open signup
  2. Run: write v1 → write v2 → delete (via /delete) → write v3 → rm (via /exec)
  3. List S3 versions for each file
  4. Decide: are versions 1:1 with filesystem ops, or is there noise?
"""
from __future__ import annotations

import json
import time

import boto3
import httpx

DEV_URL = "https://TroveS-Farga-l26Qz4ZsWaub-1833993557.us-east-1.elb.amazonaws.com"
BUCKET  = "trove-dev-383140420039"
NAMESPACE = "version-compat"

s3 = boto3.client("s3")


def step(label: str, body: str = "") -> None:
    print(f"\n── {label} ──")
    if body:
        print(body.rstrip())


def list_versions(prefix: str) -> list[dict]:
    """Return all version + delete-marker entries under a prefix, sorted by time."""
    resp = s3.list_object_versions(Bucket=BUCKET, Prefix=prefix)
    items = []
    for v in resp.get("Versions", []):
        items.append({
            "kind": "version",
            "key": v["Key"],
            "version_id": v["VersionId"],
            "size": v["Size"],
            "is_latest": v["IsLatest"],
            "modified": v["LastModified"].isoformat(),
        })
    for m in resp.get("DeleteMarkers", []):
        items.append({
            "kind": "delete-marker",
            "key": m["Key"],
            "version_id": m["VersionId"],
            "is_latest": m["IsLatest"],
            "modified": m["LastModified"].isoformat(),
        })
    items.sort(key=lambda i: i["modified"])
    return items


def show_versions(prefix: str) -> None:
    versions = list_versions(prefix)
    if not versions:
        print("  (no versions)")
        return
    for v in versions:
        marker = "DELETE" if v["kind"] == "delete-marker" else f"v{v.get('size', '?')}B"
        cur    = "  ← current" if v["is_latest"] else ""
        print(f"  {v['modified'][:19]} | {marker:>9} | {v['version_id'][:24]}…{cur}")


def main() -> int:
    # Use httpx directly: dev ALB cert is for api.trovefiles.dev, doesn't match ELB DNS.
    client = httpx.Client(base_url=DEV_URL, verify=False, timeout=30)

    # 1. Create workspace
    step("Create dev workspace")
    resp = client.post("/v1/workspaces", json={"name": "version-compat"})
    resp.raise_for_status()
    ws       = resp.json()
    ws_id    = ws["workspace_id"]
    api_key  = ws["api_key"]
    headers  = {"Authorization": f"Bearer {api_key}", "X-Namespace": NAMESPACE}
    print(f"  workspace_id = {ws_id}")
    s3_prefix = f"{ws_id}/{NAMESPACE}/"

    def write(path: str, content: str) -> None:
        r = client.post("/write", json={"path": path, "content": content}, headers=headers)
        r.raise_for_status()

    def delete(path: str) -> None:
        r = client.post("/delete", json={"path": path}, headers=headers)
        r.raise_for_status()

    def execute(cmd: str) -> str:
        r = client.post("/exec", json={"command": cmd}, headers=headers)
        r.raise_for_status()
        return r.text

    # 2. The cycle for /write + /delete
    step("Write a.txt v1")
    write("a.txt", "version one\n")

    step("Overwrite a.txt with v2")
    write("a.txt", "version two\n")

    step("Overwrite a.txt with v3")
    write("a.txt", "version three\n")

    step("Delete a.txt via /delete")
    delete("a.txt")

    step("Write a.txt v4 (recreate after delete)")
    write("a.txt", "version four after recreation\n")

    # 3. The cycle for /exec rm
    step("Write b.txt v1")
    write("b.txt", "alpha\n")

    step("Overwrite b.txt v2 via /exec echo >")
    execute("echo bravo > workspace/b.txt")

    step("Delete b.txt via /exec rm")
    execute("rm workspace/b.txt")

    # 4. Wait for S3 Files write-back to flush
    step("Waiting 90s for S3 Files write-back…")
    time.sleep(90)

    # 5. List versions
    step(f"S3 versions under {s3_prefix}a.txt")
    show_versions(s3_prefix + "a.txt")

    step(f"S3 versions under {s3_prefix}b.txt")
    show_versions(s3_prefix + "b.txt")

    # 6. List ALL versions in the namespace prefix (catch any noise)
    step(f"All versions under {s3_prefix}")
    all_v = list_versions(s3_prefix)
    print(f"  total entries: {len(all_v)}")
    keys = sorted({v["key"] for v in all_v})
    print(f"  unique keys ({len(keys)}):")
    for k in keys:
        n = sum(1 for v in all_v if v["key"] == k)
        print(f"    {k} → {n} versions/markers")

    print("\n" + "═" * 60)
    print("  Heuristic: a.txt should have 4 versions + 1 delete-marker = 5 entries")
    print("             b.txt should have 2 versions + 1 delete-marker = 3 entries")
    print("             Anything more = noise from S3 Files internals")
    print("═" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
