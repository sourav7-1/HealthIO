"""S3-compatible object storage (MinIO locally, AWS S3 ap-south-1 in production).

Clients never receive long-lived access: uploads and downloads go through short-lived
presigned URLs, and every object is encrypted at rest.
"""

import asyncio
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config

from app.core.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def _client(settings: Settings, endpoint: str | None) -> "S3Client":
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.ttl = settings.s3_presign_ttl_seconds
        self.client: S3Client = _client(settings, settings.s3_endpoint_url)
        # Presigned URLs are signed for the host the browser will call.
        self.public_client: S3Client = (
            _client(settings, settings.s3_public_endpoint_url)
            if settings.s3_public_endpoint_url
            else self.client
        )

    def presign_upload(self, key: str, content_type: str, max_bytes: int) -> dict[str, Any]:
        """Presigned POST that pins the content type and caps the upload size."""
        return self.public_client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type, "x-amz-server-side-encryption": "AES256"},
            Conditions=[
                {"Content-Type": content_type},
                {"x-amz-server-side-encryption": "AES256"},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=self.ttl,
        )

    def presign_download(self, key: str, *, filename: str | None = None, ttl: int = 60) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if filename:
            safe = "".join(c for c in filename if c.isalnum() or c in "._- ")[:100] or "document"
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        return self.public_client.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    async def read(self, key: str, max_bytes: int) -> bytes | None:
        """Whole object (bounded), or None if missing or larger than max_bytes."""

        def _read() -> bytes | None:
            try:
                head = self.client.head_object(Bucket=self.bucket, Key=key)
            except self.client.exceptions.ClientError:
                return None
            if head["ContentLength"] > max_bytes:
                return None
            body: bytes = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            return body

        return await asyncio.to_thread(_read)

    async def ping(self) -> None:
        await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
