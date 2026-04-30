"""Minimal Flask webhook receiver.

    pip install trove-sdk flask
    export TROVE_WEBHOOK_SECRET=trove-whsec-...
    flask --app examples/receiver_flask run --port 8080

Then point a Trove webhook at http://<your-host>:8080/trove/events.
"""
import os

from flask import Flask, abort, request

from trove_sdk import WebhookSignatureError, verify_webhook

SECRET = os.environ["TROVE_WEBHOOK_SECRET"]
app = Flask(__name__)


@app.post("/trove/events")
def receive():
    try:
        event = verify_webhook(
            secret=SECRET,
            body=request.get_data(),  # raw bytes — JSON re-serialization breaks the signature
            signature_header=request.headers.get("X-Trove-Signature", ""),
        )
    except WebhookSignatureError as e:
        app.logger.warning("rejected webhook: %s", e)
        abort(400)

    app.logger.info("event %s (%s) on %s", event.type, event.id, event.workspace_id)

    if event.type == "file.written":
        app.logger.info("  → wrote %s (%s bytes)", event.data["path"], event.data["size_bytes"])
    elif event.type == "file.deleted":
        app.logger.info("  → deleted %s", event.data["path"])
    elif event.type == "exec.completed":
        app.logger.info("  → ran %r (exit %s)", event.data["command"], event.data["exit_code"])

    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
