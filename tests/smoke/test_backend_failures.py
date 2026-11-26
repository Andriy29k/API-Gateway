"""Backend failure scenarios, driven by the fault-injection service (service-d)."""
import pytest
import requests

SLOWER_THAN_GATEWAY_TIMEOUT = 11


@pytest.mark.parametrize("status", [500, 502, 503])
def test_backend_error_is_passed_through(base_url, status):
    resp = requests.get(f"{base_url}/api/service-d/error", params={"status": status}, timeout=10)
    assert resp.status_code == status
    assert resp.json()["error"] == "Injected failure"


def test_fast_backend_response_is_not_a_timeout(base_url):
    resp = requests.get(f"{base_url}/api/service-d/slow", params={"seconds": 0.1}, timeout=10)
    assert resp.status_code == 200
    assert resp.json()["slept_seconds"] == 0.1


def test_slow_backend_returns_json_504(base_url):
    resp = requests.get(
        f"{base_url}/api/service-d/slow",
        params={"seconds": SLOWER_THAN_GATEWAY_TIMEOUT},
        timeout=SLOWER_THAN_GATEWAY_TIMEOUT + 5,
    )
    assert resp.status_code == 504
    assert resp.headers["Content-Type"].startswith("application/json")
    body = resp.json()
    assert body["error"] == "gateway_timeout"
    assert body["request_id"] == resp.headers["X-Request-ID"]
