from dataclasses import dataclass


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
