# Changelog

All notable changes to the `trove-sdk` Python package.

## 0.8.0 — 2026-05-07

### Fixed — `exec_chain` now actually persists `cwd`/`export`/vars

- 0.7.6 wrapped each step in `( ... )`, which spawns a subshell — so the
  documented "cwd and variables hold for the whole chain" guarantee was
  silently false. The README's flagship example (`cd workspace/data`,
  capture `TOKEN`, `curl -H "Authorization: $TOKEN"`) shipped an empty
  `TOKEN` and wrote outputs to the wrong directory.
- Switched to brace groups `{ ... ; }` — same parse-boundary safety against
  internal `&&` / `||` in a step, but no subshell, so state actually
  persists. Verified end-to-end against the live API: `cd`, shell vars,
  and `export` all carry through every step of the chain.
- Existing unit tests asserted the buggy `( ... )` shape and so passed
  while production failed; updated them, and added an integration-style
  test that runs the joined string through a real bash so a future
  regression to subshells gets caught by execution rather than string
  comparison.

### Changed — `exec()` no longer returns a confused error blob

`exec()` is now a thin convenience wrapper around `exec_detailed()`:

- **Success**: returns `stdout` as a `str` (unchanged for happy-path callers).
- **Non-zero exit**: raises a new `TroveExecError(command, exit_code, stdout, stderr)`
  instead of silently returning the legacy `[exit N]\n<stderr>\n<stdout>` text
  blob. That blob was an easy footgun — feeding `client.exec(...)` straight to a
  model meant the model saw a string of "normal output" with the failure mixed in.
- **Implementation**: routes through `POST /v1/exec` (the structured endpoint),
  same as `exec_detailed`. Both endpoints continue to work server-side; the SDK
  just stops surfacing the legacy text format.

`TroveExecError` subclasses `TroveError`, so existing `except TroveError:`
blocks keep catching shell failures. Its `status_code` is `None` (this is a
shell exit, not an HTTP failure) — read `exit_code` instead.

**Migration**: any caller that was inspecting the `[exit N]…` text on failure
needs to switch to `try/except TroveExecError` (recommended) or call
`exec_detailed()` and read `result.exit_code` directly. Calls that only ever
read `stdout` on the success path are unaffected.

## 0.7.6 — 2026-05-07

### Added — `exec_chain(commands)`

- **`exec_chain([cmd, cmd, ...])`** on `TroveClient` and `AsyncTroveClient`.
  Joins the commands with `&&` server-side and runs them as one exec, so
  `cd`, `export`, and shell variables hold for the whole chain. Returns
  `ExecResult`. Use it when a multi-step flow needs to share state — a
  separate `exec()` call gets a fresh shell, so anything outside the
  filesystem is gone.
- Each command is wrapped in `( ... )` so an internal `&&` / `||` doesn't
  swallow the join. Empty / whitespace-only entries raise `ValueError`.
- The 30-second wall-clock timeout still applies to the chain as a whole.
  For longer flows, write progress to files so a retry can resume.

### Changed — Docs

- New "What persists between exec calls" section in the README + dashboard
  docs makes the per-call shell model explicit: the filesystem persists,
  shell state doesn't. Three rules of thumb (deterministic setup → `init.sh`,
  cross-call computed state → file, multi-step shared state → `exec_chain`).
- Corrected `read(path)` docstring — was claiming "one round-trip" universally,
  but binary files take two trips (encoding detection + content fetch).
  One trip for text, two for binary.

## 0.7.5 — 2026-05-07

### Added — Browser-based `trove login`

- **`trove login`** now defaults to a device-code flow: prints a short
  user code, opens the dashboard, and collects a freshly-minted API key
  once the user approves in the browser. No more "go copy a key from the
  dashboard, paste it back" two-step.
- **Backwards compatible.** `--api-key` still skips the browser. Piping a
  key on stdin (`echo $KEY | trove login`) does too — handy for CI. Add
  `--no-browser` to force the old paste prompt on a TTY.
- Requires server v0.2.0+ (older servers fall back to the paste flow with
  a clear hint).

### Added — Error subclasses

- **`TroveAuthError` (401/403), `TroveNotFoundError` (404),
  `TroveTimeoutError` (408/504), `TroveRateLimitError` (429),
  `TroveServerError` (5xx)** — all subclass `TroveError`, so existing
  `except TroveError:` blocks keep working. Lets retry/recovery logic
  match on the class instead of `status_code` integers.

### Added — `TroveAdminClient.from_api_key`

- Classmethod on `TroveAdminClient` and `AsyncTroveAdminClient` that
  discovers `workspace_id` from `/v1/me`, so the multi-tenant quickstart
  needs one secret instead of two.

### Added — Truncation visibility

- **`list_dir` returns `ListResult`** — a `list[FileInfo]` subclass with a
  `.truncated` attribute. Iteration / indexing / `len()` unchanged; now
  callers can detect when the server's 1000-entry / 20-level cap was hit.
- **`read_bytes_full(path)`** — same network call as `read_bytes` but
  returns `BytesContent(content, truncated, size_bytes)` so callers can
  see when the 100 MB response cap was hit (reads `X-Trove-Truncated` /
  `X-Trove-Size` headers the SDK previously ignored).

### Added — `client.read(path)`

- Returns `str` for UTF-8 files, `bytes` for binary, in one round-trip.
  Saves the `read_text` → 415 → `read_bytes` retry pattern, especially
  behind the MCP `trove_read` tool.

### Added — MCP `trove_put_base64`

- Fourth MCP tool. Lets the model drop binaries (PDFs, images, audio)
  without the `trove_write` + `trove_exec | base64 -d` round-trip and
  shell-quoting hazard.

## 0.7.4 — 2026-05-06

### Added — Persistent shell context

- **`set_init(text)` / `get_init()` / `clear_init()`** on both `TroveClient`
  and `AsyncTroveClient`. Writes a script to `workspace/.trove/init.sh`
  that the `/exec` endpoint sources before every command — so `cd`,
  `export`, activated venvs, and shell functions carry across calls (and
  across agent process restarts, because it lives in the namespace volume).
  Replaces the "prefix every command with `cd ... && source ... && ...`"
  pattern. Requires a server that honors the convention; older servers
  store the file but won't source it.

## 0.7.3 — 2026-05-06

### Added — MCP server

- **`pip install 'trove-sdk[mcp]'`** ships an MCP server that exposes Trove
  to any MCP-compatible AI client (Claude Desktop, Cursor, Claude Code).
  The server runs locally on the user's machine over stdio; configuration
  arrives via `TROVE_API_KEY` / `TROVE_NAMESPACE` env vars baked into the
  client's config block.
- **Three tools**, deliberately tight to keep model performance up:
  - `trove_exec(command, stdin?)` — every preinstalled Unix tool through
    one entry point (`jq`, `awk`, `pdftotext`, `ffmpeg`, `python3`, …).
  - `trove_read(path)` — UTF-8 text read.
  - `trove_write(path, content)` — UTF-8 text write.

### Added — CLI

- **`trove mcp install`** auto-detects Claude Desktop and Cursor and writes
  a server entry into each one's config file. Project-scoped Claude Code
  via `--client claude-code` (writes `./.mcp.json` in cwd). Existing
  servers in the same config are preserved — atomic write through a
  sibling tempfile so a crash mid-write doesn't corrupt the user's
  setup.
- **`trove mcp uninstall`** removes the entry without touching anything else.
- **`trove mcp status`** shows which clients have it wired up and which
  namespace each is pointed at.

The `[mcp]` extra is optional — base `pip install trove-sdk` stays slim.
The CLI subcommand probes for the `mcp` package up front and surfaces a
clean install hint instead of a raw `ImportError`.

## 0.7.2 — 2026-05-06

### Changed

- **README** — promote the three-key multi-tenant pattern (admin / scoped
  runtime / unscoped runtime) to the top of the README so PyPI visitors see
  it before the API reference. No code changes.

## 0.7.1 — 2026-05-06

### Changed

- **Package description and README** — reframed positioning from "managed
  POSIX filesystem" to "files and commands for AI agents" so the PyPI page
  matches the website hero. No code changes.

## 0.7.0 — 2026-05-06

### Added — SDK

- **`exec` / `exec_detailed` accept a `stdin` keyword.** Pipe a UTF-8 payload
  to the spawned shell:

  ```python
  client.exec_detailed("jq .field", stdin=json.dumps(payload))
  ```

  None == `/dev/null` (preserves prior behavior). Server caps the payload at
  1 MB; for binary input, `upload` then redirect from disk
  (`"tool < workspace/in.bin"`). Mirrored on `AsyncTroveClient`.

### Added — CLI

- **`trove run` auto-forwards piped stdin.** When `sys.stdin` isn't a tty
  the CLI reads it and passes it to the remote shell:

  ```bash
  echo '{"x":1}' | trove run "jq .x"
  cat report.txt | trove run "wc -l"
  ```

  Pass `--no-stdin` to opt out (CI runners that lie about `isatty`). 1 MB
  cap, errors out clean above that.

### Fixed — CLI

- **`trove doctor` no longer crashes on Windows** with `UnicodeEncodeError`
  on the ✓/⚠ glyphs. Reconfigured stdout/stderr to UTF-8 at startup; fixes
  every command, not just `doctor`.

### Server (deployed alongside)

- **`POST /v1/exec` (and legacy `/exec`)** accept an optional `stdin` field.
  Old clients that never set it see no behavior change. New clients should
  probe `GET /v1/me`'s `capabilities` array for `"exec.stdin"` before
  relying on it; the SDK currently always sends it and lets the server
  ignore it on older deployments.
- **`exec.completed` webhook** carries `stdin_bytes` (count only, never the
  content).

The SDK + CLI degrade gracefully against an older API: stdin is silently
dropped if the server hasn't shipped this version yet — same behavior as
before this release.

## 0.6.0 — 2026-05-06

### Added — SDK

- **`TroveClient.exec_detailed(command) -> ExecResult`** for agent loops that
  need clean stdout/stderr separation and an exit code without parsing the
  legacy `[exit N]\nstderr\nstdout` text. Hits the new `POST /v1/exec`
  endpoint; falls back gracefully on the dashboard side. The existing
  `exec(command) -> str` method is preserved for backwards compatibility.
- **`TroveClient.read_bytes(path) -> bytes`** — binary-safe download of any
  file `read_text` would refuse (images, PDFs, audio, archives). Closes the
  symmetry gap with `upload`. Server cap is 100 MB; oversized responses come
  back truncated with the full size in the `X-Trove-Size` header.
- **`TroveClient.read_text` / `read_file` / `list_dir`** are now part of the
  documented surface. They were available against `/v1/files` but not exposed
  on the client.
- **`ExecResult`, `FileInfo`, `FileContent`** dataclasses re-exported from
  the package root.
- **`AsyncTroveClient`** mirrors all of the above.

### Added — CLI

- **`trove run` propagates the remote exit code** as the local exit code, so
  `trove run "build" && trove run "deploy"` finally works the way you'd
  expect. Previously every call returned 0 with `[exit N]` stuffed into
  stdout. Falls back to parsing the legacy `[exit N]` prefix when the server
  hasn't shipped `/v1/exec` yet.
- **Remote stderr now goes to local stderr** (was silently dropped on
  success and interleaved into stdout on failure).
- **`trove run --json`** emits a single JSON line
  `{exit_code, stdout, stderr, duration_ms}` for piping into `jq`.
- **`trove get REMOTE [LOCAL]`** — binary-safe download. Mirrors `trove put`.
  Supports `--stdout`, `--force`, and warns on truncation.
- **`trove doctor`** — one-shot diagnostic: CLI version, binary path, config
  permissions, profiles, env-var overrides, and a live `/v1/me` ping.
  Designed to be the first thing a user runs when something feels off.
- **`trove login --save-as NAME`** replaces the colliding `--profile NAME`
  flag. The old flag still works with a deprecation warning. The 0.5.x
  footgun — `trove --profile staging login ...` silently saving to
  `default` — now uses the root `--profile` as the save target with a
  one-line note.

### Server (deployed alongside)

- **`POST /v1/exec`** returns `{exit_code, stdout, stderr, duration_ms}` as
  JSON. The legacy `POST /exec` keeps its text response for backwards compat.
- **`GET /files/{path}`** symmetric of the existing `PUT /files/{path}`.
  Streams raw bytes, capped at `MAX_UPLOAD_BYTES` (100 MB) to match upload.
  Sends `X-Trove-Size` always and `X-Trove-Truncated: 1` when the cap was
  hit.
- **`GET /v1/snapshots`** now populates the `label` field. Was always
  returning `null` because the implementation skipped `head_object`; we now
  fan those calls out across a small thread pool, capped at 200 snapshots
  per response.

The SDK + CLI degrade gracefully against an older API: `exec_detailed`
falls back to text parsing on `/v1/exec` 404, `read_bytes` errors with the
server's response, and snapshot list labels stay `None`.

## 0.5.1 — 2026-05-06

### Fixed

- **`trove run` no longer eats flag-shaped arguments.** In 0.5.0 a command like
  `trove run wc -c workspace/file.txt` would error with `No such option: -c`
  because click's option parser intercepted the `-c` instead of passing it
  to the workspace shell. Affected anything with short flags (`ls -la`,
  `grep -i`, `awk -F,`, `cat -n`, ...). Fixed by switching `run` to
  `ignore_unknown_options=True` + `allow_interspersed_args=False`. The
  `-n/--namespace` option still works when placed before the command.

## 0.5.0 — 2026-05-06

### Added — CLI

The `trove` command now mirrors the SDK surface. Install with
`pip install 'trove-sdk[cli]'`.

- **Filesystem** — `trove run "<cmd>"`, `trove ls`, `trove cat`, `trove put`,
  `trove write`, `trove rm`. `run` closes the loop with `tail`: push a command,
  watch its event land in the activity feed.
- **Key management** — `trove keys list / create / revoke` (admin scope).
- **Webhooks** — `trove webhooks list / create / delete / test` (admin scope).
- **Snapshots** — `trove snapshot create / list / restore / delete`.
- **`trove login`** discovers the workspace ID (and namespace lock) by calling
  `/v1/me`, so users only need to paste one secret. `--workspace` still works
  if the user wants to be explicit; `--namespace` bakes a default into the
  saved profile.
- **`trove whoami`** now shows the active key's scope and namespace lock so
  customer-scoped keys can't accidentally be pointed at the wrong namespace.
- **`TROVE_NAMESPACE` env var** — picked up alongside `TROVE_API_KEY` /
  `TROVE_WORKSPACE_ID`. Per-command `-n/--namespace` overrides both.
- **Friendly hint** when the `[cli]` extras are missing — `trove --help` no
  longer dies with a raw `ImportError: click`.

### Improved — events

- **`events list` shares `--since` with `tail`** and adds `--cursor` for
  pagination + `(no events)` on empty results.
- **`--since` accepts compound durations and ISO timestamps** in addition to
  single-unit relative durations: `now`, `5m`, `1h30m`, `2d4h15m`,
  `2024-12-01T00:00:00Z`, `2024-12-01`.
- **Local time** rendering for event timestamps. Events from earlier days
  get an `MM-DD ` prefix so the log doesn't look stuck in a single day.
  `--json` mode preserves the raw ISO string.
- **`tail --idle 30`** prints a heartbeat to stderr after a quiet period so
  you know the poll is still alive.
- **`-v / --verbose`** expands truncated `exec.completed` summaries to the
  full command + first stdout line.

### Server

These features rely on a corresponding API change (deployed alongside the
release):

- **`GET /v1/me`** — returns `{workspace_id, key_id, key_name, scope, namespace}`
  for the bearer key. Powers `trove login` discovery and the `whoami` upgrade.
- **`snapshot.deleted` event** — now dispatched on snapshot delete from both
  runtime and management endpoints. Previously `snapshot.created` and
  `snapshot.restored` were emitted but `snapshot.deleted` was invisible.

The SDK + CLI degrade gracefully against an older API: `trove login` falls
back to prompting for the workspace ID, and `snapshot.deleted` simply
doesn't appear in `tail`.

## 0.4.0 — Earlier release

Snapshot support added to `TroveClient`.

## 0.3.0 — Earlier release

Webhook support: admin methods + signature verifier.

## 0.2.0 — Earlier release

Initial public release with `TroveClient`, `TroveAdminClient`,
`AsyncTroveClient`, `AsyncTroveAdminClient`.
