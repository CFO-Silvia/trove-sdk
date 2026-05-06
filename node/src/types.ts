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

/** One entry returned by `listDir`. */
export interface FileInfo {
  name:        string
  path:        string
  is_dir:      boolean
  size_bytes:  number | null
  modified_at: string
}

/** Result of `readFile` / `readText`. */
export interface FileContent {
  path:        string
  size_bytes:  number
  modified_at: string
  encoding:    'utf-8' | 'binary'
  content:     string | null   // null when encoding === 'binary'
  truncated:   boolean         // true when file exceeded the 1 MB preview cap
}

export interface WebhookMetadata {
  webhook_id:  string
  url:         string
  events:      string[]
  namespace:   string | null
  description: string | null
  enabled:     boolean
  created_at:  string
}

export interface WebhookCreated extends WebhookMetadata {
  signing_secret: string
}

export interface WebhookTestResult {
  ok:        boolean
  event_id:  string
  status?:   number
  error?:    string
}

export interface WebhookEvent<T = Record<string, unknown>> {
  id:           string
  type:         string
  api_version:  string
  workspace_id: string
  namespace:    string | null
  created_at:   string
  data:         T
}

export interface Snapshot {
  snapshot_id: string
  namespace:   string
  label:       string | null
  size_bytes:  number
  created_at:  string
}
