from dataclasses import dataclass, field
from typing import Any


@dataclass
class KeyMetadata:
    key_id: str
    name: str
    prefix: str
    scope: str
    namespace: str | None
    created_at: str


@dataclass
class KeyCreated(KeyMetadata):
    api_key: str


@dataclass
class FileResult:
    path: str
    size_bytes: int


@dataclass
class WebhookMetadata:
    webhook_id: str
    url: str
    events: list[str]
    namespace: str | None
    description: str | None
    enabled: bool
    created_at: str


@dataclass
class WebhookCreated(WebhookMetadata):
    signing_secret: str


@dataclass
class WebhookTestResult:
    ok: bool
    event_id: str
    status: int | None = None
    error: str | None = None


@dataclass
class WebhookEvent:
    """A signed event delivered to a subscribed URL.

    Returned by `verify_webhook` after the signature and timestamp are validated.
    """
    id: str
    type: str
    api_version: str
    workspace_id: str
    namespace: str | None
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    snapshot_id: str
    namespace: str
    label: str | None
    size_bytes: int
    created_at: str
