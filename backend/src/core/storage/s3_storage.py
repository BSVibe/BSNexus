"""S3Storage — aioboto3 backend for S3-compatible object storage.

MinIO (dev) and Cloudflare R2 (prod) both implement the S3 API. The
same client works; only endpoint and credentials differ.

Lazy import of aioboto3 so that the dependency isn't required for
tests that use LocalStorage.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from backend.src.core.storage.deliverable_storage import ContentRef
from backend.src.models import StorageBackend


async def _iter_to_bytes(content: AsyncIterator[bytes] | bytes) -> bytes:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    chunks: list[bytes] = []
    async for chunk in content:
        chunks.append(chunk)
    return b"".join(chunks)


class S3Storage:
    backend = StorageBackend.object

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        region_name: str = "auto",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ):
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key

    def _session(self):
        import aioboto3  # lazy

        return aioboto3.Session()

    def _client_kwargs(self) -> dict:
        kwargs: dict = {"region_name": self._region_name}
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        if self._aws_access_key_id:
            kwargs["aws_access_key_id"] = self._aws_access_key_id
        if self._aws_secret_access_key:
            kwargs["aws_secret_access_key"] = self._aws_secret_access_key
        return kwargs

    async def put(
        self,
        key: str,
        content: AsyncIterator[bytes] | bytes,
        *,
        metadata: dict | None = None,
    ) -> ContentRef:
        data = await _iter_to_bytes(content)
        etag = hashlib.sha256(data).hexdigest()
        session = self._session()
        async with session.client("s3", **self._client_kwargs()) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                Metadata={k: str(v) for k, v in (metadata or {}).items()},
            )
        return ContentRef(
            backend=StorageBackend.object,
            data={"bucket": self._bucket, "key": key, "etag": etag},
        )

    async def get(self, ref: ContentRef) -> AsyncIterator[bytes]:
        session = self._session()
        async with session.client("s3", **self._client_kwargs()) as s3:
            resp = await s3.get_object(Bucket=ref.data["bucket"], Key=ref.data["key"])
            body = resp["Body"]

            async def _gen() -> AsyncIterator[bytes]:
                async for chunk in body.iter_chunks():
                    yield chunk

            return _gen()

    async def presign(self, ref: ContentRef, ttl_s: int = 3600) -> str:
        session = self._session()
        async with session.client("s3", **self._client_kwargs()) as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": ref.data["bucket"], "Key": ref.data["key"]},
                ExpiresIn=ttl_s,
            )

    async def delete(self, ref: ContentRef) -> None:
        session = self._session()
        async with session.client("s3", **self._client_kwargs()) as s3:
            await s3.delete_object(Bucket=ref.data["bucket"], Key=ref.data["key"])
