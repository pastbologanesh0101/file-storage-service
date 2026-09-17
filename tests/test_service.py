import hashlib


def _create_bucket(client, name, versioning=False):
    return client.post("/buckets", json={"name": name, "versioning": versioning})


def test_create_bucket_and_list(client):
    resp = _create_bucket(client, "photos")
    assert resp.status_code == 201
    assert resp.get_json()["name"] == "photos"

    resp = client.get("/buckets")
    assert resp.status_code == 200
    names = [b["name"] for b in resp.get_json()["buckets"]]
    assert names == ["photos"]


def test_upload_and_download_round_trip_exact_bytes(client):
    _create_bucket(client, "docs")
    payload = b"\x00\x01hello world\xff\xfe binary safe content \x00"

    put_resp = client.put(
        "/buckets/docs/objects/report.bin",
        data=payload,
        content_type="application/octet-stream",
    )
    assert put_resp.status_code == 201

    get_resp = client.get("/buckets/docs/objects/report.bin")
    assert get_resp.status_code == 200
    assert get_resp.data == payload


def test_checksum_is_recorded_correctly(client):
    _create_bucket(client, "docs")
    payload = b"the quick brown fox jumps over the lazy dog"
    expected_checksum = hashlib.sha256(payload).hexdigest()

    put_resp = client.put("/buckets/docs/objects/fox.txt", data=payload)
    assert put_resp.get_json()["checksum_sha256"] == expected_checksum

    head_resp = client.head("/buckets/docs/objects/fox.txt")
    assert head_resp.headers["X-Checksum-SHA256"] == expected_checksum


def test_delete_removes_object(client):
    _create_bucket(client, "docs")
    client.put("/buckets/docs/objects/temp.txt", data=b"temporary")

    del_resp = client.delete("/buckets/docs/objects/temp.txt")
    assert del_resp.status_code == 204

    get_resp = client.get("/buckets/docs/objects/temp.txt")
    assert get_resp.status_code == 404


def test_list_objects_with_prefix_filter(client):
    _create_bucket(client, "docs")
    client.put("/buckets/docs/objects/logs/2024-01.txt", data=b"a")
    client.put("/buckets/docs/objects/logs/2024-02.txt", data=b"b")
    client.put("/buckets/docs/objects/readme.txt", data=b"c")

    resp = client.get("/buckets/docs/objects", query_string={"prefix": "logs/"})
    assert resp.status_code == 200
    keys = sorted(o["key"] for o in resp.get_json()["objects"])
    assert keys == ["logs/2024-01.txt", "logs/2024-02.txt"]

    resp_all = client.get("/buckets/docs/objects")
    all_keys = sorted(o["key"] for o in resp_all.get_json()["objects"])
    assert all_keys == ["logs/2024-01.txt", "logs/2024-02.txt", "readme.txt"]


def test_versioning_keeps_old_versions_retrievable(client):
    _create_bucket(client, "versioned", versioning=True)

    first = client.put("/buckets/versioned/objects/note.txt", data=b"version one")
    first_version_id = first.get_json()["version_id"]

    second = client.put("/buckets/versioned/objects/note.txt", data=b"version two")
    second_version_id = second.get_json()["version_id"]

    assert first_version_id != second_version_id

    latest = client.get("/buckets/versioned/objects/note.txt")
    assert latest.data == b"version two"

    old = client.get(
        "/buckets/versioned/objects/note.txt",
        query_string={"version_id": first_version_id},
    )
    assert old.status_code == 200
    assert old.data == b"version one"

    new_explicit = client.get(
        "/buckets/versioned/objects/note.txt",
        query_string={"version_id": second_version_id},
    )
    assert new_explicit.data == b"version two"


def test_non_versioned_bucket_overwrites(client):
    _create_bucket(client, "plain", versioning=False)
    client.put("/buckets/plain/objects/note.txt", data=b"first")
    client.put("/buckets/plain/objects/note.txt", data=b"second")

    resp = client.get("/buckets/plain/objects/note.txt")
    assert resp.data == b"second"


def test_presigned_url_works_before_expiry_and_rejected_after(client, clock):
    _create_bucket(client, "docs")
    client.put("/buckets/docs/objects/secret.txt", data=b"top secret payload")

    presign_resp = client.post(
        "/buckets/docs/objects/secret.txt/presign", json={"expires_in": 60}
    )
    assert presign_resp.status_code == 200
    url = presign_resp.get_json()["url"]

    # Still valid just before expiry.
    clock.advance(59)
    ok_resp = client.get(url)
    assert ok_resp.status_code == 200
    assert ok_resp.data == b"top secret payload"

    # Now push the clock past expiry -- must be rejected.
    clock.advance(10)
    expired_resp = client.get(url)
    assert expired_resp.status_code == 403
    assert "expired" in expired_resp.get_json()["error"]


def test_presigned_url_tamper_detected(client):
    _create_bucket(client, "docs")
    client.put("/buckets/docs/objects/secret.txt", data=b"top secret payload")

    presign_resp = client.post(
        "/buckets/docs/objects/secret.txt/presign", json={"expires_in": 3600}
    )
    payload = presign_resp.get_json()["payload"]
    sig = presign_resp.get_json()["signature"]

    tampered = client.get("/presigned", query_string={"p": payload, "sig": sig[:-1] + "0"})
    assert tampered.status_code == 403


def test_path_traversal_in_bucket_and_key_rejected(client):
    resp = client.post("/buckets", json={"name": "../../etc"})
    assert resp.status_code == 400

    _create_bucket(client, "docs")
    resp2 = client.put("/buckets/docs/objects/../../etc/passwd", data=b"nope")
    assert resp2.status_code == 400

    resp3 = client.put("/buckets/..%2f..%2fetc/objects/passwd", data=b"nope")
    assert resp3.status_code in (400, 404)
