"""Rate limiting and throttling (limits are defined in gateway/nginx.conf and routes.conf)."""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

# api_limit: 5 r/s, burst=10 (5 served at once, 5 delayed). faults_limit: 2 r/s, burst=5.
API_ALLOWED_AT_ONCE = 1 + 10
FAULTS_ALLOWED_AT_ONCE = 1 + 5

# Time for the limit buckets to empty, so tests do not affect each other.
REFILL_SECONDS = 3


@pytest.fixture(autouse=True)
def refill_limits():
    time.sleep(REFILL_SECONDS)


def timed_get(url):
    start = time.perf_counter()
    resp = requests.get(url, timeout=15)
    return resp, time.perf_counter() - start


def parallel_get(url, count):
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(timed_get, [url] * count))


def statuses(results):
    return [resp.status_code for resp, _ in results]


def test_requests_over_burst_get_429(base_url):
    results = parallel_get(f"{base_url}/api/service-a/hello", 18)
    codes = statuses(results)
    assert set(codes) <= {200, 429}
    assert codes.count(429) > 0
    # Small margin: the bucket refills a little while requests are in flight.
    assert codes.count(200) <= API_ALLOWED_AT_ONCE + 2


def test_requests_within_burst_are_throttled_not_rejected(base_url):
    results = parallel_get(f"{base_url}/api/service-a/hello", 10)
    assert statuses(results) == [200] * 10
    # 4 of these requests exceed "delay=5" and are delayed by 0.2s steps (5 r/s).
    slowest = max(elapsed for _, elapsed in results)
    assert slowest >= 0.5


def test_429_response_is_json_with_retry_after(base_url):
    results = parallel_get(f"{base_url}/api/service-a/hello", 18)
    rejected = [resp for resp, _ in results if resp.status_code == 429]
    assert rejected, "expected at least one 429 response"
    resp = rejected[0]
    assert resp.headers["Content-Type"].startswith("application/json")
    assert resp.headers["Retry-After"] == "1"
    body = resp.json()
    assert body["error"] == "too_many_requests"
    assert body["request_id"] == resp.headers["X-Request-ID"]


def test_limit_recovers_after_pause(base_url):
    parallel_get(f"{base_url}/api/service-a/hello", 18)
    time.sleep(REFILL_SECONDS)
    assert requests.get(f"{base_url}/api/service-a/hello", timeout=10).status_code == 200


def test_fault_service_has_stricter_limit(base_url):
    codes = statuses(parallel_get(f"{base_url}/api/service-d/hello", 10))
    assert codes.count(429) > 0
    assert codes.count(200) <= FAULTS_ALLOWED_AT_ONCE + 1


def test_health_is_not_rate_limited(base_url):
    codes = statuses(parallel_get(f"{base_url}/health", 18))
    assert codes == [200] * 18
