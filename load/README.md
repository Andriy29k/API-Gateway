# Load tests (k6)

`rate-limit.js` checks the gateway rate limiting and throttling under load (T-024).

Gateway limits: **5 requests/s per client IP**, `burst=10 delay=5` (5 extra requests are served at once,
the next 5 are delayed, the rest get `429`), `limit_conn` 20 concurrent requests per IP.

| Scenario | Load | Expectation (threshold) |
|---|---|---|
| `normal` | 3 req/s for 30 s (below the limit) | 429 share < 1 %, p95 < 1 s |
| `over_limit` | 20 req/s for 30 s (4× the limit) | 429 share > 50 %, accepted requests p95 < 3 s |
| `burst` | 30 requests at the same moment | 429 share between 30 % and 90 %, accepted requests p95 < 3 s |

Every response must be `200` or `429`; a `429` must have the JSON body `too_many_requests` and `Retry-After: 1`.
The scenarios run one after another with 10 s pauses, so the limit is free again when the next one starts. One run takes about 1.5 minutes.

## Run

Install k6: <https://grafana.com/docs/k6/latest/set-up/install-k6/>.

```bash
k6 run load/rate-limit.js                                               # Compose, http://localhost:8080
GATEWAY_URL=https://api.api-gateway.pp.ua k6 run load/rate-limit.js     # k3s
```

k6 exits with a non-zero code if a threshold fails. All requests come from your IP, so do not run
other traffic against the gateway at the same time. Watch the run in Grafana: dashboard **API Gateway overview**
(panels *429 responses/s*, *Gateway requests by status code*, *Gateway response time*).

## Results

k3s on Azure (`Standard_B2as_v2`, `southafricanorth`), k6 v2.4.0 from Ukraine (about 200 ms round trip), 2026-11-28.
Two runs gave the same numbers; the second one:

| Scenario | Requests | 429 | 429 share | Response time |
|---|---:|---:|---:|---|
| `normal` | 90 | 0 | 0 % | p95 211 ms (all requests) |
| `over_limit` | 601 | 443 | 73.7 % | accepted: median 1.17 s, p95 1.21 s |
| `burst` | 30 | 17 | 56.7 % | accepted: median 419 ms, p95 1.09 s |

- All thresholds passed, 0 unexpected status codes, all 1181 checks passed.
- `over_limit`: 158 of 601 requests were accepted, about 5.3 req/s over 30 s, which matches the 5 r/s limit plus the burst.
  Accepted requests wait about 1 s in the NGINX queue (`delay`): this is the throttling.
- `burst`: 13 of 30 requests were accepted (about 1 + burst 10; a few more because the requests do not arrive at exactly the same time).
  Accepted requests are spread from about 0.2 s (served at once) to about 1.2 s (delayed).
- `normal`: the response time is almost only the network round trip; the gateway and service-a add a few milliseconds.

Seen in Prometheus/Grafana during the runs: up to about 7.7 responses/s with `429`, gateway p95 up to about 1.06 s,
gateway pod memory about 11 MB, CPU of all pods far below 0.01 cores, no restarts.

## Limitations

- One client IP: the test checks the per-IP limit, not the capacity of the gateway (the cluster was mostly idle).
- A `429` from `limit_conn` looks the same as one from `limit_req`, so the two limits cannot be told apart in the results.
- The numbers depend on the network between the k6 machine and the cluster.
- Not run in CI: it needs a running gateway and takes about 1.5 minutes.
