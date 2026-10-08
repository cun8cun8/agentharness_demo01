output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  value     = aws_eks_cluster.this.certificate_authority[0].data
  sensitive = true
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "database_backup_bucket" {
  value = aws_s3_bucket.database_backups.bucket
}

output "dr_database_backup_bucket" {
  value = try(aws_s3_bucket.dr_database_backups[0].bucket, null)
}

output "dr_artifact_bucket" {
  value = try(aws_s3_bucket.dr_artifacts[0].bucket, null)
}

output "workspace_efs_id" {
  value = aws_efs_file_system.workspace.id
}

output "workspace_storage_class" {
  value = "researchforge-efs-rwx"
}

output "efs_csi_pod_identity_role_arn" {
  value = aws_iam_role.efs_csi.arn
}

output "model_serving_pod_identity_role_arn" {
  value = aws_iam_role.model_serving.arn
}

output "dns_failover_record" {
  value = try(aws_route53_record.primary[0].fqdn, null)
}

output "ecr_repositories" {
  value = { for name, repo in aws_ecr_repository.images : name => repo.repository_url }
}

output "secret_arns" {
  value = { for name, secret in aws_secretsmanager_secret.production : name => secret.arn }
}

output "external_secrets_pod_identity_role_arn" {
  value = aws_iam_role.external_secrets.arn
}

output "redis_primary_endpoint" {
  value = var.create_managed_redis ? aws_elasticache_replication_group.redis[0].primary_endpoint_address : null
}

output "gpu_node_group" {
  value = try(aws_eks_node_group.gpu[0].node_group_name, null)
}

output "gpu_training_kubernetes_settings" {
  value = var.gpu_enabled ? {
    node_selector = { "researchforge.io/workload" = "gpu-training" }
    tolerations   = [{ key = "nvidia.com/gpu", operator = "Equal", value = "true", effect = "NoSchedule" }]
  } : null
}

output "configure_kubectl" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}
