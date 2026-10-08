locals {
  azs                         = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)
  public_subnets              = { for index, az in local.azs : az => cidrsubnet(var.vpc_cidr, 4, index) }
  private_subnets             = { for index, az in local.azs : az => cidrsubnet(var.vpc_cidr, 4, index + 8) }
  database_backup_bucket_name = coalesce(var.database_backup_bucket_name, "${var.name}-database-backups")
  secret_names = {
    application = "${var.name}/production/application"
    backup      = "${var.name}/production/postgres-backup"
    restore     = "${var.name}/production/restore-drill"
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    Name = "${var.name}-vpc"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags = {
    Name = "${var.name}-igw"
  }
}

resource "aws_subnet" "public" {
  for_each                = local.public_subnets
  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = true
  tags = {
    Name                                    = "${var.name}-public-${each.key}"
    "kubernetes.io/role/elb"                = "1"
    "kubernetes.io/cluster/${var.name}-eks" = "shared"
  }
}

resource "aws_subnet" "private" {
  for_each          = local.private_subnets
  vpc_id            = aws_vpc.this.id
  availability_zone = each.key
  cidr_block        = each.value
  tags = {
    Name                                    = "${var.name}-private-${each.key}"
    "kubernetes.io/role/internal-elb"       = "1"
    "kubernetes.io/cluster/${var.name}-eks" = "shared"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags = {
    Name = "${var.name}-nat"
  }
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = values(aws_subnet.public)[0].id
  depends_on    = [aws_internet_gateway.this]
  tags = {
    Name = "${var.name}-nat"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

data "aws_iam_policy_document" "eks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks" {
  name               = "${var.name}-eks-control-plane"
  assume_role_policy = data.aws_iam_policy_document.eks_assume.json
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  role       = aws_iam_role.eks.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${var.name}-eks/cluster"
  retention_in_days = 30
}

resource "aws_eks_cluster" "this" {
  name     = "${var.name}-eks"
  role_arn = aws_iam_role.eks.arn
  version  = var.kubernetes_version

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  vpc_config {
    subnet_ids              = values(aws_subnet.private)[*].id
    endpoint_private_access = true
    endpoint_public_access  = var.cluster_endpoint_public_access
    public_access_cidrs     = var.cluster_endpoint_public_access ? var.cluster_public_access_cidrs : []
  }

  depends_on = [aws_iam_role_policy_attachment.eks_cluster, aws_cloudwatch_log_group.eks]
}

data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name}-eks-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_eks_node_group" "default" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name}-default"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = values(aws_subnet.private)[*].id
  instance_types  = var.node_instance_types
  capacity_type   = "ON_DEMAND"
  disk_size       = 80

  scaling_config {
    min_size     = var.node_min_size
    desired_size = var.node_desired_size
    max_size     = var.node_max_size
  }

  update_config {
    max_unavailable_percentage = 33
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

resource "aws_eks_node_group" "gpu" {
  count           = var.gpu_enabled ? 1 : 0
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name}-gpu-training"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = values(aws_subnet.private)[*].id
  instance_types  = var.gpu_node_instance_types
  ami_type        = "AL2023_x86_64_NVIDIA"
  capacity_type   = "ON_DEMAND"
  disk_size       = 200

  labels = {
    "researchforge.io/workload" = "gpu-training"
  }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  scaling_config {
    min_size     = var.gpu_node_min_size
    desired_size = var.gpu_node_desired_size
    max_size     = var.gpu_node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  lifecycle {
    precondition {
      condition     = var.gpu_node_min_size >= 0 && var.gpu_node_desired_size >= var.gpu_node_min_size && var.gpu_node_max_size >= var.gpu_node_desired_size
      error_message = "GPU node group sizes must satisfy 0 <= min <= desired <= max."
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

resource "aws_eks_addon" "core" {
  for_each = toset(["vpc-cni", "coredns", "kube-proxy", "aws-ebs-csi-driver", "eks-pod-identity-agent"])

  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.default]
}

resource "aws_iam_role" "efs_csi" {
  name               = "${var.name}-efs-csi"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
}

resource "aws_iam_role_policy_attachment" "efs_csi" {
  role       = aws_iam_role.efs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy"
}

resource "aws_eks_addon" "efs_csi" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "aws-efs-csi-driver"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.default]
}

resource "aws_eks_pod_identity_association" "efs_csi" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "kube-system"
  service_account = "efs-csi-controller-sa"
  role_arn        = aws_iam_role.efs_csi.arn
  depends_on      = [aws_eks_addon.efs_csi, aws_iam_role_policy_attachment.efs_csi]
}

data "aws_iam_policy_document" "model_serving" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["models/*"]
    }
  }
  statement {
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.artifacts.arn}/models/*"]
  }
}

resource "aws_iam_role" "model_serving" {
  name               = "${var.name}-model-serving"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
}

resource "aws_iam_role_policy" "model_serving" {
  name   = "read-model-artifacts"
  role   = aws_iam_role.model_serving.id
  policy = data.aws_iam_policy_document.model_serving.json
}

resource "aws_eks_pod_identity_association" "model_serving" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "researchforge-models"
  service_account = "researchforge-model-serving"
  role_arn        = aws_iam_role.model_serving.arn
  depends_on      = [aws_eks_addon.core, aws_iam_role_policy.model_serving]
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = var.artifact_bucket_name
  force_destroy = false
  tags = {
    Name = "${var.name}-artifacts"
  }
}

resource "aws_s3_bucket" "dr_artifacts" {
  provider      = aws.dr
  count         = var.enable_cross_region_backup_replication ? 1 : 0
  bucket        = var.dr_artifact_bucket_name
  force_destroy = false
  tags = {
    Name = "${var.name}-dr-artifacts"
  }

  lifecycle {
    precondition {
      condition     = var.dr_region != null && var.dr_region != var.region && var.dr_artifact_bucket_name != null
      error_message = "Cross-region artifact replication requires a distinct dr_region and dr_artifact_bucket_name."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "dr_artifacts" {
  provider                = aws.dr
  count                   = var.enable_cross_region_backup_replication ? 1 : 0
  bucket                  = aws_s3_bucket.dr_artifacts[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "dr_artifacts" {
  provider = aws.dr
  count    = var.enable_cross_region_backup_replication ? 1 : 0
  bucket   = aws_s3_bucket.dr_artifacts[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dr_artifacts" {
  provider = aws.dr
  count    = var.enable_cross_region_backup_replication ? 1 : 0
  bucket   = aws_s3_bucket.dr_artifacts[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-noncurrent-artifacts"
    status = "Enabled"
    filter { prefix = "" }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

resource "aws_s3_bucket" "database_backups" {
  bucket        = local.database_backup_bucket_name
  force_destroy = false
  tags = {
    Name = "${var.name}-database-backups"
  }
}

resource "aws_s3_bucket_public_access_block" "database_backups" {
  bucket                  = aws_s3_bucket.database_backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "database_backups" {
  bucket = aws_s3_bucket.database_backups.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "database_backups" {
  bucket = aws_s3_bucket.database_backups.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "database_backups" {
  bucket = aws_s3_bucket.database_backups.id
  rule {
    id     = "retain-database-backups"
    status = "Enabled"
    filter { prefix = "" }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}

resource "aws_s3_bucket" "dr_database_backups" {
  provider      = aws.dr
  count         = var.enable_cross_region_backup_replication ? 1 : 0
  bucket        = var.dr_backup_bucket_name
  force_destroy = false
  tags = {
    Name = "${var.name}-dr-database-backups"
  }

  lifecycle {
    precondition {
      condition     = var.dr_region != null && var.dr_region != var.region && var.dr_backup_bucket_name != null
      error_message = "Cross-region backup replication requires a distinct dr_region and dr_backup_bucket_name."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "dr_database_backups" {
  provider                = aws.dr
  count                   = var.enable_cross_region_backup_replication ? 1 : 0
  bucket                  = aws_s3_bucket.dr_database_backups[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "dr_database_backups" {
  provider = aws.dr
  count    = var.enable_cross_region_backup_replication ? 1 : 0
  bucket   = aws_s3_bucket.dr_database_backups[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dr_database_backups" {
  provider = aws.dr
  count    = var.enable_cross_region_backup_replication ? 1 : 0
  bucket   = aws_s3_bucket.dr_database_backups[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

data "aws_iam_policy_document" "backup_replication_assume" {
  count = var.enable_cross_region_backup_replication ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup_replication" {
  count              = var.enable_cross_region_backup_replication ? 1 : 0
  name               = "${var.name}-database-backup-replication"
  assume_role_policy = data.aws_iam_policy_document.backup_replication_assume[0].json
}

data "aws_iam_policy_document" "backup_replication" {
  count = var.enable_cross_region_backup_replication ? 1 : 0
  statement {
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [aws_s3_bucket.database_backups.arn]
  }
  statement {
    actions   = ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"]
    resources = ["${aws_s3_bucket.database_backups.arn}/*"]
  }
  statement {
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = ["${aws_s3_bucket.dr_database_backups[0].arn}/*"]
  }
}

resource "aws_iam_role_policy" "backup_replication" {
  count  = var.enable_cross_region_backup_replication ? 1 : 0
  name   = "replicate-database-backups"
  role   = aws_iam_role.backup_replication[0].id
  policy = data.aws_iam_policy_document.backup_replication[0].json
}

resource "aws_s3_bucket_replication_configuration" "database_backups" {
  count  = var.enable_cross_region_backup_replication ? 1 : 0
  bucket = aws_s3_bucket.database_backups.id
  role   = aws_iam_role.backup_replication[0].arn

  rule {
    id       = "replicate-cnpg-backups"
    status   = "Enabled"
    priority = 1
    filter {}
    delete_marker_replication { status = "Enabled" }
    destination {
      bucket        = aws_s3_bucket.dr_database_backups[0].arn
      storage_class = "STANDARD"
    }
  }

  depends_on = [aws_s3_bucket_versioning.database_backups, aws_s3_bucket_versioning.dr_database_backups]
}

data "aws_iam_policy_document" "artifact_replication_assume" {
  count = var.enable_cross_region_backup_replication ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "artifact_replication" {
  count              = var.enable_cross_region_backup_replication ? 1 : 0
  name               = "${var.name}-artifact-replication"
  assume_role_policy = data.aws_iam_policy_document.artifact_replication_assume[0].json
}

data "aws_iam_policy_document" "artifact_replication" {
  count = var.enable_cross_region_backup_replication ? 1 : 0
  statement {
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
  }
  statement {
    actions   = ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }
  statement {
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = ["${aws_s3_bucket.dr_artifacts[0].arn}/*"]
  }
}

resource "aws_iam_role_policy" "artifact_replication" {
  count  = var.enable_cross_region_backup_replication ? 1 : 0
  name   = "replicate-artifacts"
  role   = aws_iam_role.artifact_replication[0].id
  policy = data.aws_iam_policy_document.artifact_replication[0].json
}

resource "aws_s3_bucket_replication_configuration" "artifacts" {
  count  = var.enable_cross_region_backup_replication ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  role   = aws_iam_role.artifact_replication[0].arn

  rule {
    id       = "replicate-artifacts"
    status   = "Enabled"
    priority = 1
    filter {}
    delete_marker_replication { status = "Enabled" }
    destination {
      bucket        = aws_s3_bucket.dr_artifacts[0].arn
      storage_class = "STANDARD"
    }
  }

  depends_on = [aws_s3_bucket_versioning.artifacts, aws_s3_bucket_versioning.dr_artifacts]
}

resource "aws_security_group" "efs" {
  name        = "${var.name}-efs"
  description = "NFS access to the ResearchForge shared workspace"
  vpc_id      = aws_vpc.this.id
  ingress {
    protocol    = "tcp"
    from_port   = 2049
    to_port     = 2049
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_efs_file_system" "workspace" {
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  lifecycle_policy { transition_to_ia = "AFTER_30_DAYS" }
  tags = {
    Name = "${var.name}-workspace"
  }
}

resource "aws_efs_backup_policy" "workspace" {
  file_system_id = aws_efs_file_system.workspace.id
  backup_policy { status = "ENABLED" }
}

resource "aws_efs_mount_target" "workspace" {
  for_each        = aws_subnet.private
  file_system_id  = aws_efs_file_system.workspace.id
  subnet_id       = each.value.id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_ecr_repository" "images" {
  for_each             = toset(["api", "frontend", "sandbox", "trainer"])
  name                 = "${var.name}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_secretsmanager_secret" "production" {
  for_each                = local.secret_names
  name                    = each.value
  recovery_window_in_days = var.deletion_protection ? 30 : 0
  tags = {
    Name = "${var.name}-${each.key}"
  }

  dynamic "replica" {
    for_each = var.enable_cross_region_backup_replication ? [var.dr_region] : []
    content {
      region = replica.value
    }
  }
}

resource "aws_route53_health_check" "primary" {
  count             = var.enable_dns_failover ? 1 : 0
  fqdn              = var.primary_endpoint_fqdn
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"
  request_interval  = 30
  failure_threshold = 3
  measure_latency   = true
}

resource "aws_route53_health_check" "disaster_recovery" {
  count             = var.enable_dns_failover ? 1 : 0
  fqdn              = var.dr_endpoint_fqdn
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"
  request_interval  = 30
  failure_threshold = 3
  measure_latency   = true
}

resource "aws_route53_record" "primary" {
  count           = var.enable_dns_failover ? 1 : 0
  zone_id         = var.route53_zone_id
  name            = var.dns_record_name
  type            = "CNAME"
  ttl             = 30
  records         = [var.primary_endpoint_fqdn]
  set_identifier  = "researchforge-primary"
  health_check_id = aws_route53_health_check.primary[0].id

  failover_routing_policy { type = "PRIMARY" }

  lifecycle {
    precondition {
      condition     = var.route53_zone_id != null && var.dns_record_name != null && var.primary_endpoint_fqdn != null && var.dr_endpoint_fqdn != null
      error_message = "DNS failover requires route53_zone_id, dns_record_name, primary_endpoint_fqdn, and dr_endpoint_fqdn."
    }
  }
}

resource "aws_route53_record" "disaster_recovery" {
  count           = var.enable_dns_failover ? 1 : 0
  zone_id         = var.route53_zone_id
  name            = var.dns_record_name
  type            = "CNAME"
  ttl             = 30
  records         = [var.dr_endpoint_fqdn]
  set_identifier  = "researchforge-disaster-recovery"
  health_check_id = aws_route53_health_check.disaster_recovery[0].id

  failover_routing_policy { type = "SECONDARY" }
}

resource "aws_security_group" "redis" {
  count       = var.create_managed_redis ? 1 : 0
  name        = "${var.name}-redis"
  description = "TLS Redis access from the ResearchForge VPC"
  vpc_id      = aws_vpc.this.id
  ingress {
    protocol    = "tcp"
    from_port   = 6379
    to_port     = 6379
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_elasticache_subnet_group" "redis" {
  count      = var.create_managed_redis ? 1 : 0
  name       = "${var.name}-redis"
  subnet_ids = values(aws_subnet.private)[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  count                      = var.create_managed_redis ? 1 : 0
  replication_group_id       = "${var.name}-redis"
  description                = "ResearchForge Redis Streams"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  port                       = 6379
  parameter_group_name       = "default.redis7"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = var.redis_auth_token
  subnet_group_name          = aws_elasticache_subnet_group.redis[0].name
  security_group_ids         = [aws_security_group.redis[0].id]
  apply_immediately          = false
  auto_minor_version_upgrade = true
  tags = {
    Name = "${var.name}-redis"
  }
}

data "aws_iam_policy_document" "pod_identity_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "external_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = values(aws_secretsmanager_secret.production)[*].arn
  }
}

resource "aws_iam_role" "external_secrets" {
  name               = "${var.name}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
}

resource "aws_iam_role_policy" "external_secrets" {
  name   = "read-researchforge-production-secrets"
  role   = aws_iam_role.external_secrets.id
  policy = data.aws_iam_policy_document.external_secrets.json
}

resource "aws_eks_pod_identity_association" "external_secrets" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "external-secrets"
  service_account = "external-secrets"
  role_arn        = aws_iam_role.external_secrets.arn
  depends_on      = [aws_eks_addon.core]
}
