# File Storage Service

A small, self-contained, S3-like object storage service written in Python
with Flask. It stores object bytes on the local filesystem (sandboxed to a
storage root) and metadata (content-type, size, SHA-256 checksum,
timestamps, versions) in SQLite. It supports buckets, per-bucket object
versioning, and HMAC-signed presigned download URLs.

This is a learning/portfolio project, not production-grade storage — there
is no auth beyond presigned URLs, no replication, and no concurrency
control beyond SQLite's own locking.

## Features

- **Buckets**: create and list buckets, optionally with versioning enabled.
- **Objects**: `PUT` (upload), `GET` (download), `HEAD` (metadata only),
  `DELETE`, and `list` (with an optional key-prefix filter) within a bucket.
- **Metadata**: every object records its content-type, size, SHA-256
  checksum, and creation time.
- **Versioning**: if a bucket has versioning enabled, re-uploading the same
  key keeps the previous bytes around, addressable by `version_id`. Buckets
  without versioning simply overwrite in place (old bytes are removed from
  disk).
- **Presigned URLs**: generate a time-limited, tamper-proof download link
  for an object without requiring the caller to have any other
  credentials.
- **Sandboxed storage**: bucket names and object keys are validated to
  reject any path-traversal attempt (`..`, absolute paths, null bytes,
  etc). All bytes are written under a single configured storage root.

## Running it

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py
```

The server listens on `http://127.0.0.1:5000`. Object bytes are written
under `storage_data/` and metadata under `metadata.sqlite3` (both are
git-ignored).

## Running the tests

```bash
pip install -r requirements.txt pytest
pytest -v
```

Tests use `create_app(testing=True)`, which spins up an isolated temp
directory for storage and an in-memory SQLite database per test, plus an
injectable `FixedClock` so presigned-URL expiry can be tested without real
sleeping.

## API

All request/response bodies are JSON unless noted otherwise (object
upload/download bodies are raw bytes).

### Create a bucket

```bash
curl -X POST http://127.0.0.1:5000/buckets \
  -H 'Content-Type: application/json' \
  -d '{"name": "photos", "versioning": true}'
```

```json
{"name": "photos", "versioning": true, "created_at": 1700000000.0}
```

### List buckets

```bash
curl http://127.0.0.1:5000/buckets
```

### Upload an object (PUT)

```bash
curl -X PUT http://127.0.0.1:5000/buckets/photos/objects/cat.png \
  -H 'Content-Type: image/png' \
  --data-binary @cat.png
```

```json
{
  "bucket": "photos",
  "key": "cat.png",
  "version_id": "3f9c...",
  "size": 48213,
  "checksum_sha256": "9a1c...",
  "created_at": 1700000000.0
}
```

Uploading the same key again:
- If the bucket has versioning **enabled**, the previous version stays on
  disk and remains retrievable by its `version_id`.
- If versioning is **disabled**, the previous bytes are deleted and the
  key now points only at the new content.

### Download an object (GET)

```bash
curl http://127.0.0.1:5000/buckets/photos/objects/cat.png -o cat.png

# a specific older version:
curl "http://127.0.0.1:5000/buckets/photos/objects/cat.png?version_id=3f9c..." -o cat_old.png
```

Response headers include `X-Checksum-SHA256` and `X-Version-Id`.

### Object metadata only (HEAD)

```bash
curl -I http://127.0.0.1:5000/buckets/photos/objects/cat.png
```

### Delete an object

```bash
curl -X DELETE http://127.0.0.1:5000/buckets/photos/objects/cat.png
```

In a versioned bucket this adds a "delete marker" as the latest version;
older versions remain retrievable by `version_id`. In a non-versioned
bucket the object and its bytes are removed entirely.

### List objects (optionally by prefix)

```bash
curl http://127.0.0.1:5000/buckets/photos/objects
curl "http://127.0.0.1:5000/buckets/photos/objects?prefix=vacation/"
```

### Presigned URLs

Generate a signed, time-limited download link for an object. The token is
a base64url-encoded JSON payload (`bucket`, `key`, `version_id`,
`expires_at`) plus an HMAC-SHA256 signature over that payload, computed
with the server's secret key. `/presigned` recomputes the HMAC and
compares it (constant-time) before checking `expires_at` against the
current time — so the URL cannot be tampered with (changing the key,
bucket, or expiry invalidates the signature) and stops working once it
expires, with no database lookup or session needed to check it.

```bash
curl -X POST http://127.0.0.1:5000/buckets/photos/objects/cat.png/presign \
  -H 'Content-Type: application/json' \
  -d '{"expires_in": 300}'
```

```json
{
  "url": "/presigned?p=eyJi...&sig=8f2a...",
  "payload": "eyJi...",
  "signature": "8f2a...",
  "expires_at": 1700000300.0,
  "version_id": "3f9c..."
}
```

```bash
curl "http://127.0.0.1:5000/presigned?p=eyJi...&sig=8f2a..." -o cat.png
```

After `expires_at` has passed, the same URL returns `403 {"error": "url expired"}`.
A tampered payload or signature returns `403 {"error": "invalid signature"}`.

## Project layout

```
app/
  __init__.py   - application factory (create_app)
  clock.py      - injectable clock (real + fixed, for tests)
  db.py         - SQLite metadata store
  storage.py    - sandboxed filesystem byte storage
  signing.py    - HMAC presigned-URL signing/verification
  routes.py     - HTTP API
tests/          - pytest suite using Flask's test client
run.py          - dev server entry point
```

## License

MIT — see [LICENSE](LICENSE).
