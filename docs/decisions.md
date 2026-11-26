# Architecture Decisions

Short records of important technical decisions: what was decided, why, and what it means for the project.

---

# Migrate backend services from Flask to FastAPI after CI is in place

### Context

The original technical requirements name FastAPI as the backend framework. The four demo services (`services/service-a` … `service-d`) were written with Flask + gunicorn.

Current size:

| Service   | App code | Unit tests | Endpoints                                         |
|-----------|---------:|-----------:|---------------------------------------------------|
| service-a |   ~100   |    ~80     | `/health`, `/hello`                               |
| service-b |   ~160   |   ~140     | `/health`, `/hello`, `POST /echo`, `/inspect`     |
| service-c |   ~140   |   ~100     | `/health`, `/hello`, `/items`, `/items/<id>`      |
| service-d |   ~130   |   ~110     | `/health`, `/hello`, `/slow`, `/error`            |

All four services repeat the same request ID handling, JSON access logging and JSON error handling.

The main focus of the project is the NGINX gateway. The backends are demo upstreams, but they are part of what is presented and evaluated.

### Options

1. **Keep Flask.** No work, but the project does not match the requirements.
2. **Migrate now.** Matches the requirements, but CI does not exist yet, so regressions would only be found by running the tests manually.
3. **Migrate after CI (T-017).** Matches the requirements. The pytest smoke tests in `tests/smoke/` do not depend on the backend framework, so CI runs them on every migration PR and catches changes in behaviour.

### Decision

Option 3: migrate all four services to FastAPI, **after T-017 (CI pipeline)**, one service per task.

### Consequences

Benefits:
- The project follows the original requirements.
- Automatic OpenAPI documentation (`/docs`, `/openapi.json`) for every service.
- Request validation with type hints / Pydantic instead of manual parsing (`limit`, `seconds`, `status`).
- `async` endpoints: `service-d /slow` can use `asyncio.sleep` and no longer blocks a worker thread.

Costs and risks:
- Each service and its unit tests are rewritten (Flask test client → FastAPI `TestClient`, which needs `httpx`).
- New dependencies: `fastapi`, `uvicorn`, `httpx` (tests only). Dockerfile `CMD` changes from gunicorn to uvicorn.
- FastAPI returns validation errors as `422` with its own body format by default. Custom exception handlers are needed to keep the current error format.
- Shared code (request ID, logging, errors) stays duplicated per service, because each service is a separate Docker build context. Revisit only if the duplication becomes a real problem.

### API contract that must not change

The gateway, the smoke tests and the Compose/Dockerfile health checks depend on this behaviour:

- Port `8000`, `GET /health` → `200 {"status": "ok", "service": "<name>"}`.
- Same paths, methods, query parameters and success response fields as today.
- Error body format `{"error": ..., "status": ..., "request_id": ...}` for 400, 404, 405, 413, 415 and 500.
  - Invalid input returns `400`, not FastAPI's default `422`.
  - Unknown routes return `404`, wrong methods return `405`.
- `X-Request-ID`: accept a valid incoming value (`^[A-Za-z0-9._-]{1,64}$`), otherwise generate one; always return it in the response header.
- JSON access log line per request with metadata only (no query string, headers or body).
- Body limit `MAX_BODY_BYTES` (default 64 KB) → `413`.
- Non-root user and `HEALTHCHECK` in the Dockerfile.

### Follow-up tasks

| Task  | Branch                     | Scope                                               | Depends on |
|-------|----------------------------|-----------------------------------------------------|------------|
| T-027 | `027-fastapi-service-a`    | Migrate service-a; sets the pattern for the others   | T-017      |
| T-028 | `028-fastapi-service-b`    | Migrate service-b (`/echo`, `/inspect`, body limit)  | T-027      |
| T-029 | `029-fastapi-service-c`    | Migrate service-c (`/items` validation)             | T-027      |
| T-030 | `030-fastapi-service-d`    | Migrate service-d (`async` `/slow`, `/error`)        | T-027      |

Each migration task is done when the service's unit tests pass and the CI smoke tests are green without changes to `tests/smoke/`.
