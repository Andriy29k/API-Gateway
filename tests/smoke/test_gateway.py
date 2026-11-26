"""Smoke tests for a running gateway (docker compose up -d --build)."""
import pytest
import requests

TIMEOUT = 10


def get(url, **kwargs):
    return requests.get(url, timeout=TIMEOUT, **kwargs)


def test_gateway_health(base_url):
    resp = get(f"{base_url}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "api-gateway"


@pytest.mark.parametrize("service", ["service-a", "service-b", "service-c", "service-d"])
def test_service_hello_is_routed(base_url, service):
    resp = get(f"{base_url}/api/{service}/hello")
    assert resp.status_code == 200
    assert resp.json()["service"] == service


def test_query_string_is_forwarded(base_url):
    resp = get(f"{base_url}/api/service-a/hello", params={"name": "Ann"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Ann"


def test_post_json_body_is_forwarded(base_url):
    resp = requests.post(f"{base_url}/api/service-b/echo", json={"k": "v"}, timeout=TIMEOUT)
    assert resp.status_code == 200
    assert resp.json()["received"] == {"k": "v"}


def test_service_c_list_with_limit(base_url):
    resp = get(f"{base_url}/api/service-c/items", params={"limit": 2})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_service_c_single_item(base_url):
    resp = get(f"{base_url}/api/service-c/items/3")
    assert resp.status_code == 200
    assert resp.json()["item"]["name"] == "monitor"


def test_upstream_404_is_passed_through(base_url):
    resp = get(f"{base_url}/api/service-c/items/999")
    assert resp.status_code == 404
    assert resp.json()["error"] == "Not Found"


def test_upstream_400_is_passed_through(base_url):
    resp = get(f"{base_url}/api/service-c/items", params={"limit": "abc"})
    assert resp.status_code == 400
    assert resp.json()["status"] == 400


@pytest.mark.parametrize("path", ["/nope", "/api/notes", "/api/catalog/items"])
def test_unknown_path_returns_gateway_json_404(base_url, path):
    resp = get(f"{base_url}{path}")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_missing_slash_redirects_and_keeps_query(base_url):
    resp = get(f"{base_url}/api/service-a", params={"x": "1"}, allow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["Location"] == "/api/service-a/?x=1"


def test_exactly_one_request_id_header(base_url):
    resp = get(f"{base_url}/api/service-a/hello")
    # requests joins repeated headers into one string, so read the raw header list.
    assert len(resp.raw.headers.getlist("X-Request-ID")) == 1


def test_request_id_reaches_backend(base_url):
    resp = get(f"{base_url}/api/service-a/hello")
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]
