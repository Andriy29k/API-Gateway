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
    assert body["service"] == "service-a"
    assert body["message"] == "hello"
    assert body["name"] is None


def test_hello_passes_query_parameter(client):
    assert client.get("/hello?name=Ann").get_json()["name"] == "Ann"


def test_hello_truncates_long_name(client):
    assert len(client.get("/hello?name=" + "x" * 500).get_json()["name"]) == 50


def test_request_id_generated_when_missing(client):
    resp = client.get("/hello")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])
    assert resp.get_json()["request_id"] == resp.headers["X-Request-ID"]


def test_valid_request_id_is_preserved(client):
    resp = client.get("/hello", headers={"X-Request-ID": "abc-123.X_y"})
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
    body = resp.get_json()
    assert body["error"] == "Not Found"
    assert "Traceback" not in resp.get_data(as_text=True)
    assert resp.headers["X-Request-ID"]


def test_wrong_method_is_json_405(client):
    resp = client.post("/hello")
    assert resp.status_code == 405
    assert resp.get_json()["status"] == 405


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
