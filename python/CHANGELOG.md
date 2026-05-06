# Changelog

All notable changes to the `trove-sdk` Python package.

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
