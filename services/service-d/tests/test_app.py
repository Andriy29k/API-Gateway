import asyncio
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import SERVICE_NAME, create_app


@pytest.fixture
def client():
    # raise_server_exceptions=False: unhandled errors become JSON 500, like in production.
    return TestClient(create_app(max_slow_seconds=2), raise_server_exceptions=False)


@pytest.fixture
def slept(monkeypatch):
    """Replace asyncio.sleep in the app, so tests do not wait; returns the requested durations."""
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr("app.asyncio.sleep", fake_sleep)
    return calls


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": SERVICE_NAME}


def test_hello_identifies_service(client):
    body = client.get("/hello").json()
    assert body["service"] == "service-d"
    assert body["message"] == "hello"


def test_head_request_is_allowed(client):
    resp = client.head("/hello")
    assert resp.status_code == 200
    assert resp.content == b""


def test_slow_sleeps_requested_time(client, slept):
    body = client.get("/slow?seconds=1.5").json()
    assert slept == [1.5]
    assert body["slept_seconds"] == 1.5


def test_slow_default_is_one_second(client, slept):
    client.get("/slow")
    assert slept == [1.0]


@pytest.mark.parametrize("seconds", ["0", "2"])
def test_slow_bounds_are_valid(client, slept, seconds):
    assert client.get(f"/slow?seconds={seconds}").status_code == 200


@pytest.mark.parametrize("seconds", ["abc", "-1", "3", "2.01", "1e9", "nan", "inf", ""])
def test_slow_rejects_invalid_or_excessive_values(client, slept, seconds):
    resp = client.get(f"/slow?seconds={seconds}")
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("query.seconds:")
    assert slept == []


def test_max_slow_seconds_from_environment(monkeypatch):
    monkeypatch.setenv("MAX_SLOW_SECONDS", "0.5")
    client = TestClient(create_app())
    assert client.get("/slow?seconds=0.6").status_code == 400


def test_slow_does_not_block_other_requests():
    # The real asyncio.sleep: two 0.3 s requests in parallel take about 0.3 s, not 0.6 s.
    app = create_app(max_slow_seconds=2)

    async def run():
        import httpx2

        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            start = time.perf_counter()
            responses = await asyncio.gather(*(client.get("/slow?seconds=0.3") for _ in range(2)))
            return time.perf_counter() - start, responses

    elapsed, responses = asyncio.run(run())
    assert [r.status_code for r in responses] == [200, 200]
    assert elapsed < 0.55


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
def test_error_returns_requested_5xx(client, status):
    resp = client.get(f"/error?status={status}")
    assert resp.status_code == status
    assert resp.json()["status"] == status
    assert resp.json()["error"] == "Injected failure"


def test_error_default_is_500(client):
    assert client.get("/error").status_code == 500


@pytest.mark.parametrize("status", ["200", "404", "499", "600", "abc", "-500", "500.5", ""])
def test_error_rejects_non_5xx(client, status):
    resp = client.get(f"/error?status={status}")
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("query.status:")


def test_unhandled_error_is_json_500_without_details(client):
    @client.app.get("/boom")
    async def boom():
        raise RuntimeError("internal-secret-detail")

    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"error": "Internal Server Error", "status": 500, "request_id": resp.headers["X-Request-ID"]}
    assert "internal-secret-detail" not in resp.text


def test_request_id_generated_when_missing(client):
    resp = client.get("/hello")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]


def test_valid_request_id_is_preserved(client):
    resp = client.get("/error?status=503", headers={"X-Request-ID": "abc-123.X_y"})
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


def test_wrong_method_is_json_405(client):
    resp = client.post("/slow")
    assert resp.status_code == 405
    assert resp.json()["status"] == 405
    assert "GET" in resp.headers["Allow"]


def test_body_over_limit_is_json_413():
    client = TestClient(create_app(max_body_bytes=10))
    resp = client.post("/error", content=b"x" * 11)
    assert resp.status_code == 413
    assert resp.json()["status"] == 413


def test_openapi_documents_bounds(client):
    spec = client.get("/openapi.json").json()
    seconds = spec["paths"]["/slow"]["get"]["parameters"][0]["schema"]
    status = spec["paths"]["/error"]["get"]["parameters"][0]["schema"]
    assert (seconds["minimum"], seconds["maximum"]) == (0, 2)
    assert (status["minimum"], status["maximum"]) == (500, 599)
    assert "'/openapi.json'" not in client.get("/docs").text  # relative link, works behind the gateway


def test_log_has_request_id_and_no_query_string(client, caplog):
    with caplog.at_level("INFO", logger=SERVICE_NAME):
        client.get("/error?status=503&token=secret-token", headers={"X-Request-ID": "trace-4"})
    entries = [json.loads(r.getMessage()) for r in caplog.records if r.name == SERVICE_NAME]
    entry = entries[-1]
    assert entry["request_id"] == "trace-4"
    assert entry["path"] == "/error"
    assert entry["status"] == 503
    assert "secret-token" not in json.dumps(entry)
