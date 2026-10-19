import json
import re

import pytest

from app import SERVICE_NAME, create_app


@pytest.fixture
def client():
    return create_app({"TESTING": True, "MAX_SLOW_SECONDS": 2}).test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "service": SERVICE_NAME}


def test_hello_identifies_service(client):
    body = client.get("/hello").get_json()
    assert body["service"] == "service-d"
    assert body["message"] == "hello"


def test_slow_sleeps_requested_time(client, monkeypatch):
    slept = []
    monkeypatch.setattr("app.time.sleep", slept.append)
    body = client.get("/slow?seconds=1.5").get_json()
    assert slept == [1.5]
    assert body["slept_seconds"] == 1.5


def test_slow_default_is_one_second(client, monkeypatch):
    slept = []
    monkeypatch.setattr("app.time.sleep", slept.append)
    client.get("/slow")
    assert slept == [1.0]


@pytest.mark.parametrize("seconds", ["abc", "-1", "3", "1e9"])
def test_slow_rejects_invalid_or_excessive_values(client, monkeypatch, seconds):
    slept = []
    monkeypatch.setattr("app.time.sleep", slept.append)
    resp = client.get(f"/slow?seconds={seconds}")
    assert resp.status_code == 400
    assert slept == []


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
def test_error_returns_requested_5xx(client, status):
    resp = client.get(f"/error?status={status}")
    assert resp.status_code == status
    assert resp.get_json()["status"] == status


def test_error_default_is_500(client):
    assert client.get("/error").status_code == 500


@pytest.mark.parametrize("status", ["200", "404", "499", "600", "abc", "-500"])
def test_error_rejects_non_5xx(client, status):
    assert client.get(f"/error?status={status}").status_code == 400


def test_unexpected_exception_does_not_leak_details():
    app = create_app({"TESTING": False})

    @app.get("/boom")
    def boom():
        raise RuntimeError("internal-secret-detail")

    resp = app.test_client().get("/boom")
    assert resp.status_code == 500
    assert "internal-secret-detail" not in resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "Internal Server Error"


def test_request_id_generated_when_missing(client):
    resp = client.get("/hello")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])
    assert resp.get_json()["request_id"] == resp.headers["X-Request-ID"]


def test_valid_request_id_is_preserved(client):
    resp = client.get("/error?status=503", headers={"X-Request-ID": "abc-123.X_y"})
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
    assert "Traceback" not in resp.get_data(as_text=True)


def test_log_has_request_id_and_no_query_string(client, caplog):
    with caplog.at_level("INFO", logger=SERVICE_NAME):
        client.get("/error?status=503&token=secret-token", headers={"X-Request-ID": "trace-4"})
    entries = [json.loads(r.getMessage()) for r in caplog.records if r.name == SERVICE_NAME]
    entry = entries[-1]
    assert entry["request_id"] == "trace-4"
    assert entry["path"] == "/error"
    assert entry["status"] == 503
    assert "secret-token" not in json.dumps(entry)
