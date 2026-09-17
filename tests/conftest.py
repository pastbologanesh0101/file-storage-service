import shutil

import pytest

from app import create_app
from app.clock import FixedClock


@pytest.fixture
def clock():
    return FixedClock(start=1_700_000_000.0)


@pytest.fixture
def app(clock):
    application = create_app(testing=True, clock=clock, secret_key="test-secret")
    storage_root = application.config["STORAGE_ROOT"]
    yield application
    shutil.rmtree(storage_root, ignore_errors=True)


@pytest.fixture
def client(app):
    return app.test_client()
