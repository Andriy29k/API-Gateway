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
    assert body["service"] == "service-a"
    assert body["message"] == "hello"
    assert body["name"] is None


def test_hello_passes_query_parameter(client):
    assert client.get("/hello?name=Ann").json()["name"] == "Ann"


def test_hello_truncates_long_name(client):
    assert len(client.get("/hello?name=" + "x" * 500).json()["name"]) == 50


def test_head_request_is_allowed(client):
    resp = client.head("/hello")
    assert resp.status_code == 200
    assert resp.content == b""


def test_request_id_generated_when_missing(client):
    resp = client.get("/hello")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]


def test_valid_request_id_is_preserved(client):
    resp = client.get("/hello", headers={"X-Request-ID": "abc-123.X_y"})
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
    body = resp.json()
    assert body == {"error": "Not Found", "status": 404, "request_id": resp.headers["X-Request-ID"]}
    assert "Traceback" not in resp.text


def test_wrong_method_is_json_405(client):
    resp = client.post("/hello")
    assert resp.status_code == 405
    assert resp.json()["error"] == "Method Not Allowed"
    assert resp.json()["status"] == 405
    assert "GET" in resp.headers["Allow"]


def test_body_over_limit_is_json_413():
    client = TestClient(create_app(max_body_bytes=10))
    resp = client.post("/hello", content=b"x" * 11)
    assert resp.status_code == 413
    assert resp.json()["error"] == "Request Entity Too Large"
    assert resp.json()["status"] == 413


def test_unhandled_error_is_json_500_without_details(client):
    app = client.app

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail")

    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"error": "Internal Server Error", "status": 500, "request_id": resp.headers["X-Request-ID"]}
    assert "secret internal detail" not in resp.text


def test_openapi_and_docs_are_available(client):
    assert client.get("/openapi.json").json()["info"]["title"] == SERVICE_NAME
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "openapi.json" in docs.text
    assert "'/openapi.json'" not in docs.text  # relative link, works behind the gateway prefix


def test_log_has_request_id_and_no_query_string(client, caplog):
    with caplog.at_level("INFO", logger=SERVICE_NAME):
        client.get("/hello?name=secret-token", headers={"X-Request-ID": "trace-1"})
    entries = [json.loads(r.getMessage()) for r in caplog.records if r.name == SERVICE_NAME]
    assert entries, "expected an access log entry"
    entry = entries[-1]
    assert entry["request_id"] == "trace-1"
    assert entry["path"] == "/hello"
    assert entry["status"] == 200
    assert "duration_ms" in entry
    assert "secret-token" not in json.dumps(entry)


def _sample(client, name, labels):
    return client.app.state.metrics_registry.get_sample_value(name, labels)


def test_metrics_use_route_template(client):
    client.get("/hello?name=x")
    labels = {"method": "GET", "route": "/hello", "status": "200"}
    assert _sample(client, "http_requests_total", labels) == 1.0
    assert _sample(client, "http_request_duration_seconds_count", {"method": "GET", "route": "/hello"}) == 1.0


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
