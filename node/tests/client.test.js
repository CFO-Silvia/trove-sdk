import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { TroveClient, TroveError } from '../dist/index.js'

const BASE = 'https://api.trovefiles.dev'

function mockFetch(handler) {
  return mock.method(globalThis, 'fetch', handler)
}

test('exec returns text', async () => {
  const m = mockFetch(async () => new Response('hello\n', { status: 200 }))
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  assert.equal(await client.exec('echo hello'), 'hello\n')
  m.mock.restore()
})

test('exec throws TroveError on 401', async () => {
  const m = mockFetch(async () =>
    Response.json({ detail: 'Invalid API key' }, { status: 401 })
  )
  const client = new TroveClient('trove-sk-bad', 'ns', { baseUrl: BASE })
  await assert.rejects(() => client.exec('echo hi'), (err) => {
    assert(err instanceof TroveError)
    assert.equal(err.statusCode, 401)
    return true
  })
  m.mock.restore()
})

test('write strips workspace/ prefix', async () => {
  let capturedBody
  const m = mockFetch(async (url, init) => {
    capturedBody = JSON.parse(init.body)
    return Response.json({ path: 'workspace/hello.txt', size_bytes: 5 })
  })
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  await client.write('workspace/hello.txt', 'hello')
  assert.equal(capturedBody.path, 'hello.txt')
  m.mock.restore()
})

test('write plain path unchanged', async () => {
  let capturedBody
  const m = mockFetch(async (url, init) => {
    capturedBody = JSON.parse(init.body)
    return Response.json({ path: 'workspace/data.json', size_bytes: 2 })
  })
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  await client.write('data.json', '{}')
  assert.equal(capturedBody.path, 'data.json')
  m.mock.restore()
})

test('delete returns deleted path', async () => {
  const m = mockFetch(async () =>
    Response.json({ deleted: 'workspace/hello.txt' })
  )
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  const result = await client.delete('workspace/hello.txt')
  assert.equal(result, 'workspace/hello.txt')
  m.mock.restore()
})

test('createSnapshot posts label and returns Snapshot', async () => {
  let capturedBody, capturedUrl, capturedMethod
  const m = mockFetch(async (url, init) => {
    capturedUrl = url
    capturedMethod = init.method
    capturedBody = JSON.parse(init.body)
    return Response.json({
      snapshot_id: 'snap-abc123',
      namespace: 'ns',
      label: 'before-restore',
      size_bytes: 12345,
      created_at: '2026-04-30T00:00:00Z',
    })
  })
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  const snap = await client.createSnapshot('before-restore')
  assert.equal(capturedUrl, `${BASE}/v1/snapshots`)
  assert.equal(capturedMethod, 'POST')
  assert.deepEqual(capturedBody, { label: 'before-restore' })
  assert.equal(snap.snapshot_id, 'snap-abc123')
  assert.equal(snap.label, 'before-restore')
  m.mock.restore()
})

test('createSnapshot omits label as null when not provided', async () => {
  let capturedBody
  const m = mockFetch(async (url, init) => {
    capturedBody = JSON.parse(init.body)
    return Response.json({
      snapshot_id: 'snap-x', namespace: 'ns', label: null,
      size_bytes: 0, created_at: '2026-04-30T00:00:00Z',
    })
  })
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  await client.createSnapshot()
  assert.deepEqual(capturedBody, { label: null })
  m.mock.restore()
})

test('listSnapshots unwraps {snapshots: [...]}', async () => {
  const m = mockFetch(async () =>
    Response.json({ snapshots: [
      { snapshot_id: 'snap-1', namespace: 'ns', label: null, size_bytes: 1, created_at: 'x' },
      { snapshot_id: 'snap-2', namespace: 'ns', label: 'tag', size_bytes: 2, created_at: 'y' },
    ] })
  )
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  const list = await client.listSnapshots()
  assert.equal(list.length, 2)
  assert.equal(list[1].label, 'tag')
  m.mock.restore()
})

test('listSnapshots returns [] when key missing', async () => {
  const m = mockFetch(async () => Response.json({}))
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  assert.deepEqual(await client.listSnapshots(), [])
  m.mock.restore()
})

test('restoreSnapshot returns files_restored', async () => {
  let capturedUrl, capturedMethod
  const m = mockFetch(async (url, init) => {
    capturedUrl = url
    capturedMethod = init.method
    return Response.json({ snapshot_id: 'snap-1', restored: true, files_restored: 42 })
  })
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  const n = await client.restoreSnapshot('snap-1')
  assert.equal(capturedUrl, `${BASE}/v1/snapshots/snap-1/restore`)
  assert.equal(capturedMethod, 'POST')
  assert.equal(n, 42)
  m.mock.restore()
})

test('deleteSnapshot resolves on 200', async () => {
  let capturedUrl, capturedMethod
  const m = mockFetch(async (url, init) => {
    capturedUrl = url
    capturedMethod = init.method
    return new Response(null, { status: 204 })
  })
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  await client.deleteSnapshot('snap-zzz')
  assert.equal(capturedUrl, `${BASE}/v1/snapshots/snap-zzz`)
  assert.equal(capturedMethod, 'DELETE')
  m.mock.restore()
})

test('snapshot methods raise TroveError on non-2xx', async () => {
  const m = mockFetch(async () =>
    Response.json({ detail: 'snapshot not found' }, { status: 404 })
  )
  const client = new TroveClient('trove-sk-test', 'ns', { baseUrl: BASE })
  await assert.rejects(() => client.restoreSnapshot('snap-missing'), (err) => {
    assert(err instanceof TroveError)
    assert.equal(err.statusCode, 404)
    return true
  })
  m.mock.restore()
})
