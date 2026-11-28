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
  Update (T-027): Starlette 1.x uses `httpx2` for its `TestClient`, so the tests use `httpx2` instead of `httpx`.
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

### Outcome (T-027 – T-030)

All four services run on FastAPI + uvicorn; `tests/smoke/` was not changed. Differences from the Flask versions:

- The `detail` text of `400` responses is Pydantic's message (e.g. `query.limit: Input should be greater than or equal to 1`); status and body format are unchanged.
- `OPTIONS` returns `405` (Flask answered it automatically). Nothing uses it (no CORS).
- Integer query parameters accept a few more spellings (`500.0`, `+500`, `0500` for `/error?status=`); the allowed range is unchanged.
- service-d `/slow` uses `asyncio.sleep`, so slow requests no longer take up worker threads (gunicorn allowed 2 workers × 4 threads).

---

# Run a self-managed single-node k3s cluster on an Azure VM, managed with Terraform

### Context

The project needs a public Kubernetes environment for the gateway (Helm, Argo CD, monitoring, demo). Constraints:

- Azure subscription with a regional quota of **4 vCPU**.
- In the EU regions checked (westeurope, northeurope, polandcentral) the subscription is offered no 2-vCPU sizes; `southafricanorth` offers them.
- One person maintains the project; costs must stay low.
- A domain (`api-gateway.pp.ua`) with DNS hosted at the registrar (NIC.UA).

### Options

1. **AKS.** Managed control plane, but extra cost and more Azure-specific setup than the project needs.
2. **k3s on one Azure VM.** Lightweight, includes Traefik, fits the quota; the cluster is our responsibility.
3. **Local cluster only (kind / k3d).** Free, but no public endpoint, TLS or real GitOps demo.

### Decision

Option 2:

- Terraform (AzureRM) creates the resource group, VNet/subnet, NSG, static public IP and one `Standard_B2as_v2` VM (2 vCPU / 8 GB) with Ubuntu 24.04 in `southafricanorth`. An optional worker (`worker_count = 1`) still fits the 4 vCPU quota.
- k3s (pinned version) is installed on first boot by cloud-init. Traefik is the ingress controller.
- TLS: cert-manager + Let's Encrypt (HTTP-01 through Traefik), manifests in `k8s/bootstrap/`.
- DNS stays at the registrar. Terraform only outputs the `A` records; they are created manually.
- Terraform state is local (git-ignored) and backed up manually.
- CI runs only static checks (`fmt`, `validate`, `tflint`), never `plan`/`apply`.

### Consequences

Benefits:
- Low cost: about $78/month 24/7, about $37/month with the daily auto-shutdown and deallocation when idle.
- Simple, fully declarative setup that can be recreated with `terraform apply`.

Costs and risks:
- Single node, no high availability; the cluster and its data are lost with the VM.
- Burstable CPU (about 40% baseline): long load tests can use up CPU credits.
- About 200 ms latency from Ukraine to `southafricanorth`.
- Local state is a single copy and contains the k3s join token. Option for later: a remote backend in the existing storage account.
- SSH and the Kubernetes API are open only to one admin IP; when it changes, `admin_source_cidr` must be updated and applied.
- Changing cloud-init inputs (`k3s_version`, `domain_name`, `subdomains`) recreates the VM.
