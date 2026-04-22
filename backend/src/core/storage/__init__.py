"""Deliverable storage — pluggable backend for versioned artifacts.

Two tracks:
- Code → git (branch + commit, reuses core/git_ops.py)
- Everything else → S3-compatible object storage (MinIO dev, R2 prod)
- LocalStorage fallback for tests / dev without containers

Each version's storage backend is recorded in
``deliverable_versions.storage_backend`` so future migrations can walk
references without ambiguity.
"""

from backend.src.core.storage.deliverable_storage import (
    ContentRef,
    DeliverableStorage,
    storage_for_type,
)
from backend.src.core.storage.git_storage import GitStorage
from backend.src.core.storage.local_storage import LocalStorage
from backend.src.core.storage.s3_storage import S3Storage

__all__ = [
    "ContentRef",
    "DeliverableStorage",
    "GitStorage",
    "LocalStorage",
    "S3Storage",
    "storage_for_type",
]
