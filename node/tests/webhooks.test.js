import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { createHmac } from 'node:crypto'
import {
  TroveAdminClient,
  verifyWebhook,
  WebhookSignatureError,
} from '../dist/index.js'

const BASE = 'https://api.trovefiles.dev'
const WS_ID = 'ws-abc123'

function mockFetch(handler) {
  return mock.method(globalThis, 'fetch', handler)
}

// ── Admin client ──────────────────────────────────────────────────────────────

test('createWebhook returns WebhookCreated with secret', async () => {
  const m = mockFetch(async () =>
    Response.json({
      webhook_id: 'wh-aaa',
      url: 'https://example.com/hook',
      events: ['file.written'],
      namespace: null,
      description: null,
      enabled: true,
      created_at: '2026-04-30T00:00:00Z',
      signing_secret: 'trove-whsec-deadbeef',
    }, { status: 201 }),
  )
  const admin = new TroveAdminClient('trove-sk-admin', WS_ID, { baseUrl: BASE })
  const wh = await admin.createWebhook('https://example.com/hook', { events: ['file.written'] })
  assert.equal(wh.signing_secret, 'trove-whsec-deadbeef')
  assert.deepEqual(wh.events, ['file.written'])
  m.mock.restore()
})

test('listWebhooks unwraps {webhooks: [...]}', async () => {
  const m = mockFetch(async () =>
    Response.json({ webhooks: [
      { webhook_id: 'wh-1', url: 'https://a', events: ['*'], namespace: null, description: null, enabled: true, created_at: '2026-04-30T00:00:00Z' },
      { webhook_id: 'wh-2', url: 'https://b', events: ['file.written'], namespace: 'alice', description: 'alice prod', enabled: true, created_at: '2026-04-30T01:00:00Z' },
    ]}),
  )
  const admin = new TroveAdminClient('trove-sk-admin', WS_ID, { baseUrl: BASE })
  const hooks = await admin.listWebhooks()
  assert.equal(hooks.length, 2)
  assert.equal(hooks[1].namespace, 'alice')
  m.mock.restore()
})

test('deleteWebhook resolves on 200', async () => {
  const m = mockFetch(async () =>
    Response.json({ webhook_id: 'wh-1', deleted: true }),
  )
  const admin = new TroveAdminClient('trove-sk-admin', WS_ID, { baseUrl: BASE })
  await assert.doesNotReject(() => admin.deleteWebhook('wh-1'))
  m.mock.restore()
})

test('testWebhook returns WebhookTestResult', async () => {
  const m = mockFetch(async () =>
    Response.json({ ok: true, status: 204, event_id: 'evt-zzz' }),
  )
  const admin = new TroveAdminClient('trove-sk-admin', WS_ID, { baseUrl: BASE })
  const result = await admin.testWebhook('wh-1')
  assert.equal(result.ok, true)
  assert.equal(result.status, 204)
  m.mock.restore()
})

// ── Signature verification ────────────────────────────────────────────────────

const SECRET = 'trove-whsec-test'

function sign(secret, ts, body) {
  const digest = createHmac('sha256', secret).update(`${ts}.${body}`).digest('hex')
  return `t=${ts},v1=${digest}`
}

function payload() {
  return JSON.stringify({
    id: 'evt-abc',
    type: 'file.written',
    api_version: '2026-04-30',
    workspace_id: 'ws-abc',
    namespace: 'alice',
    created_at: '2026-04-30T00:00:00Z',
    data: { path: 'workspace/foo.txt', size_bytes: 12, source: 'write' },
  })
}

test('verifyWebhook happy path', () => {
  const body = payload()
  const ts = Math.floor(Date.now() / 1000)
  const event = verifyWebhook({
    secret: SECRET,
    body,
    signatureHeader: sign(SECRET, ts, body),
  })
  assert.equal(event.id, 'evt-abc')
  assert.equal(event.type, 'file.written')
  assert.equal(event.data.path, 'workspace/foo.txt')
})

test('verifyWebhook rejects bad secret', () => {
  const body = payload()
  const ts = Math.floor(Date.now() / 1000)
  const sig = sign('wrong-secret', ts, body)
  assert.throws(
    () => verifyWebhook({ secret: SECRET, body, signatureHeader: sig }),
    (err) => err instanceof WebhookSignatureError && /mismatch/i.test(err.message),
  )
})

test('verifyWebhook rejects stale timestamp', () => {
  const body = payload()
  const ts = Math.floor(Date.now() / 1000) - 1000
  const sig = sign(SECRET, ts, body)
  assert.throws(
    () => verifyWebhook({ secret: SECRET, body, signatureHeader: sig, toleranceSeconds: 300 }),
    (err) => err instanceof WebhookSignatureError && /tolerance/i.test(err.message),
  )
})

test('verifyWebhook rejects tampered body', () => {
  const body = payload()
  const ts = Math.floor(Date.now() / 1000)
  const sig = sign(SECRET, ts, body)
  const tampered = body.replace('foo.txt', 'bar.txt')
  assert.throws(
    () => verifyWebhook({ secret: SECRET, body: tampered, signatureHeader: sig }),
    WebhookSignatureError,
  )
})

test('verifyWebhook rejects malformed header', () => {
  assert.throws(
    () => verifyWebhook({ secret: SECRET, body: payload(), signatureHeader: 'garbage' }),
    WebhookSignatureError,
  )
})

test('verifyWebhook accepts Uint8Array body', () => {
  const body = payload()
  const ts = Math.floor(Date.now() / 1000)
  const sig = sign(SECRET, ts, body)
  const event = verifyWebhook({
    secret: SECRET,
    body: new TextEncoder().encode(body),
    signatureHeader: sig,
  })
  assert.equal(event.id, 'evt-abc')
})
