export type WorkspaceType = 'server_managed' | 'local_import' | 'github_connected'

export interface FileInfo {
  path: string
  name: string
  is_dir: boolean
  size: number
  modified_at: string | null
}

export interface FileListResponse {
  files: FileInfo[]
  current_path: string
}

export interface FileContentResponse {
  path: string
  content: string
  is_binary: boolean
  size: number
}

export interface GitHubConnection {
  repo_url: string
  branch: string
  connected: boolean
  last_synced: string | null
}
