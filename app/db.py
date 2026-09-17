"""SQLite-backed metadata store for buckets and objects.

Uses a single long-lived connection (with check_same_thread=False) so
that ":memory:" databases work for tests -- a fresh connection per
request would see an empty in-memory database every time.
"""
import sqlite3
import threading


SCHEMA = """
CREATE TABLE IF NOT EXISTS buckets (
    name TEXT PRIMARY KEY,
    versioning INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket TEXT NOT NULL,
    key TEXT NOT NULL,
    version_id TEXT NOT NULL,
    content_type TEXT,
    size INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    created_at REAL NOT NULL,
    is_latest INTEGER NOT NULL DEFAULT 1,
    deleted INTEGER NOT NULL DEFAULT 0,
    storage_path TEXT,
    UNIQUE(bucket, version_id)
);

CREATE INDEX IF NOT EXISTS idx_objects_bucket_key ON objects(bucket, key);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- buckets ---------------------------------------------------
    def create_bucket(self, name: str, versioning: bool, created_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO buckets (name, versioning, created_at) VALUES (?, ?, ?)",
                (name, int(versioning), created_at),
            )
            self._conn.commit()

    def get_bucket(self, name: str):
        with self._lock:
            cur = self._conn.execute("SELECT * FROM buckets WHERE name = ?", (name,))
            return cur.fetchone()

    def list_buckets(self):
        with self._lock:
            cur = self._conn.execute("SELECT * FROM buckets ORDER BY name")
            return cur.fetchall()

    # -- objects -----------------------------------------------------
    def clear_latest(self, bucket: str, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE objects SET is_latest = 0 WHERE bucket = ? AND key = ?",
                (bucket, key),
            )
            self._conn.commit()

    def insert_object_version(self, bucket, key, version_id, content_type, size,
                               checksum, created_at, is_latest=True, deleted=False,
                               storage_path=None):
        with self._lock:
            self._conn.execute(
                """INSERT INTO objects
                   (bucket, key, version_id, content_type, size, checksum,
                    created_at, is_latest, deleted, storage_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (bucket, key, version_id, content_type, size, checksum,
                 created_at, int(is_latest), int(deleted), storage_path),
            )
            self._conn.commit()

    def delete_object_row(self, bucket: str, key: str, version_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM objects WHERE bucket = ? AND key = ? AND version_id = ?",
                (bucket, key, version_id),
            )
            self._conn.commit()

    def get_latest(self, bucket: str, key: str):
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM objects WHERE bucket = ? AND key = ?
                   AND is_latest = 1 ORDER BY id DESC LIMIT 1""",
                (bucket, key),
            )
            return cur.fetchone()

    def get_version(self, bucket: str, key: str, version_id: str):
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM objects WHERE bucket = ? AND key = ?
                   AND version_id = ? LIMIT 1""",
                (bucket, key, version_id),
            )
            return cur.fetchone()

    def list_objects(self, bucket: str, prefix: str = ""):
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM objects WHERE bucket = ? AND is_latest = 1
                   AND deleted = 0 AND key LIKE ? ESCAPE '\\' ORDER BY key""",
                (bucket, _like_prefix(prefix)),
            )
            return cur.fetchall()

    def list_versions(self, bucket: str, key: str):
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM objects WHERE bucket = ? AND key = ?
                   ORDER BY id DESC""",
                (bucket, key),
            )
            return cur.fetchall()


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"
