import { TroveError } from './error.js'
import type { FileResult } from './types.js'

const DEFAULT_BASE_URL = 'https://api.trovefiles.dev'

function normPath(path: string): string {
  const p = path.replace(/^\/+/, '')
  return p.startsWith('workspace/') ? p.slice('workspace/'.length) : p
}

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

export class TroveClient {
  private readonly baseUrl: string
  private readonly headers: Record<string, string>

  constructor(
    apiKey: string,
    namespace: string,
    { baseUrl = DEFAULT_BASE_URL }: { baseUrl?: string } = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.headers = {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
      'X-Namespace': namespace,
    }
  }

  async exec(command: string): Promise<string> {
    const res = await fetch(`${this.baseUrl}/exec`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({ command }),
    })
    await raiseForStatus(res)
    return res.text()
  }

  async write(path: string, content: string): Promise<FileResult> {
    const res = await fetch(`${this.baseUrl}/write`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({ path: normPath(path), content }),
    })
    await raiseForStatus(res)
    return res.json() as Promise<FileResult>
  }

  async upload(path: string, data: Uint8Array | ArrayBuffer): Promise<FileResult> {
    const headers = { ...this.headers }
    delete headers['Content-Type']
    const res = await fetch(`${this.baseUrl}/files/${normPath(path)}`, {
      method: 'PUT',
      headers,
      body: data as BodyInit,
    })
    await raiseForStatus(res)
    return res.json() as Promise<FileResult>
  }

  async delete(path: string): Promise<string> {
    const res = await fetch(`${this.baseUrl}/delete`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({ path: normPath(path) }),
    })
    await raiseForStatus(res)
    const body = await res.json() as { deleted: string }
    return body.deleted
  }
}
