import json
import urllib.error

import app as app_module


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


FAKE_OPEN_METEO_PAYLOAD = {
    "daily": {
        "time": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06", "2026-01-07"],
        "weathercode": [61, 2, 0, 3, 95, 51, 1],
        "temperature_2m_max": [29.5, 30.1, 31.0, 29.8, 28.2, 29.0, 30.5],
        "temperature_2m_min": [24.1, 24.5, 25.0, 24.3, 23.8, 24.0, 24.7],
        "precipitation_sum": [5.2, 0.0, 0.0, 1.1, 12.4, 3.0, 0.0],
        "precipitation_probability_max": [80, 10, 5, 30, 95, 60, 15],
        "windspeed_10m_max": [15.0, 12.3, 10.1, 14.4, 22.0, 16.5, 13.2],
    },
    "current_weather": {"temperature": 27.3, "windspeed": 13.1, "weathercode": 61, "time": "2026-01-01T12:00"},
}


def test_weather_forecast_page_renders_with_mocked_open_meteo(auth_client, monkeypatch):
    monkeypatch.setattr(
        app_module.urllib.request, "urlopen", lambda url, timeout=8: _FakeResponse(FAKE_OPEN_METEO_PAYLOAD)
    )
    resp = auth_client.get("/weather")
    assert resp.status_code == 200
    assert b"Galle" in resp.data
    assert b"Slight rain" in resp.data  # weathercode 61
    assert b"29.5" in resp.data


def test_weather_is_cached_between_requests(auth_client, monkeypatch):
    call_count = {"n": 0}

    def fake_urlopen(url, timeout=8):
        call_count["n"] += 1
        return _FakeResponse(FAKE_OPEN_METEO_PAYLOAD)

    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)

    auth_client.get("/weather")
    auth_client.get("/weather")
    assert call_count["n"] == 1  # second request served from the weather_cache table


def test_weather_falls_back_to_cache_on_network_error(auth_client, monkeypatch):
    monkeypatch.setattr(
        app_module.urllib.request, "urlopen", lambda url, timeout=8: _FakeResponse(FAKE_OPEN_METEO_PAYLOAD)
    )
    auth_client.get("/weather")  # populates the cache

    def raise_error(url, timeout=8):
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr(app_module.urllib.request, "urlopen", raise_error)
    # Force the cache to be treated as stale so the fallback path is exercised.
    with app_module.app.app_context():
        conn = app_module.get_connection()
        conn.execute("UPDATE weather_cache SET fetched_at = '2000-01-01 00:00:00' WHERE id = 1")
        conn.commit()

    resp = auth_client.get("/weather")
    assert resp.status_code == 200
    assert b"Galle" in resp.data  # stale cached forecast still displayed, not an error page


def test_weather_page_accessible_to_operations_role(auth_client, monkeypatch):
    monkeypatch.setattr(
        app_module.urllib.request, "urlopen", lambda url, timeout=8: _FakeResponse(FAKE_OPEN_METEO_PAYLOAD)
    )
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})
    resp = ops_client.get("/weather")
    assert resp.status_code == 200
