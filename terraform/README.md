# Terraform: Azure k3s infrastructure

Minimal Azure infrastructure for a single-node k3s cluster that hosts the API Gateway project.
Why it is built this way: [ADR "Run a self-managed single-node k3s cluster on an Azure VM"](../docs/decisions.md).
TLS and cluster add-ons: [`../k8s/bootstrap/`](../k8s/bootstrap/README.md).

## Architecture

```text
Internet
   |  HTTP :80 / HTTPS :443 (any source)      SSH :22, Kubernetes API :6443 (admin_source_cidr only)
   v
Static public IP  pip-k3s-server
   |
NSG nsg-k3s  (associated with the subnet)
   |
VNet vnet-api-gateway 10.10.0.0/16
 └─ subnet snet-k3s 10.10.1.0/24
     ├─ vm-k3s-server   Standard_B2as_v2, Ubuntu 24.04, k3s server
     │     Traefik (ingress, :80/:443) → cert-manager / Let's Encrypt → apps
     └─ vm-k3s-worker-1 (optional, worker_count = 1, private IP only)

Resource group rg-api-gateway, region southafricanorth.
DNS: A record api.<domain> → public IP, created manually at the registrar.
```

Terraform creates: resource group, VNet, subnet, NSG + subnet association, static Standard public IP, NIC, Linux VM, daily auto-shutdown schedule and a random k3s join token (10 resources without a worker, 13 with one).

### NSG inbound rules

| Priority | Name | Port | Source |
|---|---|---|---|
| 100 | `allow-ssh-admin` | 22/TCP | `admin_source_cidr` |
| 110 | `allow-http` | 80/TCP | Internet |
| 120 | `allow-https` | 443/TCP | Internet |
| 130 | `allow-kube-api-admin` | 6443/TCP | `admin_source_cidr` |
| 140 | `allow-kube-api-vnet` | 6443/TCP | VirtualNetwork |
| 150 | `allow-flannel-vxlan-vnet` | 8472/UDP | VirtualNetwork |

Everything else from the Internet is denied by the Azure default rules.

## Requirements

- Terraform >= 1.9 (CI uses 1.14.3)
- Azure CLI, logged in with `az login`
- An SSH key pair (only the public key is used by Terraform)
- A domain where you can create DNS records
- Regional vCPU quota: 2 for the server, 4 with a worker

## Usage

```bash
cd terraform

# Azure authentication: the provider uses the Azure CLI login.
az login
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Personal values (git-ignored)
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: admin_source_cidr, ssh_public_key, domain_name

terraform init
terraform fmt -check -recursive
terraform validate
terraform plan
terraform apply      # only after reviewing the plan and the costs
```

`terraform apply` and `terraform destroy` are run only after explicit approval.
Provider versions are locked in `.terraform.lock.hcl` (committed).

## Outputs

| Output | Description |
|---|---|
| `server_public_ip` | Static public IP of the server |
| `server_private_ip` / `worker_private_ips` | Private IPs in `snet-k3s` |
| `ssh_command` | `ssh azureuser@<public-ip>` |
| `kubeconfig_command` | Copies the k3s kubeconfig to `~/.kube/api-gateway.yaml` and points it to the public IP |
| `dns_records` | `A` records to create at the registrar |
| `dns_check_commands` | `dig` commands to check them |

## Variables

| Name | Default | Description |
|---|---|---|
| `location` | `southafricanorth` | Azure region |
| `resource_group_name` | `rg-api-gateway` | Resource group name |
| `tags` | Project / Environment / ManagedBy | Tags for every resource |
| `vnet_address_space` | `["10.10.0.0/16"]` | VNet address space |
| `subnet_address_prefix` | `10.10.1.0/24` | k3s subnet |
| `admin_source_cidr` | — (required) | Admin public IP/CIDR for SSH and the Kubernetes API; `0.0.0.0/0` is rejected |
| `vm_size` | `Standard_B2as_v2` | VM size (2 vCPU / 8 GB); only 2-vCPU sizes are accepted |
| `os_disk_size_gb` | `30` | OS disk size (StandardSSD, billed as the E4 32 GB tier) |
| `auto_shutdown_time` | `2200` | Daily auto-shutdown time, `HHMM` |
| `auto_shutdown_timezone` | `FLE Standard Time` | Windows time zone ID (Kyiv) |
| `worker_count` | `0` | Optional workers, 0 or 1 (4 vCPU quota) |
| `admin_username` | `azureuser` | VM admin user |
| `ssh_public_key` | — (required) | SSH public key content |
| `domain_name` | — (required) | Your domain, e.g. `example.com` |
| `subdomains` | `["api"]` | Subdomains pointing to the VM public IP (single lowercase labels) |
| `dns_record_ttl` | `300` | Suggested TTL (seconds) for the registrar records |
| `k3s_version` | `v1.37.2+k3s1` | Pinned k3s release (stable channel at the time of writing) |

## State

The state is stored locally in `terraform/terraform.tfstate`. It is git-ignored and contains sensitive values (the k3s join token).
If it is lost, Terraform can no longer manage the resources. Keep a private backup after every `apply`:

```bash
cp terraform.tfstate ~/backups/api-gateway-$(date +%F).tfstate
```

## SSH and cluster access

```bash
eval "$(terraform output -raw ssh_command)"

# kubeconfig for a local kubectl
mkdir -p ~/.kube
eval "$(terraform output -raw kubeconfig_command)"
KUBECONFIG=~/.kube/api-gateway.yaml kubectl get nodes

# without a local kubectl
ssh azureuser@$(terraform output -raw server_public_ip) sudo k3s kubectl get nodes
```

The kubeconfig contains cluster-admin credentials: keep it at `0600` and never commit it.

## DNS records

DNS stays at the domain registrar; Terraform does not create a DNS zone. After `terraform apply`:

```bash
terraform output dns_records          # A records to create at the registrar
terraform output dns_check_commands   # commands to verify them
```

Create one `A` record per entry (host = `api`, value = the VM public IP, TTL = 300).
The domain must use the registrar's DNS hosting, not its parking nameservers.
DNS changes can take from a few minutes to a few hours to propagate; until then test with
`curl --resolve api.<domain>:443:<public-ip> https://api.<domain>/`.

## k3s installation

k3s is installed on first boot by cloud-init (`cloud-init/*.tftpl`, passed as VM `custom_data`):

- **Server:** writes `/etc/rancher/k3s/config.yaml` (join token, kubeconfig mode `0600`, `tls-san` for the public IP and every `<subdomain>.<domain>`), then runs the official installer pinned to `k3s_version`. Traefik stays enabled.
- **Worker** (`worker_count = 1`): joins `https://<server private IP>:6443` with the same token.

The join token is generated by `random_password` and stored only in the local state.

Check the installation after `terraform apply`:

```bash
ssh azureuser@<public-ip> 'cloud-init status --wait && sudo k3s kubectl get nodes'
```

Notes:

- Changing anything that ends up in `custom_data` (template, `k3s_version`, `subdomains`, `domain_name`) **recreates the VM**. Upgrade k3s on a running cluster with the installer instead.
- The installer is downloaded from `get.k3s.io` at boot, so the VM needs outbound Internet access (allowed by default in Azure).

## TLS

HTTPS is configured inside the cluster, not by Terraform: cert-manager, Let's Encrypt `ClusterIssuer`s and the Traefik HTTP → HTTPS redirect.
See [`../k8s/bootstrap/README.md`](../k8s/bootstrap/README.md). It requires working DNS and port 80 open from the Internet.

## Operations

Stop and start the VM (Terraform does not track the power state, so no `plan` changes):

```bash
az vm deallocate -g rg-api-gateway -n vm-k3s-server   # stops compute billing
az vm start      -g rg-api-gateway -n vm-k3s-server
```

`az vm stop` without deallocation keeps billing the compute. The VM is also deallocated every day at `auto_shutdown_time`.
The static public IP and the OS disk are billed even while the VM is deallocated; the IP address does not change.

If your admin public IP changes, SSH and port 6443 stop working. Update `admin_source_cidr` in `terraform.tfvars` and run `terraform plan` / `terraform apply` (only the NSG rules change).

Destroy everything (only with explicit approval; the cluster and all its data are lost):

```bash
terraform plan -destroy
terraform destroy
```

## CI and linting

`.github/workflows/terraform.yml` runs on pull requests and pushes to `terraform`, `dev` and `main` when `terraform/**` changes:
`fmt -check`, `init -backend=false`, `validate` and `tflint`. It never runs `plan` or `apply` (local state, no Azure credentials in GitHub).

Run tflint locally ([install](https://github.com/terraform-linters/tflint#installation), rules in `.tflint.hcl`):

```bash
tflint --init
tflint --format compact
```

## Costs

Retail prices for `southafricanorth` (2026-11):

| Item | Price |
|---|---|
| VM `Standard_B2as_v2` | ≈ $0.098/h (≈ $71/month 24/7, ≈ $30/month at 10 h/day) |
| OS disk StandardSSD E4 | ≈ $3.50/month |
| Static Standard public IP | ≈ $3.65/month |

Total: about $78/month 24/7, about $37/month with daily shutdown. A worker doubles the VM and disk costs.

## Known limitations

- Single node, no high availability. The worker join is implemented but not tested on a real VM.
- Burstable CPU (about 40% baseline): long k6 load tests can use up the CPU credits.
- About 200 ms latency from Ukraine to `southafricanorth`.
- Local state only (no locking, single copy).
- SSH and the Kubernetes API are reachable from one admin IP only.
- NGINX gateway, Helm chart and Argo CD are not deployed yet (T-019, T-020).
