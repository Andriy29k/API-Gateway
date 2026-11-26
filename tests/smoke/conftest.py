import os
import time

import pytest
import requests

BASE_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")

# The gateway allows 5 requests/s per client (burst 10), shared by all API routes.
# A short pause before each test keeps the suite under that limit.
PACE_SECONDS = float(os.environ.get("SMOKE_PACE", "0.25"))


@pytest.fixture(scope="session")
def base_url():
    try:
        requests.get(f"{BASE_URL}/health", timeout=3)
    except requests.ConnectionError:
        pytest.fail(f"Gateway is not reachable at {BASE_URL}. Start it with: docker compose up -d --build")
    return BASE_URL


@pytest.fixture(autouse=True)
def pace():
    time.sleep(PACE_SECONDS)
