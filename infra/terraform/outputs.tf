# Sentinel deployment skeleton — outputs.
#
# Only values derivable from variables/locals are exposed today; outputs
# referencing provisioned resources (service URL, secret ARN, log group)
# arrive with their modules. Nothing here touches the cloud.

output "name_prefix" {
  description = "Prefix every future resource name derives from."
  value       = local.name_prefix
}

output "environment" {
  description = "Active environment selector (also drives remote-state key prefixes)."
  value       = var.environment
}

output "baseline_tags" {
  description = "Tag map applied by default_tags and expected on every module."
  value       = local.tags
}
