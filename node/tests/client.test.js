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
