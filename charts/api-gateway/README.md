# Helm chart: api-gateway

Deploys the NGINX API Gateway and the backend services to the k3s cluster (T-019).

```text
Client --HTTPS--> Traefik Ingress (TLS, cert-manager) --> gateway:8080 (NGINX) --> service-a|b|c|d:8000
```

| Resource | Description |
|---|---|
| `Deployment`/`Service` per backend | Image `<image.repository>/api-gateway-<service>:<image.tag>` from GHCR (T-018), port 8000, probes on `/health`, non-root, read-only root filesystem |
| `ConfigMap gateway-config` | `nginx.conf` + `default.conf`, the Kubernetes version of `gateway/` |
| `Deployment`/`Service gateway` | `nginx:1.28-alpine` with the ConfigMap, probes on `/health`, restarts when the config changes |
| `Ingress gateway` | Host `ingress.host`, TLS certificate from the `ingress.clusterIssuer` ClusterIssuer (`k8s/bootstrap/`) |

## Differences from Docker Compose

- Upstreams and `/api/<service>/` routes are generated from `values.yaml`. A disabled service has no route (`404`).
- **service-d (fault injection) is disabled by default.** Enable it only for tests: `--set services.service-d.enabled=true`.
- The client IP comes from the `X-Forwarded-For` header set by Traefik (`gateway.trustedProxies`, NGINX `real_ip`), so rate limits and `X-Real-IP` work per client and not per Traefik pod.
- `X-Forwarded-Proto` is passed on from Traefik (`https`), because TLS ends there.

The NGINX config exists twice (`gateway/` for Compose, the ConfigMap here). Change both and run the smoke tests against both.

## Values

| Key | Default | Description |
|---|---|---|
| `image.repository` | `ghcr.io/andriy29k` | Registry and owner of the backend images |
| `image.tag` | `latest` | Image tag for all backends (`sha-<short>` after T-021) |
| `image.pullPolicy` | `Always` | Re-pull moving tags on every rollout |
| `services.<name>.enabled` | `true` (`false` for service-d) | Deploy the service and its gateway route |
| `services.<name>.replicas` | `1` | Pods per backend |
| `services.<name>.limitReq` | `zone=api_limit burst=10 delay=5` | NGINX `limit_req` for the route (`faults_limit` for service-d) |
| `serviceResources` | 50m / 64Mi, limit 256Mi | Resources of every backend container |
| `gateway.image` | `nginx:1.28-alpine` | Same image as in Compose |
| `gateway.replicas` | `1` | Rate limit zones are per pod: more replicas multiply the limit |
| `gateway.trustedProxies` | `["10.42.0.0/16"]` | CIDRs whose `X-Forwarded-For` is trusted (k3s pod network) |
| `ingress.enabled` | `true` | Create the Ingress |
| `ingress.host` | `api.api-gateway.pp.ua` | Public host name |
| `ingress.clusterIssuer` | `letsencrypt-prod` | cert-manager ClusterIssuer |
| `ingress.tlsSecretName` | `api-gateway-tls` | Secret created by cert-manager |

## Usage

Requirements: images in GHCR with public visibility, cert-manager and ClusterIssuers from `k8s/bootstrap/`, DNS for `ingress.host`.

```bash
export KUBECONFIG=~/.kube/api-gateway.yaml

helm lint charts/api-gateway --strict
helm template api-gateway charts/api-gateway -n api-gateway     # render locally

helm upgrade --install api-gateway charts/api-gateway -n api-gateway --create-namespace --reset-values
kubectl -n api-gateway get pods,ingress,certificate

curl https://api.api-gateway.pp.ua/health
curl https://api.api-gateway.pp.ua/api/service-a/hello
```

Smoke tests against the cluster (they need service-d):

```bash
helm upgrade --install api-gateway charts/api-gateway -n api-gateway --set services.service-d.enabled=true
GATEWAY_URL=https://api.api-gateway.pp.ua python -m pytest tests/smoke -v -rx
helm upgrade --install api-gateway charts/api-gateway -n api-gateway --reset-values   # disable service-d again
```

`--reset-values` matters: without any `--set`/`-f`, `helm upgrade` reuses the values of the previous release,
so service-d would stay enabled.

Expected result: all tests pass, `test_body_over_gateway_limit_returns_gateway_json_413` is `xfail` (see below).

Remove: `helm uninstall api-gateway -n api-gateway`.

## Known limitations

- Bodies over 1 MB: NGINX answers `413` before the upload ends. Traefik sends this response and closes the connection
  without reading the rest of the body. curl shows the JSON `413`; clients that first send the whole body
  (e.g. Python `requests`) get a connection error instead. The smoke test marks this case as `xfail` over HTTPS.
- Traefik must run with `externalTrafficPolicy: Local` (`k8s/bootstrap/traefik-config.yaml`), otherwise all clients share one rate limit.

- The gateway runs the official `nginx` image, whose master process runs as root (same as Compose).
- `image.tag: latest` with `pullPolicy: Always` is not reproducible; T-021 pins `sha-<short>` tags.
- One tag for all backends.
