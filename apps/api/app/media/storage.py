from dataclasses import dataclass
from typing import Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings


class StorageProvider(Protocol):
    def create_upload_url(self, key: str, content_type: str, expires_in: int) -> str: ...
    def create_download_url(self, key: str, expires_in: int) -> str: ...
    def head(self, key: str) -> tuple[int, str]: ...
    def get(self, key: str) -> bytes: ...
    def put(self, key: str, body: bytes, content_type: str) -> None: ...
    def delete(self, key: str) -> None: ...


@dataclass
class S3StorageProvider:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str

    @property
    def client(self):
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
        settings = get_settings()
        if settings.environment != "production":
            self.client.put_bucket_cors(
                Bucket=self.bucket,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedHeaders": ["content-type"],
                            "AllowedMethods": ["PUT"],
                            "AllowedOrigins": [settings.web_origin],
                            "ExposeHeaders": [],
                            "MaxAgeSeconds": 300,
                        }
                    ]
                },
            )

    def create_upload_url(self, key: str, content_type: str, expires_in: int) -> str:
        self.ensure_bucket()
        return self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )

    def create_download_url(self, key: str, expires_in: int) -> str:
        self.ensure_bucket()
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
        )

    def head(self, key: str) -> tuple[int, str]:
        result = self.client.head_object(Bucket=self.bucket, Key=key)
        return int(result["ContentLength"]), result.get("ContentType", "application/octet-stream")

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def storage_provider() -> S3StorageProvider:
    settings = get_settings()
    return S3StorageProvider(
        endpoint_url=settings.storage_endpoint_url,
        access_key=settings.storage_access_key,
        secret_key=settings.storage_secret_key,
        bucket=settings.storage_bucket,
        region=settings.storage_region,
    )
