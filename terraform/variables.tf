variable "location" {
  type    = string
  default = "southafricanorth"
}

variable "resource_group_name" {
  type    = string
  default = "rg-api-gateway"
}

variable "tags" {
  type = map(string)
  default = {
    Project     = "API-Gateway"
    Environment = "Development"
    ManagedBy   = "Terraform"
  }
}

variable "vnet_address_space" {
  type    = list(string)
  default = ["10.10.0.0/16"]
}

variable "subnet_address_prefix" {
  type    = string
  default = "10.10.1.0/24"
}

variable "admin_source_cidr" {
  type = string

  validation {
    condition     = can(cidrhost(var.admin_source_cidr, 0)) && var.admin_source_cidr != "0.0.0.0/0"
    error_message = "admin_source_cidr must be a valid CIDR (e.g. 203.0.113.10/32) and must not be 0.0.0.0/0."
  }
}

variable "vm_size" {
  type    = string
  default = "Standard_B2as_v2"

  # Only 2-vCPU sizes, so server + 1 worker never exceeds the 4 vCPU quota.
  validation {
    condition     = contains(["Standard_B2as_v2", "Standard_B2als_v2", "Standard_B2s_v2"], var.vm_size)
    error_message = "vm_size must be a 2-vCPU size: Standard_B2as_v2, Standard_B2als_v2 or Standard_B2s_v2."
  }
}

variable "os_disk_size_gb" {
  type    = number
  default = 30
}

variable "auto_shutdown_time" {
  type    = string
  default = "2200"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3])[0-5][0-9]$", var.auto_shutdown_time))
    error_message = "auto_shutdown_time must be in HHMM format, e.g. 2200."
  }
}

variable "auto_shutdown_timezone" {
  type    = string
  default = "FLE Standard Time"
}

variable "worker_count" {
  type    = number
  default = 0

  validation {
    condition     = var.worker_count >= 0 && var.worker_count <= 1
    error_message = "worker_count must be 0 or 1 (4 vCPU regional quota: server 2 vCPU + worker 2 vCPU)."
  }
}

variable "admin_username" {
  type    = string
  default = "azureuser"
}

variable "ssh_public_key" {
  type = string

  validation {
    condition     = startswith(var.ssh_public_key, "ssh-")
    error_message = "ssh_public_key must be an OpenSSH public key (starts with ssh-ed25519 or ssh-rsa)."
  }
}

variable "domain_name" {
  type = string
}

variable "subdomains" {
  type    = list(string)
  default = ["api"]
}
