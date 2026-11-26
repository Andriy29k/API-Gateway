"""HTTP methods, request headers and request bodies forwarded through the gateway."""
import pytest
import requests

TIMEOUT = 10

# Limits: gateway client_max_body_size is 1 MB (nginx.conf), backend MAX_BODY_BYTES is 64 KB.
BACKEND_TOO_LARGE = 100 * 1024
GATEWAY_TOO_LARGE = 2 * 1024 * 1024


def inspect(base_url, method="GET", **kwargs):
    return requests.request(method, f"{base_url}/api/service-b/inspect", timeout=TIMEOUT, **kwargs)


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_method_is_forwarded(base_url, method):
    resp = inspect(base_url, method)
    assert resp.status_code == 200
    assert resp.json()["method"] == method


def test_head_request_has_no_body(base_url):
    resp = requests.head(f"{base_url}/api/service-b/hello", timeout=TIMEOUT)
    assert resp.status_code == 200
    assert resp.content == b""


def test_unsupported_method_returns_json_405(base_url):
    resp = requests.post(f"{base_url}/api/service-a/hello", timeout=TIMEOUT)
    assert resp.status_code == 405
    assert resp.json()["status"] == 405


def test_custom_header_is_forwarded(base_url):
    resp = inspect(base_url, headers={"X-Custom-Header": "smoke-test"})
    assert resp.json()["headers"]["X-Custom-Header"] == "smoke-test"


def test_gateway_sets_forwarding_headers(base_url):
    resp = inspect(base_url)
    headers = resp.json()["headers"]
    assert headers["X-Real-IP"]
    assert headers["X-Forwarded-For"] == headers["X-Real-IP"]
    assert headers["X-Forwarded-Proto"] == "http"
    assert headers["Host"]


def test_client_cannot_spoof_forwarded_for(base_url):
    resp = inspect(base_url, headers={"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "1.2.3.4"})
    headers = resp.json()["headers"]
    assert headers["X-Forwarded-For"] != "1.2.3.4"
    assert headers["X-Real-IP"] != "1.2.3.4"


def test_backend_receives_gateway_request_id(base_url):
    resp = inspect(base_url)
    assert resp.json()["headers"]["X-Request-ID"] == resp.headers["X-Request-ID"]


def test_binary_body_size_is_preserved(base_url):
    body = bytes(range(256)) * 40
    resp = inspect(base_url, "PUT", data=body, headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 200
    assert resp.json()["body_bytes"] == len(body)


def test_json_body_round_trip(base_url):
    payload = {"text": "привіт", "nested": {"list": [1, 2, 3]}, "flag": True}
    resp = requests.post(f"{base_url}/api/service-b/echo", json=payload, timeout=TIMEOUT)
    assert resp.status_code == 200
    assert resp.json()["received"] == payload


def test_wrong_content_type_returns_415(base_url):
    resp = requests.post(
        f"{base_url}/api/service-b/echo",
        data="plain text",
        headers={"Content-Type": "text/plain"},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 415
    assert resp.json()["status"] == 415


def test_invalid_json_returns_400(base_url):
    resp = requests.post(
        f"{base_url}/api/service-b/echo",
        data="{not json",
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 400
    assert resp.json()["status"] == 400


def test_body_over_backend_limit_returns_backend_413(base_url):
    resp = inspect(base_url, "POST", data=b"x" * BACKEND_TOO_LARGE)
    assert resp.status_code == 413
    body = resp.json()
    # Backend (Flask) error format, not the gateway one: the gateway let the body through.
    assert body["status"] == 413
    assert body["error"] != "payload_too_large"


def test_body_over_gateway_limit_returns_gateway_json_413(base_url):
    resp = inspect(base_url, "POST", data=b"x" * GATEWAY_TOO_LARGE)
    assert resp.status_code == 413
    body = resp.json()
    assert body["error"] == "payload_too_large"
    assert body["request_id"] == resp.headers["X-Request-ID"]
