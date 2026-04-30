import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { TroveAdminClient, TroveError } from '../dist/index.js'

const BASE = 'https://api.trovefiles.dev'
const WS_ID = 'ws-abc123'

function mockFetch(handler) {
  return mock.method(globalThis, 'fetch', handler)
}

test('listKeys returns array of KeyMetadata', async () => {
  const m = mockFetch(async () =>
    Response.json({ keys: [
      { key_id: 'key-1', name: 'prod', prefix: 'trove-sk-aaa', scope: 'workspace', namespace: null, created_at: '2026-01-01T00:00:00Z' },
      { key_id: 'key-2', name: 'alice', prefix: 'trove-sk-bbb', scope: 'workspace', namespace: 'alice', created_at: '2026-01-02T00:00:00Z' },
    ]})
  )
  const admin = new TroveAdminClient('trove-sk-admin', WS_ID, { baseUrl: BASE })
  const keys = await admin.listKeys()
  assert.equal(keys.length, 2)
  assert.equal(keys[1].namespace, 'alice')
  m.mock.restore()
})

test('createKey returns KeyCreated with api_key', async () => {
  const m = mockFetch(async () =>
    Response.json({
      key_id: 'key-new', name: 'bob', prefix: 'trove-sk-ccc',
      scope: 'workspace', namespace: 'bob', created_at: '2026-01-03T00:00:00Z',
      api_key: 'trove-sk-cccfull',
    }, { status: 201 })
  )
  const admin = new TroveAdminClient('trove-sk-admin', WS_ID, { baseUrl: BASE })
  const key = await admin.createKey('bob', { namespace: 'bob' })
  assert.equal(key.api_key, 'trove-sk-cccfull')
  assert.equal(key.namespace, 'bob')
  m.mock.restore()
})

test('revokeKey resolves on 200', async () => {
  const m = mockFetch(async () =>
    Response.json({ key_id: 'key-1', revoked: true })
  )
  const admin = new TroveAdminClient('trove-sk-admin', WS_ID, { baseUrl: BASE })
  await assert.doesNotReject(() => admin.revokeKey('key-1'))
  m.mock.restore()
})

test('raises TroveError on 403', async () => {
  const m = mockFetch(async () =>
    Response.json({ detail: 'Workspace keys cannot manage keys.' }, { status: 403 })
  )
  const admin = new TroveAdminClient('trove-sk-ws-key', WS_ID, { baseUrl: BASE })
  await assert.rejects(() => admin.createKey('fail'), (err) => {
    assert(err instanceof TroveError)
    assert.equal(err.statusCode, 403)
    return true
  })
  m.mock.restore()
})
