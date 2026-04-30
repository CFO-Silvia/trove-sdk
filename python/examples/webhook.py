"""Subscribe to Trove webhooks and verify deliveries.

Run once to subscribe:

    TROVE_ADMIN_KEY=trove-sk-admin-... \
    TROVE_WORKSPACE_ID=ws-... \
        python examples/webhook.py

Save the printed signing secret. Then call `receive()` from your HTTP handler
to validate each incoming delivery.
"""
import os

from trove_sdk import TroveAdminClient, verify_webhook


def subscribe() -> None:
    """Register a URL to receive `file.written`, `file.deleted`, `exec.completed`."""
    with TroveAdminClient(
        os.environ["TROVE_ADMIN_KEY"],
        os.environ["TROVE_WORKSPACE_ID"],
    ) as admin:
        hook = admin.create_webhook(
            "https://your-app.com/trove/events",
            events=["file.written", "file.deleted", "exec.completed"],
        )
    print(f"webhook_id     : {hook.webhook_id}")
    print(f"signing_secret : {hook.signing_secret}  ← save this, shown once")


def receive(raw_body: bytes, signature_header: str, secret: str) -> None:
    """Validate one delivery. Pass the raw request bytes — not parsed JSON."""
    event = verify_webhook(
        secret=secret,
        body=raw_body,
        signature_header=signature_header,
    )
    print(f"{event.type} on {event.workspace_id}: {event.data}")


if __name__ == "__main__":
    subscribe()
