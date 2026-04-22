"""Tests for DeliverableStorage — LocalStorage + factory dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.src.core.storage import (
    ContentRef,
    GitStorage,
    LocalStorage,
    storage_for_type,
)
from backend.src.models import DeliverableType, StorageBackend


@pytest.mark.asyncio
async def test_local_storage_put_and_get_round_trip(tmp_path):
    storage = LocalStorage(tmp_path)

    ref = await storage.put("nested/file.txt", b"hello world")

    assert ref.backend == StorageBackend.object
    assert ref.data["key"] == "nested/file.txt"
    assert ref.data["etag"]

    stream = await storage.get(ref)
    chunks = [chunk async for chunk in stream]
    assert b"".join(chunks) == b"hello world"


@pytest.mark.asyncio
async def test_local_storage_presign_returns_file_uri(tmp_path):
    storage = LocalStorage(tmp_path)
    ref = await storage.put("a.txt", b"x")

    url = await storage.presign(ref)
    assert url.startswith("file://")


@pytest.mark.asyncio
async def test_local_storage_delete_removes_file(tmp_path):
    storage = LocalStorage(tmp_path)
    ref = await storage.put("a.txt", b"x")

    assert Path(ref.data["path"]).exists()
    await storage.delete(ref)
    assert not Path(ref.data["path"]).exists()


def test_content_ref_serialization_roundtrip():
    ref = ContentRef(
        backend=StorageBackend.object,
        data={"bucket": "b", "key": "k", "etag": "e"},
    )
    raw = ref.to_dict()
    assert raw == {"backend": "object", "bucket": "b", "key": "k", "etag": "e"}

    restored = ContentRef.from_dict(raw)
    assert restored.backend == StorageBackend.object
    assert restored.data == {"bucket": "b", "key": "k", "etag": "e"}


def test_storage_for_type_code_goes_to_git(tmp_path):
    obj = LocalStorage(tmp_path / "obj")
    git = GitStorage(str(tmp_path / "git"))

    chosen = storage_for_type(
        DeliverableType.code, object_storage=obj, git_storage=git
    )
    assert chosen is git


def test_storage_for_type_doc_goes_to_object(tmp_path):
    obj = LocalStorage(tmp_path / "obj")
    git = GitStorage(str(tmp_path / "git"))

    for kind in (
        DeliverableType.doc,
        DeliverableType.design,
        DeliverableType.data,
        DeliverableType.url,
    ):
        chosen = storage_for_type(kind, object_storage=obj, git_storage=git)
        assert chosen is obj


@pytest.mark.asyncio
async def test_local_storage_accepts_async_iterator(tmp_path):
    storage = LocalStorage(tmp_path)

    async def gen():
        yield b"chunk-"
        yield b"one-"
        yield b"two"

    ref = await storage.put("streamed.bin", gen())

    stream = await storage.get(ref)
    data = b"".join([chunk async for chunk in stream])
    assert data == b"chunk-one-two"
