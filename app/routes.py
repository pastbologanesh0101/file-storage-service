"""HTTP API for the File Storage Service.

See README.md for the full API description with curl examples.
"""
import hashlib
import uuid

from flask import Blueprint, current_app, jsonify, request, Response

from .storage import PathTraversalError, Storage, validate_bucket_name, validate_key
from .signing import generate_presigned, verify_presigned

bp = Blueprint("api", __name__)


def _storage() -> Storage:
    key = "_storage_instance"
    if key not in current_app.config:
        current_app.config[key] = Storage(current_app.config["STORAGE_ROOT"])
    return current_app.config[key]


def _db():
    return current_app.config["DATABASE"]


def _clock():
    return current_app.config["CLOCK"]


def _bucket_row_to_dict(row):
    return {
        "name": row["name"],
        "versioning": bool(row["versioning"]),
        "created_at": row["created_at"],
    }


def _object_row_to_dict(row):
    return {
        "bucket": row["bucket"],
        "key": row["key"],
        "version_id": row["version_id"],
        "content_type": row["content_type"],
        "size": row["size"],
        "checksum_sha256": row["checksum"],
        "created_at": row["created_at"],
        "is_latest": bool(row["is_latest"]),
        "deleted": bool(row["deleted"]),
    }


@bp.errorhandler(PathTraversalError)
def _handle_path_traversal(err):
    return jsonify({"error": str(err)}), 400


# ---------------------------------------------------------------- buckets
@bp.route("/buckets", methods=["POST"])
def create_bucket():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    versioning = bool(data.get("versioning", False))
    if not name:
        return jsonify({"error": "bucket name is required"}), 400
    validate_bucket_name(name)

    db = _db()
    if db.get_bucket(name) is not None:
        return jsonify({"error": f"bucket '{name}' already exists"}), 409

    now = _clock().now()
    db.create_bucket(name, versioning, now)
    _storage().create_bucket_dir(name)
    return jsonify({"name": name, "versioning": versioning, "created_at": now}), 201


@bp.route("/buckets", methods=["GET"])
def list_buckets():
    rows = _db().list_buckets()
    return jsonify({"buckets": [_bucket_row_to_dict(r) for r in rows]}), 200


# ---------------------------------------------------------------- objects
@bp.route("/buckets/<bucket>/objects/<path:key>", methods=["PUT"])
def put_object(bucket, key):
    validate_bucket_name(bucket)
    validate_key(key)
    db = _db()
    bucket_row = db.get_bucket(bucket)
    if bucket_row is None:
        return jsonify({"error": f"bucket '{bucket}' does not exist"}), 404

    data = request.get_data()
    content_type = request.content_type or "application/octet-stream"
    checksum = hashlib.sha256(data).hexdigest()
    version_id = uuid.uuid4().hex
    now = _clock().now()

    storage = _storage()
    storage_path = storage.write(bucket, version_id, data)

    versioning = bool(bucket_row["versioning"])
    if not versioning:
        # Overwrite semantics: remove any prior physical version(s) for
        # this key so only the newest bytes are kept on disk.
        old_versions = db.list_versions(bucket, key)
        for old in old_versions:
            if old["storage_path"]:
                storage.delete(old["storage_path"])
        db.clear_latest(bucket, key)
        for old in old_versions:
            db.delete_object_row(bucket, key, old["version_id"])
    else:
        db.clear_latest(bucket, key)

    db.insert_object_version(
        bucket=bucket, key=key, version_id=version_id, content_type=content_type,
        size=len(data), checksum=checksum, created_at=now, is_latest=True,
        deleted=False, storage_path=storage_path,
    )

    return jsonify({
        "bucket": bucket, "key": key, "version_id": version_id,
        "size": len(data), "checksum_sha256": checksum, "created_at": now,
    }), 201


@bp.route("/buckets/<bucket>/objects/<path:key>", methods=["GET"])
def get_object(bucket, key):
    validate_bucket_name(bucket)
    validate_key(key)
    version_id = request.args.get("version_id")
    row = _lookup_object(bucket, key, version_id)
    if row is None or row["deleted"]:
        return jsonify({"error": "object not found"}), 404

    data = _storage().read(row["storage_path"])
    resp = Response(data, mimetype=row["content_type"] or "application/octet-stream")
    resp.headers["X-Checksum-SHA256"] = row["checksum"]
    resp.headers["X-Version-Id"] = row["version_id"]
    resp.headers["Content-Length"] = str(row["size"])
    return resp


@bp.route("/buckets/<bucket>/objects/<path:key>", methods=["HEAD"])
def head_object(bucket, key):
    validate_bucket_name(bucket)
    validate_key(key)
    version_id = request.args.get("version_id")
    row = _lookup_object(bucket, key, version_id)
    if row is None or row["deleted"]:
        return Response(status=404)

    resp = Response(status=200)
    resp.headers["X-Checksum-SHA256"] = row["checksum"]
    resp.headers["X-Version-Id"] = row["version_id"]
    resp.headers["Content-Length"] = str(row["size"])
    resp.headers["Content-Type"] = row["content_type"] or "application/octet-stream"
    return resp


@bp.route("/buckets/<bucket>/objects/<path:key>", methods=["DELETE"])
def delete_object(bucket, key):
    validate_bucket_name(bucket)
    validate_key(key)
    db = _db()
    bucket_row = db.get_bucket(bucket)
    if bucket_row is None:
        return jsonify({"error": f"bucket '{bucket}' does not exist"}), 404

    latest = db.get_latest(bucket, key)
    if latest is None or latest["deleted"]:
        return jsonify({"error": "object not found"}), 404

    versioning = bool(bucket_row["versioning"])
    now = _clock().now()
    if versioning:
        # Add a delete marker as the new latest version; prior versions
        # remain retrievable by version id.
        db.clear_latest(bucket, key)
        db.insert_object_version(
            bucket=bucket, key=key, version_id=uuid.uuid4().hex,
            content_type=None, size=0, checksum="", created_at=now,
            is_latest=True, deleted=True, storage_path=None,
        )
    else:
        if latest["storage_path"]:
            _storage().delete(latest["storage_path"])
        db.delete_object_row(bucket, key, latest["version_id"])

    return "", 204


@bp.route("/buckets/<bucket>/objects", methods=["GET"])
def list_objects(bucket):
    validate_bucket_name(bucket)
    db = _db()
    if db.get_bucket(bucket) is None:
        return jsonify({"error": f"bucket '{bucket}' does not exist"}), 404

    prefix = request.args.get("prefix", "")
    rows = db.list_objects(bucket, prefix)
    return jsonify({"objects": [_object_row_to_dict(r) for r in rows]}), 200


def _lookup_object(bucket, key, version_id):
    db = _db()
    if db.get_bucket(bucket) is None:
        return None
    if version_id:
        return db.get_version(bucket, key, version_id)
    return db.get_latest(bucket, key)


# ---------------------------------------------------------- presigned URLs
@bp.route("/buckets/<bucket>/objects/<path:key>/presign", methods=["POST"])
def presign_object(bucket, key):
    validate_bucket_name(bucket)
    validate_key(key)
    data = request.get_json(silent=True) or {}
    expires_in = data.get("expires_in", 3600)
    version_id_arg = request.args.get("version_id") or data.get("version_id")

    row = _lookup_object(bucket, key, version_id_arg)
    if row is None or row["deleted"]:
        return jsonify({"error": "object not found"}), 404

    now = _clock().now()
    expires_at = now + float(expires_in)
    secret = current_app.config["PRESIGN_SECRET"]
    payload_b64, sig = generate_presigned(secret, bucket, key, row["version_id"], expires_at)

    return jsonify({
        "url": f"/presigned?p={payload_b64}&sig={sig}",
        "payload": payload_b64,
        "signature": sig,
        "expires_at": expires_at,
        "version_id": row["version_id"],
    }), 200


@bp.route("/presigned", methods=["GET"])
def download_presigned():
    payload_b64 = request.args.get("p", "")
    sig = request.args.get("sig", "")
    secret = current_app.config["PRESIGN_SECRET"]
    now = _clock().now()

    payload, error = verify_presigned(secret, payload_b64, sig, now)
    if error:
        return jsonify({"error": error}), 403

    row = _lookup_object(payload["bucket"], payload["key"], payload["version_id"])
    if row is None or row["deleted"]:
        return jsonify({"error": "object not found"}), 404

    data = _storage().read(row["storage_path"])
    resp = Response(data, mimetype=row["content_type"] or "application/octet-stream")
    resp.headers["X-Checksum-SHA256"] = row["checksum"]
    resp.headers["X-Version-Id"] = row["version_id"]
    return resp
