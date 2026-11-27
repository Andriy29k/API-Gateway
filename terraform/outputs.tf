output "server_public_ip" {
  value = azurerm_public_ip.server.ip_address
}

output "server_private_ip" {
  value = azurerm_network_interface.server.private_ip_address
}

output "worker_private_ips" {
  value = azurerm_network_interface.worker[*].private_ip_address
}

output "ssh_command" {
  value = "ssh ${var.admin_username}@${azurerm_public_ip.server.ip_address}"
}

output "dns_records" {
  value = [
    for name in var.subdomains : {
      type  = "A"
      host  = name
      fqdn  = "${name}.${var.domain_name}"
      value = azurerm_public_ip.server.ip_address
      ttl   = var.dns_record_ttl
    }
  ]
}

output "dns_check_commands" {
  value = [for name in var.subdomains : "dig +short ${name}.${var.domain_name}"]
}
