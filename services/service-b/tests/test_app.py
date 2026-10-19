import json
import re

import pytest

from app import SERVICE_NAME, create_app


@pytest.fixture
def client():
    return create_app({"TESTING": True}).test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "service": SERVICE_NAME}


def test_hello_identifies_service(client):
    body = client.get("/hello").get_json()
    assert body["service"] == "service-b"
    assert body["message"] == "hello"


def test_echo_returns_json_body(client):
    payload = {"a": 1, "nested": {"b": [1, 2, 3]}, "text": "привіт"}
    resp = client.post("/echo", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["received"] == payload
    assert body["service"] == "service-b"
    assert body["content_length"] > 0


def test_echo_rejects_non_json_content_type(client):
    resp = client.post("/echo", data="plain text", content_type="text/plain")
    assert resp.status_code == 415
    assert resp.get_json()["status"] == 415


def test_echo_rejects_invalid_json(client):
    resp = client.post("/echo", data="{not json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["status"] == 400


def test_echo_rejects_oversized_body():
    client = create_app({"TESTING": True, "MAX_CONTENT_LENGTH": 100}).test_client()
    resp = client.post("/echo", json={"data": "x" * 1000})
    assert resp.status_code == 413
    assert resp.get_json()["status"] == 413


def test_echo_does_not_accept_get(client):
    assert client.get("/echo").status_code == 405


def test_request_id_generated_when_missing(client):
    resp = client.get("/hello")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])
    assert resp.get_json()["request_id"] == resp.headers["X-Request-ID"]


def test_valid_request_id_is_preserved(client):
    resp = client.post("/echo", json={"k": "v"}, headers={"X-Request-ID": "abc-123.X_y"})
    assert resp.headers["X-Request-ID"] == "abc-123.X_y"
    assert resp.get_json()["request_id"] == "abc-123.X_y"


@pytest.mark.parametrize("bad_id", ["a" * 65, "has space", "semi;colon", "new\tline"])
def test_invalid_request_id_is_replaced(client, bad_id):
    resp = client.get("/hello", headers={"X-Request-ID": bad_id})
    assert resp.headers["X-Request-ID"] != bad_id
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])


def test_unknown_route_is_json_404(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Not Found"
    assert "Traceback" not in resp.get_data(as_text=True)
    assert resp.headers["X-Request-ID"]


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
