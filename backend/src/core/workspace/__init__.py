"""Workspace storage — abstracted file storage for project workspaces."""

from backend.src.core.workspace.storage_backend import FileInfo, StorageBackend
from backend.src.core.workspace.local_storage import LocalStorageBackend
from backend.src.core.workspace.service import WorkspaceService

__all__ = ["FileInfo", "LocalStorageBackend", "StorageBackend", "WorkspaceService"]
