# Terraform State Backend

此模块只需在目标 AWS 账户执行一次。它创建私有、加密、版本化的 S3 state 桶和按需计费的 DynamoDB 锁表；不要把它的本地 state 提交到仓库。

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
terraform init
terraform apply
terraform output -raw backend_hcl | Set-Content ..\aws\backend.hcl
```

之后在 `infra/terraform/aws` 目录执行：

```powershell
terraform init -backend-config=backend.hcl
```

State 桶需要由 CI 与受控平台管理员访问。应用 IAM 最小权限策略前，先保留 break-glass 恢复路径和版本化对象恢复权限。
