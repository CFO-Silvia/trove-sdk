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


class TroveExecError(TroveError):
    """A shell command ran but exited non-zero.

    Distinct from HTTP errors (the request itself succeeded). Holds the
    captured `stdout` and `stderr` so callers can inspect or re-raise with
    context. The `status_code` field is `None` — use `exit_code` for the
    shell exit instead.
    """

    def __init__(self, command: str, exit_code: int, stdout: str, stderr: str):
        snippet = (stderr.strip() or stdout.strip() or "<no output>")
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        super().__init__(
            f"command failed (exit {exit_code}): {snippet}",
            status_code=None,
        )
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


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
