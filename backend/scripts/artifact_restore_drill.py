"""Back up S3 object bytes, restore into a UUID bucket, and compare hashes.

Source objects are read-only. Only the bucket created by this invocation is
removed. Credentials come from environment variables and are never written.
Run against a quiescent source for a coordinated database/object backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import boto3


def object_manifest(client, bucket):
    return sorted(
        [{"key": item["Key"], "size": item["Size"], "etag": item["ETag"]}
         for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket)
         for item in page.get("Contents", [])], key=lambda item: item["key"])


def restore_drill(client, source_bucket, output_dir):
    started = time.monotonic()
    target = "rf-restore-drill-" + uuid4().hex
    root = Path(output_dir).resolve() / target
    root.mkdir(parents=True, exist_ok=False)
    report = {"status": "failed", "source_bucket": source_bucket,
              "target_bucket": target, "target_removed": False}
    created = False
    try:
        before = object_manifest(client, source_bucket)
        records = []
        for index, item in enumerate(before):
            response = client.get_object(Bucket=source_bucket, Key=item["key"], IfMatch=item["etag"])
            blob = response["Body"]
            try:
                content = blob.read()
            finally:
                blob.close()
            filename = f"{index:08d}.blob"
            (root / filename).write_bytes(content)
            records.append({**item, "file": filename, "sha256": hashlib.sha256(content).hexdigest(),
                            "content_type": response.get("ContentType", "application/octet-stream"),
                            "metadata": response.get("Metadata", {})})
        if before != object_manifest(client, source_bucket):
            raise RuntimeError("SOURCE_CHANGED_DURING_BACKUP")
        (root / "manifest.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        client.create_bucket(Bucket=target)
        created = True
        for record in records:
            client.put_object(Bucket=target, Key=record["key"], Body=(root / record["file"]).read_bytes(),
                              ContentType=record["content_type"], Metadata=record["metadata"])
        actual = object_manifest(client, target)
        if [(x["key"], x["size"]) for x in actual] != [(x["key"], x["size"]) for x in before]:
            raise RuntimeError("RESTORED_MANIFEST_MISMATCH")
        for record in records:
            response = client.get_object(Bucket=target, Key=record["key"])
            blob = response["Body"]
            try:
                content = blob.read()
            finally:
                blob.close()
            if hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise RuntimeError("RESTORED_HASH_MISMATCH")
            if response.get("Metadata", {}) != record["metadata"] or response.get("ContentType") != record["content_type"]:
                raise RuntimeError("RESTORED_METADATA_MISMATCH")
        report.update(status="passed", object_count=len(records), backup_bytes=sum(x["size"] for x in records),
                      elapsed_seconds=round(time.monotonic() - started, 3))
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            if created:
                # UUID target created above, never source_bucket.
                for page in client.get_paginator("list_objects_v2").paginate(Bucket=target):
                    objects = [{"Key": x["Key"]} for x in page.get("Contents", [])]
                    if objects:
                        result = client.delete_objects(Bucket=target, Delete={"Objects": objects})
                        if result.get("Errors"):
                            raise RuntimeError("TARGET_CLEANUP_FAILED")
                client.delete_bucket(Bucket=target)
                report["target_removed"] = True
        finally:
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", default="researchforge")
    parser.add_argument("--output-dir", default=".data/artifact-restore-drills")
    args = parser.parse_args()
    client = boto3.client("s3", endpoint_url=args.endpoint,
                          aws_access_key_id=os.getenv("MINIO_ROOT_USER"),
                          aws_secret_access_key=os.getenv("MINIO_ROOT_PASSWORD"),
                          region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    print(json.dumps(restore_drill(client, args.bucket, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
