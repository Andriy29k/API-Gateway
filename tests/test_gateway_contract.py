import os
import time

import requests

BASE_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")


def test_health():
    response = requests.get(f"{BASE_URL}/health", timeout=3)
    assert response.status_code == 200
    assert response.json()["service"] == "api-gateway"


def test_catalog_route():
    response = requests.get(f"{BASE_URL}/api/catalog/items", timeout=3)
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert response.headers.get("X-Request-ID")


def test_notes_route():
    response = requests.get(f"{BASE_URL}/api/notes", timeout=3)
    assert response.status_code == 200
    assert "notes" in response.json()


def test_unknown_route():
    response = requests.get(f"{BASE_URL}/not-a-route", timeout=3)
    assert response.status_code == 404
