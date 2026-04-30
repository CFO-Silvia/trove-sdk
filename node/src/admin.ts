import { TroveError } from './error.js'
import type {
  KeyCreated,
  KeyMetadata,
  WebhookCreated,
  WebhookMetadata,
  WebhookTestResult,
} from './types.js'

const DEFAULT_BASE_URL = 'https://api.trovefiles.dev'

async function raiseForStatus(res: Response): Promise<void> {
  if (!res.ok) {
    let detail: string
    try {
      const body = await res.clone().json()
      detail = body.detail ?? (await res.text())
    } catch {
      detail = await res.text()
    }
    throw new TroveError(detail, res.status)
  }
}

export class TroveAdminClient {
  private readonly baseUrl: string
  private readonly workspaceId: string
  private readonly headers: Record<string, string>

  constructor(
    apiKey: string,
    workspaceId: string,
    { baseUrl = DEFAULT_BASE_URL }: { baseUrl?: string } = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.workspaceId = workspaceId
    this.headers = {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    }
  }

  private get keysUrl(): string {
    return `${this.baseUrl}/v1/workspaces/${this.workspaceId}/keys`
  }

  private get webhooksUrl(): string {
    return `${this.baseUrl}/v1/workspaces/${this.workspaceId}/webhooks`
  }

  async createKey(name: string, { namespace }: { namespace?: string } = {}): Promise<KeyCreated> {
    const res = await fetch(this.keysUrl, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({ name, namespace: namespace ?? null, admin: false }),
    })
    await raiseForStatus(res)
    return res.json() as Promise<KeyCreated>
  }

  async listKeys(): Promise<KeyMetadata[]> {
    const res = await fetch(this.keysUrl, { headers: this.headers })
    await raiseForStatus(res)
    const body = await res.json() as { keys: KeyMetadata[] }
    return body.keys
  }

  async revokeKey(keyId: string): Promise<void> {
    const res = await fetch(`${this.keysUrl}/${keyId}`, {
      method: 'DELETE',
      headers: this.headers,
    })
    await raiseForStatus(res)
  }

  async createWebhook(
    url: string,
    {
      events,
      namespace,
      description,
    }: { events?: string[]; namespace?: string; description?: string } = {},
  ): Promise<WebhookCreated> {
    const res = await fetch(this.webhooksUrl, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({
        url,
        events:      events ?? ['*'],
        namespace:   namespace ?? null,
        description: description ?? null,
      }),
    })
    await raiseForStatus(res)
    return res.json() as Promise<WebhookCreated>
  }

  async listWebhooks(): Promise<WebhookMetadata[]> {
    const res = await fetch(this.webhooksUrl, { headers: this.headers })
    await raiseForStatus(res)
    const body = await res.json() as { webhooks: WebhookMetadata[] }
    return body.webhooks
  }

  async deleteWebhook(webhookId: string): Promise<void> {
    const res = await fetch(`${this.webhooksUrl}/${webhookId}`, {
      method: 'DELETE',
      headers: this.headers,
    })
    await raiseForStatus(res)
  }

  async testWebhook(webhookId: string): Promise<WebhookTestResult> {
    const res = await fetch(`${this.webhooksUrl}/${webhookId}/test`, {
      method: 'POST',
      headers: this.headers,
    })
    await raiseForStatus(res)
    return res.json() as Promise<WebhookTestResult>
  }
}
