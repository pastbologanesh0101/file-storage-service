"""Filesystem-backed byte storage, sandboxed to a root directory.

All bucket names and object keys are validated so that nothing can
escape the configured storage root (no path traversal via "..",
absolute paths, null bytes, etc).
"""
import os


class PathTraversalError(ValueError):
    """Raised when a bucket name or key would escape the storage root."""


def validate_name(name: str, field: str = "name") -> str:
    if not isinstance(name, str) or not name:
        raise PathTraversalError(f"invalid {field}: must be a non-empty string")
    if "\x00" in name:
        raise PathTraversalError(f"invalid {field}: contains null byte")
    if name.startswith("/") or name.startswith("\\"):
        raise PathTraversalError(f"invalid {field}: must not be absolute")
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise PathTraversalError(f"invalid {field}: contains illegal path segment")
    return name


def validate_bucket_name(bucket: str) -> str:
    validate_name(bucket, "bucket name")
    if "/" in bucket:
        raise PathTraversalError("invalid bucket name: must not contain '/'")
    return bucket


def validate_key(key: str) -> str:
    return validate_name(key, "object key")


class Storage:
    """Stores raw object bytes on disk under `root`, sandboxed."""

    def __init__(self, root: str):
        self.root = os.path.realpath(root)
        os.makedirs(self.root, exist_ok=True)

    def _bucket_dir(self, bucket: str) -> str:
        validate_bucket_name(bucket)
        path = os.path.realpath(os.path.join(self.root, bucket))
        self._assert_within_root(path)
        return path

    def _assert_within_root(self, path: str) -> None:
        if path != self.root and not path.startswith(self.root + os.sep):
            raise PathTraversalError("resolved path escapes storage root")

    def create_bucket_dir(self, bucket: str) -> None:
        path = self._bucket_dir(bucket)
        os.makedirs(path, exist_ok=True)

    def object_path(self, bucket: str, version_id: str) -> str:
        # Object bytes are addressed by version id (opaque uuid), which
        # sidesteps any issue with keys containing slashes or unusual
        # characters while still keeping everything sandboxed per-bucket.
        validate_name(version_id, "version id")
        bucket_dir = self._bucket_dir(bucket)
        path = os.path.realpath(os.path.join(bucket_dir, f"{version_id}.bin"))
        self._assert_within_root(path)
        if not path.startswith(bucket_dir + os.sep) and path != bucket_dir:
            raise PathTraversalError("resolved object path escapes bucket directory")
        return path

    def write(self, bucket: str, version_id: str, data: bytes) -> str:
        self.create_bucket_dir(bucket)
        path = self.object_path(bucket, version_id)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def read(self, path: str) -> bytes:
        self._assert_within_root(os.path.realpath(path))
        with open(path, "rb") as fh:
            return fh.read()

    def delete(self, path: str) -> None:
        real = os.path.realpath(path)
        self._assert_within_root(real)
        if os.path.exists(real):
            os.remove(real)
