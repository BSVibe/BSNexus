"""Tests for workspace storage backend and service."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from backend.src.core.workspace.local_storage import LocalStorageBackend
from backend.src.core.workspace.service import WorkspaceService


@pytest_asyncio.fixture
async def storage(tmp_path):
    return LocalStorageBackend(str(tmp_path))


@pytest_asyncio.fixture
async def service(storage):
    return WorkspaceService(storage)


class TestLocalStorageBackend:
    async def test_init_and_exists(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        path = await storage.init_workspace(ws_id)
        assert path.endswith(ws_id)
        assert await storage.exists(ws_id, "")

    async def test_write_and_read(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        await storage.write_file(ws_id, "hello.txt", b"Hello World")
        data = await storage.read_file(ws_id, "hello.txt")
        assert data == b"Hello World"

    async def test_read_nonexistent_raises(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        with pytest.raises(FileNotFoundError):
            await storage.read_file(ws_id, "nope.txt")

    async def test_list_files(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        await storage.write_file(ws_id, "a.py", b"# python")
        await storage.write_file(ws_id, "src/b.py", b"# nested")

        files = await storage.list_files(ws_id)
        names = {f.name for f in files}
        assert "a.py" in names
        assert "src" in names  # directory

    async def test_list_files_recursive(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        await storage.write_file(ws_id, "src/main.py", b"# main")
        await storage.write_file(ws_id, "src/utils/helper.py", b"# help")

        files = await storage.list_files(ws_id, recursive=True)
        paths = {f.path for f in files}
        assert "src" in paths
        assert "src/main.py" in paths
        assert "src/utils" in paths
        assert "src/utils/helper.py" in paths

    async def test_delete_file(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        await storage.write_file(ws_id, "tmp.txt", b"temp")
        await storage.delete_file(ws_id, "tmp.txt")
        assert not await storage.exists(ws_id, "tmp.txt")

    async def test_delete_workspace(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        await storage.write_file(ws_id, "data.txt", b"data")
        await storage.delete_workspace(ws_id)
        assert not await storage.exists(ws_id, "")

    async def test_path_traversal_blocked(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        with pytest.raises(PermissionError):
            await storage.read_file(ws_id, "../../etc/passwd")

    async def test_hidden_files_excluded(self, storage: LocalStorageBackend):
        ws_id = str(uuid.uuid4())
        await storage.init_workspace(ws_id)
        await storage.write_file(ws_id, ".hidden", b"secret")
        await storage.write_file(ws_id, "visible.txt", b"public")
        files = await storage.list_files(ws_id)
        names = {f.name for f in files}
        assert "visible.txt" in names
        assert ".hidden" not in names


class TestWorkspaceService:
    async def test_create_and_list(self, service: WorkspaceService):
        project_id = uuid.uuid4()
        path = await service.create_workspace(project_id)
        assert path

        # Write and list
        await service.write_file(project_id, "README.md", b"# Hello")
        files = await service.list_files(project_id)
        assert len(files) == 1
        assert files[0].name == "README.md"

    async def test_read_file(self, service: WorkspaceService):
        project_id = uuid.uuid4()
        await service.create_workspace(project_id)
        await service.write_file(project_id, "app.py", b"print('hi')")
        data = await service.read_file(project_id, "app.py")
        assert data == b"print('hi')"

    async def test_cleanup(self, service: WorkspaceService):
        project_id = uuid.uuid4()
        await service.create_workspace(project_id)
        await service.write_file(project_id, "data.txt", b"x")
        await service.cleanup_workspace(project_id)
        assert not await service.file_exists(project_id, "data.txt")

    async def test_get_workspace_path(self, service: WorkspaceService):
        project_id = uuid.uuid4()
        path = service.get_workspace_path(project_id)
        assert str(project_id) in path
