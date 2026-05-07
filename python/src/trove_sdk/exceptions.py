from __future__ import annotations

import httpx


class TroveError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TroveAuthError(TroveError):
    """401 / 403 — bad or revoked key, or scope/namespace not allowed."""


class TroveNotFoundError(TroveError):
    """404 — file, namespace, key, or webhook not found."""


class TroveTimeoutError(TroveError):
    """408 / 504 — server-side exec or upstream timeout."""


class TroveRateLimitError(TroveError):
    """429 — too many requests. Back off and retry."""


class TroveServerError(TroveError):
    """5xx — generic server-side failure."""


_STATUS_TO_CLASS: dict[int, type[TroveError]] = {
    401: TroveAuthError,
    403: TroveAuthError,
    404: TroveNotFoundError,
    408: TroveTimeoutError,
    429: TroveRateLimitError,
    504: TroveTimeoutError,
}


def raise_for_response(response: httpx.Response) -> None:
    """Raise the most specific `TroveError` subclass for a non-2xx response.

    Existing callers using `except TroveError` keep working; new code can
    catch (e.g.) `TroveRateLimitError` directly without scraping
    `status_code` integers.
    """
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text
    cls = _STATUS_TO_CLASS.get(response.status_code)
    if cls is None and response.status_code >= 500:
        cls = TroveServerError
    cls = cls or TroveError
    raise cls(detail, status_code=response.status_code)
