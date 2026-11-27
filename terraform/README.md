# Terraform: Azure k3s infrastructure

Minimal Azure infrastructure for a single-node k3s cluster that hosts the API Gateway project.
Requirements, decisions and the task plan are in [`../TERRAFORM.md`](../TERRAFORM.md).

> Work in progress: this README is completed in T-040.

## Requirements

- Terraform >= 1.9
- Azure CLI, logged in with `az login`
- An SSH key pair (only the public key is used by Terraform)

## Usage

```bash
cd terraform

# Azure authentication: the provider uses the Azure CLI login.
az login
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Personal values (git-ignored)
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars

terraform init
terraform fmt -check -recursive
terraform validate
terraform plan
```

`terraform apply` and `terraform destroy` are run only after explicit approval.

## State

The state is stored locally in `terraform/terraform.tfstate`. It is git-ignored and contains sensitive values.
Keep a private backup after every `apply`.

## Variables

| Name | Default | Description |
|---|---|---|
| `location` | `southafricanorth` | Azure region |
| `resource_group_name` | `rg-api-gateway` | Resource group name |
| `tags` | Project / Environment / ManagedBy | Tags for every resource |
| `vnet_address_space` | `["10.10.0.0/16"]` | VNet address space |
| `subnet_address_prefix` | `10.10.1.0/24` | k3s subnet |
| `admin_source_cidr` | — (required) | Admin public IP/CIDR for SSH and the Kubernetes API; `0.0.0.0/0` is rejected |
| `vm_size` | `Standard_B2as_v2` | VM size (2 vCPU / 8 GB) |
| `worker_count` | `0` | Optional workers, 0 or 1 (4 vCPU quota) |
| `admin_username` | `azureuser` | VM admin user |
| `ssh_public_key` | — (required) | SSH public key content |
| `domain_name` | — (required) | Your domain, e.g. `example.com` |
| `subdomains` | `["api"]` | Subdomains pointing to the VM public IP |
