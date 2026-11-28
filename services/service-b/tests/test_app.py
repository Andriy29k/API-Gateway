import json
import re
import socket
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app import SERVICE_NAME, create_app


@pytest.fixture
def client():
    # raise_server_exceptions=False: unhandled errors become JSON 500, like in production.
    return TestClient(create_app(), raise_server_exceptions=False)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": SERVICE_NAME}


def test_hello_identifies_service(client):
    body = client.get("/hello").json()
    assert body["service"] == "service-b"
    assert body["message"] == "hello"


def test_head_request_is_allowed(client):
    resp = client.head("/hello")
    assert resp.status_code == 200
    assert resp.content == b""


def test_echo_returns_json_body(client):
    payload = {"a": 1, "nested": {"b": [1, 2, 3]}, "text": "привіт"}
    resp = client.post("/echo", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] == payload
    assert body["service"] == "service-b"
    assert body["content_length"] > 0


def test_echo_accepts_vendor_json_content_type(client):
    resp = client.post("/echo", content=b'{"k": 1}', headers={"Content-Type": "application/problem+json"})
    assert resp.status_code == 200
    assert resp.json()["received"] == {"k": 1}


def test_echo_rejects_non_json_content_type(client):
    resp = client.post("/echo", content="plain text", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415
    assert resp.json()["status"] == 415
    assert resp.json()["error"] == "Unsupported Media Type"


@pytest.mark.parametrize("body", [b"{not json", b"", b"null", b"\xff\xfe"])
def test_echo_rejects_invalid_json(client, body):
    resp = client.post("/echo", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["status"] == 400
    assert resp.json()["detail"] == "Body is not valid JSON"


def test_echo_rejects_oversized_body():
    client = TestClient(create_app(max_body_bytes=100))
    resp = client.post("/echo", json={"data": "x" * 1000})
    assert resp.status_code == 413
    assert resp.json()["status"] == 413


def test_echo_rejects_oversized_chunked_body():
    # A generator body is sent without Content-Length (chunked), so the size is checked after reading.
    client = TestClient(create_app(max_body_bytes=100))
    resp = client.post("/echo", content=(b"x" * 60 for _ in range(3)), headers={"Content-Type": "application/json"})
    assert "Content-Length" not in resp.request.headers
    assert resp.status_code == 413


def test_echo_does_not_accept_get(client):
    resp = client.get("/echo")
    assert resp.status_code == 405
    assert resp.json()["status"] == 405


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_inspect_reports_method(client, method):
    resp = client.request(method, "/inspect")
    assert resp.status_code == 200
    assert resp.json()["method"] == method


def test_inspect_reports_body_size(client):
    resp = client.put("/inspect", content=b"\x00\x01" * 500, headers={"Content-Type": "application/octet-stream"})
    assert resp.json()["body_bytes"] == 1000


def test_inspect_returns_only_allowed_headers(client):
    resp = client.get(
        "/inspect",
        headers={
            "X-Custom-Header": "hello",
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Other": "not-listed",
        },
    )
    headers = resp.json()["headers"]
    assert headers["X-Custom-Header"] == "hello"
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    assert "X-Other" not in headers
    assert "secret" not in resp.text


def test_inspect_truncates_long_header_values(client):
    resp = client.get("/inspect", headers={"X-Custom-Header": "x" * 500})
    assert len(resp.json()["headers"]["X-Custom-Header"]) == 200


def test_inspect_rejects_oversized_body():
    client = TestClient(create_app(max_body_bytes=100))
    resp = client.post("/inspect", content=b"x" * 1000)
    assert resp.status_code == 413


def test_request_id_generated_when_missing(client):
    resp = client.get("/hello")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]


def test_valid_request_id_is_preserved(client):
    resp = client.post("/echo", json={"k": "v"}, headers={"X-Request-ID": "abc-123.X_y"})
    assert resp.headers["X-Request-ID"] == "abc-123.X_y"
    assert resp.json()["request_id"] == "abc-123.X_y"


@pytest.mark.parametrize("bad_id", ["a" * 65, "has space", "semi;colon", "new\tline"])
def test_invalid_request_id_is_replaced(client, bad_id):
    resp = client.get("/hello", headers={"X-Request-ID": bad_id})
    assert resp.headers["X-Request-ID"] != bad_id
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])


def test_unknown_route_is_json_404(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Not Found", "status": 404, "request_id": resp.headers["X-Request-ID"]}
    assert "Traceback" not in resp.text


def test_unhandled_error_is_json_500_without_details(client):
    @client.app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail")

    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"error": "Internal Server Error", "status": 500, "request_id": resp.headers["X-Request-ID"]}
    assert "secret internal detail" not in resp.text


def test_openapi_lists_every_inspect_method(client):
    spec = client.get("/openapi.json").json()
    assert sorted(spec["paths"]["/inspect"]) == ["delete", "get", "patch", "post", "put"]
    assert "'/openapi.json'" not in client.get("/docs").text  # relative link, works behind the gateway


def test_log_never_contains_request_body(client, caplog):
    with caplog.at_level("INFO", logger=SERVICE_NAME):
        client.post(
            "/echo?token=query-secret",
            json={"password": "body-secret"},
            headers={"X-Request-ID": "trace-2", "Authorization": "Bearer header-secret"},
        )
    entries = [json.loads(r.getMessage()) for r in caplog.records if r.name == SERVICE_NAME]
    entry = entries[-1]
    assert entry["request_id"] == "trace-2"
    assert entry["method"] == "POST"
    assert entry["path"] == "/echo"
    assert entry["status"] == 200
    logged = json.dumps(entries)
    for secret in ("body-secret", "header-secret", "query-secret"):
        assert secret not in logged


def _sample(client, name, labels):
    return client.app.state.metrics_registry.get_sample_value(name, labels)


def test_metrics_use_route_template(client):
    client.put("/inspect")
    labels = {"method": "PUT", "route": "/inspect", "status": "200"}
    assert _sample(client, "http_requests_total", labels) == 1.0
    assert _sample(client, "http_request_duration_seconds_count", {"method": "PUT", "route": "/inspect"}) == 1.0


def test_metrics_unknown_route_and_method_are_grouped(client):
    client.get("/does-not-exist/123")
    client.request("FOO", "/health")
    assert _sample(client, "http_requests_total", {"method": "GET", "route": "unmatched", "status": "404"}) == 1.0
    assert _sample(client, "http_requests_total", {"method": "OTHER", "route": "/health", "status": "405"}) == 1.0


def test_metrics_served_on_separate_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # The context manager runs the lifespan, which starts the metrics server.
    with TestClient(create_app(metrics_port=str(port))) as client:
        client.get("/health")
        text = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2).read().decode()
        assert 'http_requests_total{method="GET",route="/health",status="200"} 1.0' in text
        assert client.get("/metrics").status_code == 404  # not on the API port
