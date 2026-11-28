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

## Known limitations

- Argo CD polls Git (no webhook), so a deploy takes up to about 3 minutes.
- Argo CD itself and `k8s/bootstrap/` are applied manually; they are not managed by Argo CD (no app-of-apps).
- The UI is reachable from the Internet; protection is the admin password only.
- The `argocd` DNS record is not in `terraform output dns_records`: adding it to the `subdomains` variable would
  change the VM cloud-init and recreate the VM.
- New images are rolled out only when `image.tag` in `values.yaml` changes; today by hand, automated in T-021.
