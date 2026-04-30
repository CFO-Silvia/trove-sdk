import { createHmac, timingSafeEqual } from 'node:crypto'
import { TroveError } from './error.js'
import type { WebhookEvent } from './types.js'

const DEFAULT_TOLERANCE_SECONDS = 300

export class WebhookSignatureError extends TroveError {
  constructor(message: string) {
    super(message, 400)
    this.name = 'WebhookSignatureError'
  }
}

function parseHeader(header: string): { timestamp: number; v1: string } {
  const parts: Record<string, string> = {}
  for (const chunk of header.split(',')) {
    const trimmed = chunk.trim()
    const eq = trimmed.indexOf('=')
    if (eq < 0) continue
    parts[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim()
  }
  const timestamp = parseInt(parts.t ?? '', 10)
  if (!Number.isFinite(timestamp)) {
    throw new WebhookSignatureError('Missing or invalid `t` in X-Trove-Signature')
  }
  if (!parts.v1) {
    throw new WebhookSignatureError('Missing `v1` in X-Trove-Signature')
  }
  return { timestamp, v1: parts.v1 }
}

export interface VerifyWebhookOptions {
  secret:           string
  body:             string | Uint8Array
  signatureHeader:  string
  toleranceSeconds?: number
}

/**
 * Verify a Trove webhook delivery and return the parsed event. Throws
 * `WebhookSignatureError` on bad signature, missing fields, or stale timestamp.
 *
 * Pass the *raw* request body — JSON re-serialization will invalidate the
 * signature.
 *
 * Example (Express):
 *     app.post('/trove-webhook', express.raw({type: 'application/json'}), (req, res) => {
 *       const event = verifyWebhook({
 *         secret: process.env.TROVE_WEBHOOK_SECRET!,
 *         body: req.body,
 *         signatureHeader: req.header('x-trove-signature')!,
 *       })
 *       ...
 *     })
 */
export function verifyWebhook<T = Record<string, unknown>>(
  opts: VerifyWebhookOptions,
): WebhookEvent<T> {
  const tolerance = opts.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS
  if (!opts.signatureHeader) {
    throw new WebhookSignatureError('Missing X-Trove-Signature header')
  }
  const { timestamp, v1 } = parseHeader(opts.signatureHeader)

  const now = Math.floor(Date.now() / 1000)
  if (Math.abs(now - timestamp) > tolerance) {
    throw new WebhookSignatureError(
      `Signature timestamp outside tolerance (${Math.abs(now - timestamp)}s > ${tolerance}s)`,
    )
  }

  const raw = typeof opts.body === 'string' ? Buffer.from(opts.body, 'utf8') : Buffer.from(opts.body)
  const prefix = Buffer.from(`${timestamp}.`, 'utf8')
  const payload = Buffer.concat([prefix, raw])

  const expected = createHmac('sha256', opts.secret).update(payload).digest('hex')
  const expectedBuf = Buffer.from(expected, 'hex')
  let providedBuf: Buffer
  try {
    providedBuf = Buffer.from(v1, 'hex')
  } catch {
    throw new WebhookSignatureError('Signature is not valid hex')
  }
  if (
    expectedBuf.length !== providedBuf.length ||
    !timingSafeEqual(expectedBuf, providedBuf)
  ) {
    throw new WebhookSignatureError('Signature mismatch')
  }

  let decoded: WebhookEvent<T>
  try {
    decoded = JSON.parse(raw.toString('utf8')) as WebhookEvent<T>
  } catch (e) {
    throw new WebhookSignatureError(`Body is not valid JSON: ${(e as Error).message}`)
  }
  return decoded
}
