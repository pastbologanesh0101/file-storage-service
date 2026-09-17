"""File Storage Service - a small S3-like object storage service.

Provides an application factory (`create_app`) so the app can be run
normally or instantiated in tests with an isolated temp storage root
and an isolated SQLite database.
"""
import os
import secrets
import tempfile

from flask import Flask

from .clock import Clock
from .db import Database


def create_app(testing=False, storage_root=None, db_path=None, clock=None,
                secret_key=None):
    app = Flask(__name__)
    app.config["TESTING"] = bool(testing)

    if secret_key is None:
        secret_key = "dev-secret-key-change-me" if not testing else secrets.token_hex(16)
    app.config["SECRET_KEY"] = secret_key
    app.config["PRESIGN_SECRET"] = secret_key

    if storage_root is None:
        if testing:
            storage_root = tempfile.mkdtemp(prefix="fss_test_storage_")
        else:
            storage_root = os.path.join(os.getcwd(), "storage_data")
    os.makedirs(storage_root, exist_ok=True)
    app.config["STORAGE_ROOT"] = os.path.realpath(storage_root)

    if db_path is None:
        db_path = ":memory:" if testing else os.path.join(
            app.config["STORAGE_ROOT"], "..", "metadata.sqlite3"
        )

    app.config["CLOCK"] = clock or Clock()

    database = Database(db_path)
    database.init_schema()
    app.config["DATABASE"] = database

    from .routes import bp

    app.register_blueprint(bp)

    @app.teardown_appcontext
    def _close_db(exception=None):  # noqa: ARG001
        # Database keeps a single long-lived connection (needed to
        # support ":memory:" sqlite in tests), so nothing to do here.
        pass

    return app
