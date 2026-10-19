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
    assert body["service"] == "service-c"
    assert body["message"] == "hello"


def test_list_items_default(client):
    body = client.get("/items").get_json()
    assert body["count"] == 5
    assert len(body["items"]) == 5


def test_list_items_limit(client):
    body = client.get("/items?limit=2").get_json()
    assert body["count"] == 2


def test_list_items_filter(client):
    body = client.get("/items?q=MO").get_json()
    assert {item["name"] for item in body["items"]} == {"mouse", "monitor"}


@pytest.mark.parametrize("limit", ["0", "101", "abc", "-3"])
def test_list_items_invalid_limit(client, limit):
    resp = client.get(f"/items?limit={limit}")
    assert resp.status_code == 400
    assert resp.get_json()["status"] == 400


def test_get_item(client):
    body = client.get("/items/3").get_json()
    assert body["item"]["name"] == "monitor"


def test_get_missing_item_is_404(client):
    resp = client.get("/items/999")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Not Found"


def test_non_integer_item_id_is_404(client):
    assert client.get("/items/abc").status_code == 404


def test_request_id_generated_when_missing(client):
    resp = client.get("/hello")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Request-ID"])
    assert resp.get_json()["request_id"] == resp.headers["X-Request-ID"]


def test_valid_request_id_is_preserved(client):
    resp = client.get("/items", headers={"X-Request-ID": "abc-123.X_y"})
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
    assert resp.headers["X-Request-ID"]


def test_wrong_method_is_json_405(client):
    resp = client.post("/items")
    assert resp.status_code == 405
    assert resp.get_json()["status"] == 405


def test_log_has_request_id_and_no_query_string(client, caplog):
    with caplog.at_level("INFO", logger=SERVICE_NAME):
        client.get("/items?q=secret-token", headers={"X-Request-ID": "trace-3"})
    entries = [json.loads(r.getMessage()) for r in caplog.records if r.name == SERVICE_NAME]
    entry = entries[-1]
    assert entry["request_id"] == "trace-3"
    assert entry["path"] == "/items"
    assert "secret-token" not in json.dumps(entry)
