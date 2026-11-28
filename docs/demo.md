# Demo scenario

About 20 minutes. Commands are run with `scripts/demo.sh <step>`: it prints each command, waits for Enter and runs it.
`NO_PAUSE=1` runs a step without waiting (rehearsal).

## Before the demo

| When | Check |
|---|---|
| Day before | Rehearse: `NO_PAUSE=1 bash scripts/demo.sh all` (about 5 minutes, needs k6). |
| Day before | Change the Argo CD admin password (the login page is public) and know the Grafana password ([k8s/argocd/README.md](../k8s/argocd/README.md)). |
| Day before | Prepare the GitOps change (step 6): on `dev`, set `services.service-b.replicas: 2` in `charts/api-gateway/values.yaml`; open the PR `dev` → `main`, do not merge. |
| 30 min before | Start the VM if it is stopped: `az vm start -g rg-api-gateway -n vm-k3s-server`. Wait about 2 minutes. |
| 30 min before | Check your public IP is still `admin_source_cidr` (SSH and step 4 need it): `curl -s https://api.ipify.org`. |
| 30 min before | `bash scripts/demo.sh status`: all Applications `Synced Healthy`, all pods `Running`. |
| 10 min before | Open tabs: README on GitHub, GitHub Actions, the prepared PR, Argo CD, Grafana dashboard *API Gateway overview* (last 15 min, refresh 10s), a terminal in the repo. |

The VM is shut down at 22:00 (Kyiv time). For a late demo, start it again.

## Scenario

### 1. Architecture (2 min)

README on GitHub: the two diagrams. Request path: client → Traefik (TLS) → NGINX gateway → services.
Delivery: PR → `dev` → `main` → images in GHCR → Argo CD → k3s on Azure (Terraform).

### 2. Routing and errors (3 min) – `bash scripts/demo.sh routing`

| Command | Show |
|---|---|
| `/health` | answered by NGINX itself |
| `/api/service-a/hello?name=demo` | prefix removed; the same `X-Request-ID` in the header and in the body (tracing) |
| `/api/service-c/items?limit=2` | another service behind the same entry point |
| `/api/service-a?x=1` | `308` to the trailing-slash path, query kept |
| `/unknown` | JSON `404` from the gateway |
| `POST /api/service-b/echo` without JSON | `415` from the service, passed through unchanged |

### 3. Rate limiting and throttling (2 min) – `bash scripts/demo.sh ratelimit`

30 parallel requests: about 12–17 × `200` and the rest `429` (5 r/s + burst 10).
Then one rejected response in detail: `429`, `Retry-After: 1`, JSON body with the request ID.
Talking point: the limit is per client IP; on k3s Traefik passes the real client IP (`externalTrafficPolicy: Local`).

### 4. Backend failure and self-heal (2 min) – `bash scripts/demo.sh failure`

service-a is scaled to 0 by hand. Expected:
`502 bad_gateway` (no pod) → `504 gateway_timeout` (pod starting) → `200` after about 15 s.
Nobody restored it: Argo CD saw the difference from Git and reverted it (self-heal).

### 5. Fault injection (2 min) – `bash scripts/demo.sh faults`

service-d is enabled only for this step (Argo CD Application parameter, not in Git), then removed again by re-applying `k8s/argocd/api-gateway.yaml`.
`/error?status=503` → the service's own `503` is passed through; `/slow?seconds=12` → JSON `504` after 10 s (`proxy_read_timeout`).

### 6. CI and GitOps deploy (4 min)

1. GitHub Actions: a green `CI` run (unit tests per service, Compose smoke tests), `Images`, `Terraform`.
2. Merge the prepared PR `dev` → `main` (service-b replicas 1 → 2).
3. Argo CD: press **Refresh** on `api-gateway` (or wait up to 3 min) → the new commit is synced. The images are tagged
   with the new commit (`sha-<short>`, T-021): if Argo CD is faster than the `Images` workflow, the new pods wait in
   `ImagePullBackOff` for a minute while the old pods keep serving.
4. `bash scripts/demo.sh status`: two service-b pods.
5. After the demo, revert the change the same way (PR), not with `kubectl`.

### 7. Load test and monitoring (4 min) – `bash scripts/demo.sh load`

Start k6 and switch to Grafana while it runs (about 1.5 minutes):
*Gateway requests by status code* and *429 responses/s* jump in the over-limit and burst phases,
*Gateway response time* p95 rises to about 1 s (throttled requests wait in the NGINX queue), CPU/RAM stay low,
*Restarts* and *Crash-looping containers* show step 4.
In the terminal: all k6 thresholds green (0 % 429 below the limit, > 50 % over it).

Optional: Grafana → Alerting → Alert rules: `GatewayRateLimiting` turns pending/firing during a longer load.

### 8. Questions

Useful links: [README](../README.md) (limits, error codes), [load test results](../load/README.md),
[architecture decisions](decisions.md), limitations in each README.

## If something goes wrong

| Problem | Fix |
|---|---|
| Endpoints do not answer after the VM start | Wait 1–2 minutes (flannel and Traefik start last); `bash scripts/demo.sh status`. |
| `ssh` hangs | Your public IP changed: update `admin_source_cidr` in `terraform.tfvars`, `terraform apply` (only the NSG changes). |
| Step 3 shows no `429` | Run it again after 3 s; a previous step may still hold the limit. |
| Step 4 never returns `200` | Check `ssh <vm> sudo k3s kubectl -n argocd get application api-gateway`; press **Sync** in Argo CD. |
| service-d is still there after step 5 | Run the last two commands of the step again (remove the parameter). |
| k6 thresholds fail | Another client is using the same IP, or a previous step was less than 10 s ago. Run again. |

Rehearsed on 2026-11-28 with `NO_PAUSE=1`: steps 2, 3, 4, 5 and 7 gave the results described above.
Step 6 (merge → Argo CD sync) was not rehearsed with this exact change, but the same flow was verified in T-020.
