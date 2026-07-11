import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import database
import app as app_module


def _network_disabled(*args, **kwargs):
    raise urllib.error.URLError("network disabled in tests")


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    # Every dashboard load calls out to Open-Meteo — block real network access by
    # default so the suite stays fast, deterministic, and offline-safe. Tests that
    # specifically exercise weather fetching (tests/test_weather.py) override this
    # with their own monkeypatch of urlopen to simulate success/failure.
    monkeypatch.setattr(app_module.urllib.request, "urlopen", _network_disabled)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    with app_module.app.app_context():
        database.init_db()
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def auth_client(client):
    client.post("/setup", data={"username": "admin", "password": "secret123", "confirm_password": "secret123"})
    client.post("/login", data={"username": "admin", "password": "secret123"})
    return client
