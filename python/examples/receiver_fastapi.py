"""Minimal FastAPI webhook receiver.

    pip install trove-sdk fastapi 'uvicorn[standard]'
    export TROVE_WEBHOOK_SECRET=trove-whsec-...
    uvicorn examples.receiver_fastapi:app --port 8080

Then point a Trove webhook at http://<your-host>:8080/trove/events.
"""
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request

from trove_sdk import WebhookSignatureError, verify_webhook

SECRET = os.environ["TROVE_WEBHOOK_SECRET"]

app = FastAPI()
log = logging.getLogger("trove-receiver")
logging.basicConfig(level=logging.INFO)


@app.post("/trove/events", status_code=204)
async def receive(
    request: Request,
    x_trove_signature: str = Header(..., alias="X-Trove-Signature"),
):
    body = await request.body()  # raw bytes — DO NOT use request.json()
    try:
        event = verify_webhook(
            secret=SECRET,
            body=body,
            signature_header=x_trove_signature,
        )
    except WebhookSignatureError as e:
        log.warning("rejected webhook: %s", e)
        raise HTTPException(status_code=400, detail="invalid signature")

    log.info("event %s (%s) on %s", event.type, event.id, event.workspace_id)

    if event.type == "file.written":
        log.info("  -> wrote %s (%s bytes)", event.data["path"], event.data["size_bytes"])
    elif event.type == "file.deleted":
        log.info("  -> deleted %s", event.data["path"])
    elif event.type == "exec.completed":
        log.info("  -> ran %r (exit %s)", event.data["command"], event.data["exit_code"])
