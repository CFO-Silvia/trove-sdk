export interface KeyMetadata {
  key_id: string
  name: string
  prefix: string
  scope: 'workspace' | 'admin'
  namespace: string | null
  created_at: string
}

export interface KeyCreated extends KeyMetadata {
  api_key: string
}

export interface FileResult {
  path: string
  size_bytes: number
}
