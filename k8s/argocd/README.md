# Argo CD: GitOps deployment of the API Gateway

Argo CD (T-020) watches this repository and keeps the cluster in sync with `charts/api-gateway` on `main`.

```text
merge to main ──> Argo CD (polls Git every ~3 min) ──> renders charts/api-gateway ──> applies to namespace api-gateway
                         ^                                                              |
                         └──────────── self-heal: manual changes in the cluster are reverted
```

| File | What it does |
|---|---|
| `../bootstrap/argocd.yaml` | Installs Argo CD `10.20.0` (app `v3.6.3`) with the k3s Helm controller; UI on `argocd.api-gateway.pp.ua` behind Traefik + Let's Encrypt; Dex and notifications disabled |
| `api-gateway.yaml` | `Application` for `charts/api-gateway` (branch `main`, namespace `api-gateway`), automated sync with `prune` and `selfHeal` |
| `prometheus.yaml` | `Application` for the community `prometheus` chart `31.0.0` in namespace `monitoring` (T-022), with alerting rules (T-041), see [Monitoring](#monitoring-prometheus) |
| `grafana.yaml` | `Application` for the community `grafana` chart `14.6.1` (Grafana 13.3.2) + dashboards from `k8s/monitoring/dashboards` (T-023), see [Monitoring: Grafana](#monitoring-grafana) |

The repository is public, so Argo CD needs no Git credentials.

## Install

Requirements: cert-manager and the ClusterIssuers (`k8s/bootstrap/`), DNS `A argocd → <VM public IP>` at the registrar,
and `charts/api-gateway` merged into `main`.

```bash
VM=azureuser@$(terraform -chdir=terraform output -raw server_public_ip)
k() { ssh "$VM" sudo k3s kubectl "$@"; }

# 1. Argo CD
k apply -f - < k8s/bootstrap/argocd.yaml
k -n argocd wait --for=condition=Available deploy --all --timeout=300s
k -n argocd get certificate                       # argocd-server-tls READY=True (needs DNS)

# 2. If the chart was installed with helm before: hand it over to Argo CD without downtime.
#    Only Helm's release record is deleted; the running resources stay and Argo CD adopts them.
k -n api-gateway delete secret -l owner=helm,name=api-gateway

# 3. Application
k apply -f - < k8s/argocd/api-gateway.yaml
k -n argocd get application api-gateway           # SYNC STATUS Synced, HEALTH Healthy
```

## Web UI

`https://argocd.api-gateway.pp.ua`, user `admin`. The initial password is generated at install:

```bash
k -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

The login page is public. After the first login change the password (User Info → Update Password),
then delete the initial secret: `k -n argocd delete secret argocd-initial-admin-secret`.

## How to deploy a change

1. Change the chart (e.g. `charts/api-gateway/values.yaml`) in a task branch → PR into `dev` → PR `dev` → `main`.
2. Within about 3 minutes Argo CD syncs the new state (or press **Refresh** in the UI).
3. Check: `k -n argocd get application api-gateway` and `k -n api-gateway get pods`.

Do not change resources in `api-gateway` with `kubectl`/`helm`: self-heal reverts them. Change Git instead.

## Monitoring: Prometheus

`prometheus.yaml` installs the upstream chart directly from `prometheus-community`; the values live in the Application
(`valuesObject`), so they are versioned in Git. Apply it once: `k apply -f - < k8s/argocd/prometheus.yaml`.

| Component | Provides |
|---|---|
| `prometheus-server` | 7 days retention (max 4 GB) on a 5 Gi `local-path` volume; no Alertmanager, no Pushgateway |
| kubelet / cAdvisor (`kubernetes-nodes-cadvisor`) | CPU and RAM per container: `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes` |
| `kube-state-metrics` | Pod restarts and state: `kube_pod_container_status_restarts_total`, `kube_pod_container_status_ready` |
| `node-exporter` | VM CPU, RAM, disk |
| Pods with `prometheus.io/scrape` (job `kubernetes-pods`) | Traefik: requests by status code and response time (`traefik_service_requests_total`, `traefik_service_request_duration_seconds_bucket`); NGINX exporter sidecar of the gateway: `nginx_up`, connections, requests; backends (port 9100): `http_requests_total`, `http_request_duration_seconds_bucket` by `method`, `route`, `status`, service in the label `app_kubernetes_io_name` |

Useful queries:

```promql
# CPU (cores) and RAM per pod in api-gateway
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="api-gateway", container!=""}[5m]))
sum by (pod) (container_memory_working_set_bytes{namespace="api-gateway", container!=""})

# p95 response time through Traefik, and 429 responses per second
histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{service=~"api-gateway-gateway.*"}[5m])))
sum(rate(traefik_service_requests_total{service=~"api-gateway-gateway.*", code="429"}[5m]))

# Restarts in the last hour, and crash-looping containers
increase(kube_pod_container_status_restarts_total[1h]) > 0
increase(kube_pod_container_status_restarts_total[15m]) > 2 and on (namespace, pod, container) kube_pod_container_status_ready == 0
```

The last query is used for CrashLoopBackOff because on this cluster (Kubernetes 1.37) a container in back-off stays in
the `terminated` state: `kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}` is not produced
(checked with a test pod that exits with code 1).

The Prometheus UI is not public (it has no login). Open it through an SSH tunnel:

```bash
VM=azureuser@$(terraform -chdir=terraform output -raw server_public_ip)
IP=$(ssh "$VM" sudo k3s kubectl -n monitoring get svc prometheus-server -o jsonpath='{.spec.clusterIP}')
ssh -N -L 9090:$IP:80 "$VM"        # then open http://localhost:9090 (Status → Targets)
```

## Monitoring: alerting rules

Prometheus alerting rules (T-041) are in `prometheus.yaml` → `serverFiles."alerting_rules.yml"`. There is no Alertmanager,
so alerts are not sent anywhere: they are visible in the Prometheus UI (**Alerts**), in Grafana (**Alerting → Alert rules**,
data source-managed) and in the *Firing and pending alerts* table on the dashboard.

| Alert | Condition | For | Severity |
|---|---|---|---|
| `ContainerCrashLooping` | more than 2 restarts in 15 min and container not ready | 2m | critical |
| `PodNotReady` | running pod not ready | 5m | warning |
| `TargetDown` | scrape target `up == 0` | 5m | warning |
| `NodeMemoryHigh` | node memory used > 90 % | 10m | warning |
| `NodeDiskAlmostFull` | root disk used > 85 % | 10m | warning |
| `GatewayHigh5xxRatio` | more than 5 % of gateway responses are 5xx | 5m | critical |
| `GatewayRateLimiting` | more than 1 response/s with 429 | 2m | info |
| `GatewayHighLatency` | gateway p95 > 1 s | 10m | warning |

Check the rules before a change (from the running Prometheus pod; the rules file is rendered by `helm template`):

```bash
k -n monitoring exec -i deploy/prometheus-server -c prometheus-server -- \
  sh -c 'cat > /data/rules-check.yml && promtool check rules /data/rules-check.yml; rm -f /data/rules-check.yml' < alerting_rules.yml
```

Tested on 2026-11-28: a crashing test pod fired `ContainerCrashLooping` and `PodNotReady`; k6 at 20 req/s for 4 minutes
fired `GatewayRateLimiting` (about 15 × 429/s) and moved `GatewayHighLatency` to `pending` (p95 about 1.02 s, below the 10 min `for`).

A closed-loop client (each request waits for the previous ones) does not trigger 429 at all: the gateway delays the
extra requests (`burst=10 delay=5`) and the client slows down to the limit by itself. Rate-limit tests need a fixed
arrival rate (k6 `constant-arrival-rate`).

## Monitoring: Grafana

`grafana.yaml` has two sources: the `grafana-community` chart (values in `valuesObject`) and
`k8s/monitoring/dashboards` from this repository (branch `main`). Kustomize packs the dashboard JSON into the
ConfigMap `grafana-dashboards-api-gateway` with the label `grafana_dashboard`, and the Grafana sidecar loads it.
The Prometheus datasource (uid `prometheus`) is provisioned from the values. No persistent volume: everything comes from Git.

UI: `https://grafana.api-gateway.pp.ua` (Let's Encrypt), dashboard **API Gateway overview**. Anonymous access and sign-up are off.

The admin password is not in Git. Create the Secret once, before the first sync:

```bash
k -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -base64 24)"
k -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo   # show it
```

To change the password, update the Secret and restart Grafana: `k -n monitoring rollout restart deploy/grafana`.

Dashboard panels (namespace variable, default `api-gateway`):

| Panel | Query source |
|---|---|
| Crash-looping containers, Restarts (1h), Pods not ready | kube-state-metrics |
| CPU by pod, Memory by pod | cAdvisor |
| Gateway requests/s, p95, response time p50/p95/p99 | Traefik histogram (`api-gateway-gateway` service) |
| Requests by status code, 429 responses/s | Traefik counters |
| Container restarts (1h), Crash-looping containers table | kube-state-metrics |
| Service requests/s by status, Service p95 by route | Backend metrics (`http_requests_total`, `http_request_duration_seconds`), `/health` excluded |
| Firing and pending alerts | `ALERTS` from the Prometheus alerting rules (T-041) |

To change the dashboard: edit it in the UI, export JSON (Share → Export), replace `k8s/monitoring/dashboards/api-gateway.json`,
merge into `main`. Changes made only in the UI are lost when the pod restarts.

## Known limitations

- Argo CD polls Git (no webhook), so a deploy takes up to about 3 minutes.
- Argo CD itself and `k8s/bootstrap/` are applied manually; they are not managed by Argo CD (no app-of-apps).
- The UI is reachable from the Internet; protection is the admin password only.
- The `argocd` DNS record is not in `terraform output dns_records`: adding it to the `subdomains` variable would
  change the VM cloud-init and recreate the VM.
- Image tag (T-021): the Application sets `image.tag=sha-$ARGOCD_APP_REVISION_SHORT`, so every commit on `main` deploys
  the images that `images.yml` built from it. If Argo CD syncs before the images are pushed (~1–2 min), the new pods wait
  in `ImagePullBackOff` and the old pods keep serving; the rollout finishes when the images exist. If `images.yml` fails,
  the rollout stays stuck (alert `PodNotReady` after 5 min). Every commit on `main` (also docs-only) restarts the backends.
- Temporary parameters (service-d for tests): append with a JSON patch and restore with `kubectl apply -f k8s/argocd/api-gateway.yaml`,
  see [charts/api-gateway/README.md](../../charts/api-gateway/README.md).
- Response time comes from the default Traefik histogram buckets (0.1 s, 0.3 s, 1.2 s, 5 s), so percentiles are rough estimates.
- The first request with a new status code (e.g. the first 429 after a Traefik restart) creates a new counter series;
  `rate()`/`increase()` cannot count that first value, so it is missing from the graphs.
- Grafana is public and protected by the admin password only.
