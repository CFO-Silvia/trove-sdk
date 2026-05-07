# trove-sdk · Node.js

Node.js client for [Trove](https://trovefiles.dev) — files and commands for AI agents. Persistent storage that survives every session, isolated per customer, with real Unix tools (awk, jq, pdftotext, ffmpeg) preinstalled.

## Installation

```bash
npm install trove-sdk
# or
pnpm add trove-sdk
```

Requires Node.js 18+. Zero runtime dependencies.

## Usage

### Filesystem operations

```js
import { TroveClient } from 'trove-sdk'

const client = new TroveClient('trove-sk-...', 'alice')

// Run shell commands
await client.exec('mkdir -p workspace/data')
const output = await client.exec('ls workspace/')

// Write a text file
await client.write('workspace/data/notes.txt', 'hello world')

// Upload binary
const data = await fs.readFile('image.png')
await client.upload('workspace/data/image.png', data)

// Delete
await client.delete('workspace/data/notes.txt')
```

### Key management (multi-tenant)

Use an admin key from the dashboard to mint scoped keys per customer:

```js
import { TroveAdminClient } from 'trove-sdk'

const admin = new TroveAdminClient('trove-sk-admin-...', 'ws-...')

// Mint a scoped key for a customer
const key = await admin.createKey('customer-alice', { namespace: 'alice' })
console.log(key.api_key) // store this — shown once

// List active keys
const keys = await admin.listKeys()

// Revoke
await admin.revokeKey(key.key_id)
```

## API reference

### `new TroveClient(apiKey, namespace, options?)`

| Method | Description |
|--------|-------------|
| `exec(command)` | Run a shell command. Returns stdout as a string. |
| `write(path, content)` | Write a UTF-8 text file. Returns `FileResult`. |
| `upload(path, data)` | Upload a `Uint8Array` or `ArrayBuffer`. Returns `FileResult`. |
| `delete(path)` | Delete a file or directory. Returns the deleted path. |

### `new TroveAdminClient(apiKey, workspaceId, options?)`

| Method | Description |
|--------|-------------|
| `createKey(name, options?)` | Mint a new workspace key, optionally scoped to a namespace. |
| `listKeys()` | List all active keys for the workspace. |
| `revokeKey(keyId)` | Revoke a key immediately. |

### Errors

All errors throw `TroveError` with a `statusCode` property.

```js
import { TroveError } from 'trove-sdk'

try {
  await client.exec('echo hi')
} catch (err) {
  if (err instanceof TroveError) {
    console.error(err.statusCode, err.message)
  }
}
```
