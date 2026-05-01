import { TroveError } from './error.js'
import type { FileResult, Snapshot } from './types.js'

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

  async createSnapshot(label?: string | null): Promise<Snapshot> {
    const res = await fetch(`${this.baseUrl}/v1/snapshots`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({ label: label ?? null }),
    })
    await raiseForStatus(res)
    return res.json() as Promise<Snapshot>
  }

  async listSnapshots(): Promise<Snapshot[]> {
    const res = await fetch(`${this.baseUrl}/v1/snapshots`, {
      method: 'GET',
      headers: this.headers,
    })
    await raiseForStatus(res)
    const body = await res.json() as { snapshots?: Snapshot[] }
    return body.snapshots ?? []
  }

  async restoreSnapshot(snapshotId: string): Promise<number> {
    const res = await fetch(`${this.baseUrl}/v1/snapshots/${snapshotId}/restore`, {
      method: 'POST',
      headers: this.headers,
    })
    await raiseForStatus(res)
    const body = await res.json() as { files_restored?: number }
    return body.files_restored ?? 0
  }

  async deleteSnapshot(snapshotId: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/v1/snapshots/${snapshotId}`, {
      method: 'DELETE',
      headers: this.headers,
    })
    await raiseForStatus(res)
  }
}
