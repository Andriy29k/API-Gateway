# k8s bootstrap: TLS with cert-manager and Let's Encrypt

Cluster add-ons that are installed once on the k3s cluster created by `terraform/` (T-038).
Later they can be managed by Argo CD (T-020).

| File | What it does |
|---|---|
| `cert-manager.yaml` | `HelmChart` for cert-manager `v1.21.3` (with CRDs), installed by the k3s built-in Helm controller into the `cert-manager` namespace |
| `cluster-issuers.yaml` | `ClusterIssuer`s `letsencrypt-staging` and `letsencrypt-prod` (ACME HTTP-01 through the `traefik` ingress class) |
| `whoami.yaml` | Test app with an Ingress on `api.api-gateway.pp.ua` and a certificate from the issuer in the annotation |
| `traefik-config.yaml` | `HelmChartConfig` for the bundled Traefik: permanent HTTP → HTTPS redirect |

No private keys or Secrets are stored here: cert-manager creates them in the cluster.

## Requirements

- `api.<domain>` resolves to the VM public IP (`dig +short api.api-gateway.pp.ua`). Without this, HTTP-01 validation fails.
- Port 80 is open from the Internet (NSG rule `allow-http`).

## Apply

`kubectl` is not needed locally: the manifests are piped to `k3s kubectl` on the server.

```bash
cd k8s/bootstrap
VM=azureuser@$(terraform -chdir=../../terraform output -raw server_public_ip)
k() { ssh "$VM" sudo k3s kubectl "$@"; }

# 1. cert-manager
k apply -f - < cert-manager.yaml
k -n cert-manager wait --for=condition=Available deploy --all --timeout=300s

# 2. Issuers
k apply -f - < cluster-issuers.yaml
k get clusterissuer                     # both READY=True

# 3. Test app with a staging certificate (only after DNS works)
k apply -f - < whoami.yaml
k -n whoami get certificate             # whoami-tls READY=True
curl -kv https://api.api-gateway.pp.ua 2>&1 | grep -i issuer   # (STAGING) Let's Encrypt

# 4. Production: change the annotation in whoami.yaml to letsencrypt-prod, then
k -n whoami delete secret whoami-tls
k apply -f - < whoami.yaml
curl -I https://api.api-gateway.pp.ua   # trusted certificate, no -k

# 5. HTTP -> HTTPS redirect (after production works)
k apply -f - < traefik-config.yaml
curl -I http://api.api-gateway.pp.ua    # 308 to https://
```

With a local `kubectl` and `KUBECONFIG=~/.kube/api-gateway.yaml`, `kubectl apply -f <file>` works the same way.

## Troubleshooting

```bash
k -n whoami describe certificate whoami-tls
k get certificaterequest,order,challenge -A
k -n cert-manager logs deploy/cert-manager --tail=50
```

- Let's Encrypt production has rate limits (e.g. 5 failed validations per hostname per hour). Always test with staging first.
- Renewal is automatic (cert-manager renews about 30 days before expiry).
- With the redirect enabled, the HTTP-01 request is redirected to HTTPS; Let's Encrypt follows the redirect, so renewals keep working.
