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
class ExecResult:
    """Structured result of `exec_detailed` / `POST /v1/exec`.

    `stdout` and `stderr` are kept separate (the legacy text endpoint
    interleaves them on non-zero exits). `duration_ms` is wall-clock time
    measured server-side — useful for spotting slow commands without
    re-running them locally.
    """
    exit_code:   int
    stdout:      str
    stderr:      str
    duration_ms: int


@dataclass
class FileInfo:
    """One entry returned by `list_dir`."""
    name: str
    path: str
    is_dir: bool
    size_bytes: int | None
    modified_at: str


@dataclass
class FileContent:
    """Result of `read_text` / `read_bytes`."""
    path: str
    size_bytes: int
    modified_at: str
    encoding: str            # "utf-8" or "binary"
    content: str | None      # None when encoding == "binary"
    truncated: bool          # True when file exceeds the 1 MB preview cap


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
