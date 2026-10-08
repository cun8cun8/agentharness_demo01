from types import SimpleNamespace

from app.infra.artifact_store import ArtifactBlobStore


class MissingBucket(Exception):
    response = {"Error": {"Code": "NoSuchBucket"}}


class FakeS3Client:
    def __init__(self) -> None:
        self.created = False
        self.created_buckets: list[str] = []

    def head_bucket(self, *, Bucket: str) -> None:
        if not self.created:
            raise MissingBucket()

    def create_bucket(self, *, Bucket: str) -> None:
        self.created = True
        self.created_buckets.append(Bucket)

    def put_object(self, **kwargs) -> None:
        self.put_kwargs = kwargs


def test_s3_probe_can_create_a_missing_local_bucket() -> None:
    client = FakeS3Client()
    blob_store = ArtifactBlobStore(
        SimpleNamespace(
            artifact_store_backend="s3",
            artifact_store_bucket="researchforge",
            artifact_store_auto_create_bucket=True,
        )
    )
    blob_store._client = lambda: client  # type: ignore[method-assign]

    result = blob_store.probe()

    assert result["ready"] is True
    assert client.created_buckets == ["researchforge"]


def test_s3_artifacts_are_workspace_scoped_and_can_use_kms_encryption() -> None:
    client = FakeS3Client()
    blob_store = ArtifactBlobStore(
        SimpleNamespace(
            artifact_store_backend="s3",
            artifact_store_bucket="researchforge",
            artifact_store_auto_create_bucket=True,
            artifact_store_server_side_encryption="aws:kms",
            artifact_store_kms_key_id="key-123",
            artifact_store_workspace_prefix=True,
        )
    )
    blob_store._client = lambda: client  # type: ignore[method-assign]

    uri = blob_store.save("artifact-123", "hello", "workspace/a")

    assert uri == "s3://researchforge/artifacts/workspace_a/ar/ti/artifact-123.txt"
    assert client.put_kwargs["ServerSideEncryption"] == "aws:kms"
    assert client.put_kwargs["SSEKMSKeyId"] == "key-123"
