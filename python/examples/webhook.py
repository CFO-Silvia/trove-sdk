"""End-to-end webhook example.

Registers a webhook against your workspace, triggers a `file.written` event
by writing a file, and verifies the signature on the delivered payload.

Set three environment variables before running:

    TROVE_ADMIN_KEY     # Admin key — needed to manage webhooks
    TROVE_API_KEY       # Workspace key — used to write the file
    TROVE_WORKSPACE_ID  # ws-... shown on the dashboard

Then:

    pip install trove-sdk httpx
    python examples/webhook.py
"""
from __future__ import annotations

import os
import sys
import time

import httpx

from trove_sdk import (
    TroveAdminClient,
    TroveClient,
    WebhookSignatureError,
    verify_webhook,
)


def required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"Missing environment variable: {name}")
    return val


def main() -> int:
    admin_key    = required("TROVE_ADMIN_KEY")
    workspace_key = required("TROVE_API_KEY")
    workspace_id = required("TROVE_WORKSPACE_ID")
    namespace    = "webhook-example"

    # 1. Spin up a public listener on webhook.site so we can see the delivery.
    print("→ Creating webhook.site listener…")
    token = httpx.post("https://webhook.site/token", timeout=15).json()
    listener_url   = f"https://webhook.site/{token['uuid']}"
    listener_query = f"https://webhook.site/token/{token['uuid']}/requests?sorting=newest"
    print(f"  URL  : {listener_url}")
    print(f"  View : {listener_url}")

    # 2. Register the webhook with the admin client.
    print("\n→ Registering webhook…")
    admin = TroveAdminClient(admin_key, workspace_id)
    created = admin.create_webhook(
        listener_url,
        events=["file.written", "exec.completed"],
        namespace=namespace,
        description="Trove webhook example",
    )
    print(f"  webhook_id = {created.webhook_id}")
    print(f"  events     = {created.events}")
    secret = created.signing_secret  # save this — it isn't shown again

    # 3. Trigger a file.written event with the workspace key.
    print("\n→ Writing a file to fire `file.written`…")
    client = TroveClient(api_key=workspace_key, namespace=namespace)
    res = client.write("workspace/hello.txt", "hello from the webhook example")
    print(f"  wrote {res.path} ({res.size_bytes} bytes)")

    # 4. Poll the listener for the delivery.
    print("\n→ Waiting for delivery…")
    received: dict | None = None
    for attempt in range(20):
        time.sleep(1)
        body = httpx.get(listener_query, timeout=15).json()
        if body.get("data"):
            received = body["data"][0]
            print(f"  ✓ Delivered after {attempt + 1}s")
            break
    if not received:
        print("  ✗ No delivery within 20s")
        admin.delete_webhook(created.webhook_id)
        return 1

    # webhook.site returns headers as {name: [value]} — flatten.
    headers = {k: v[0] if isinstance(v, list) else v
               for k, v in received.get("headers", {}).items()}
    print(f"  X-Trove-Event     : {headers.get('x-trove-event')}")
    print(f"  X-Trove-Signature : {headers.get('x-trove-signature', '')[:48]}…")

    # 5. Verify the signature using the SDK helper.
    print("\n→ Verifying signature…")
    try:
        event = verify_webhook(
            secret=secret,
            body=received["content"],
            signature_header=headers["x-trove-signature"],
        )
    except WebhookSignatureError as e:
        print(f"  ✗ {e}")
        admin.delete_webhook(created.webhook_id)
        return 1

    print(f"  ✓ Signature valid")
    print(f"    event.id   = {event.id}")
    print(f"    event.type = {event.type}")
    print(f"    event.data = {event.data}")

    # 6. Tampered body should be rejected.
    print("\n→ Confirming tampered body is rejected…")
    tampered = received["content"].replace("hello", "xxxxx", 1)
    try:
        verify_webhook(
            secret=secret,
            body=tampered,
            signature_header=headers["x-trove-signature"],
        )
        print("  ✗ Verifier accepted tampered body!")
        admin.delete_webhook(created.webhook_id)
        return 1
    except WebhookSignatureError as e:
        print(f"  ✓ Rejected: {e}")

    # 7. Cleanup.
    print("\n→ Cleanup…")
    try:
        client.delete("workspace/hello.txt")
    except Exception:
        pass
    admin.delete_webhook(created.webhook_id)
    admin.close()
    client.close()
    print("  done.")

    print("\n✓ End-to-end webhook example PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
