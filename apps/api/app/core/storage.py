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


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.ttl = settings.s3_presign_ttl_seconds
        self.client: S3Client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def presign_upload(self, key: str, content_type: str, max_bytes: int) -> dict[str, Any]:
        """Presigned POST that pins the content type and caps the upload size."""
        return self.client.generate_presigned_post(
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

    def presign_download(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.ttl
        )

    async def ping(self) -> None:
        await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
