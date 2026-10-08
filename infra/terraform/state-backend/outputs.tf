output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}

output "backend_hcl" {
  value = <<-EOT
bucket         = "${aws_s3_bucket.state.bucket}"
key            = "researchforge/production.tfstate"
region         = "${var.region}"
dynamodb_table = "${aws_dynamodb_table.lock.name}"
encrypt        = true
EOT
}
