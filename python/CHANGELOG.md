# Changelog

All notable changes to the `trove-sdk` Python package.

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
