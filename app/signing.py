"""HMAC-signed, time-limited presigned download URLs/tokens.

A presigned token embeds (bucket, key, version_id, expires_at) as a
base64url JSON payload, plus an HMAC-SHA256 signature over that
payload using the app's secret key. Anyone holding the token+signature
can GET the object until it expires, with no other authentication --
but they cannot tamper with the payload (e.g. change the key or push
the expiry out) without invalidating the signature.
"""
import base64
import hashlib
import hmac
import json


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def generate_presigned(secret_key: str, bucket: str, key: str, version_id: str,
                        expires_at: float):
    """Returns (payload_b64, signature_hex)."""
    payload = {
        "bucket": bucket,
        "key": key,
        "version_id": version_id,
        "expires_at": expires_at,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = _b64encode(payload_bytes)
    sig = hmac.new(secret_key.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return payload_b64, sig


def verify_presigned(secret_key: str, payload_b64: str, sig: str, now: float):
    """Returns (payload_dict, None) on success or (None, error_message)."""
    if not payload_b64 or not sig:
        return None, "missing token or signature"
    expected_sig = hmac.new(secret_key.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return None, "invalid signature"
    try:
        payload = json.loads(_b64decode(payload_b64))
    except Exception:
        return None, "invalid payload"
    if now > payload.get("expires_at", 0):
        return None, "url expired"
    return payload, None
