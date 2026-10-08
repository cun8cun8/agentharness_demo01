# Harness.io 交付层

ResearchForge 的 Agent Harness 是应用运行时；本目录是可选的 Harness.io CI/CD 交付层。
两者职责不同：Agent Harness 负责模型、工具权限、沙箱、测试、Trace 和评测，Harness.io
负责代码变更后的构建、制品发布、Kubernetes 部署、审批和回滚。

## 文件

- `ci.yaml`：Harness CI Pipeline as Code。运行后端测试、前端构建、Benchmark 校验、Kubernetes/Helm 校验，并构建四类不可变镜像。
- `cd.yaml`：Harness CD Pipeline as Code。部署现有 `infra/kubernetes/production`，执行验收、人工审批并提供阶段回滚。

## 接入前准备

在 Harness 的 Organization/Project 中创建并替换以下占位符：

- `YOUR_HARNESS_ORG_ID`、`YOUR_HARNESS_PROJECT_ID`
- `YOUR_CODE_REPO_CONNECTOR_ID`、`YOUR_CODE_REPO_NAME`
- `YOUR_IMAGE_REGISTRY_CONNECTOR_ID`
- `YOUR_KUBERNETES_CLUSTER_CONNECTOR_ID`
- `YOUR_PRODUCTION_APPROVER_GROUP_ID`
- `YOUR_PROBE_API_KEY_SECRET_ID`

CI 中的 PostgreSQL 密码是一次性测试容器的非生产值，仅用于 Harness Background service；生产凭据仍必须存入 Harness Secret。

CD 还要求：

1. Harness Delegate 能访问目标 Kubernetes API、镜像仓库和 Git 仓库。
2. 生产集群已安装 Ingress、External Secrets、CNPG、KEDA、Metrics Server 和 RWX StorageClass。
3. `infra/kubernetes/production/kustomization.yaml` 中的镜像、域名、TLS、S3、模型和 Secret 配置已替换为真实值。
4. 生产探针 API Key 以 Harness Secret 保存，不能写入 YAML 或命令行固定值。
5. CI 构建出的四类镜像使用不可变 tag：`api`、`frontend`、`sandbox`、`trainer`；CD 运行时的 `IMAGE_REGISTRY` 必须与 CI 的 `IMAGE_REPOSITORY_PREFIX` 对应。

## 推荐启用顺序

1. 先导入 `ci.yaml`，配置 Git connector 后运行 PR 验证。
2. CI 连续通过后再导入 `cd.yaml`，先绑定开发/预生产环境。
3. 预生产验收通过后再绑定 `production`，保留人工审批。
4. 需要金丝雀和 Prometheus 持续验证时，再将 CD 的 Rolling 部署替换为目标集群支持的 Canary 策略。

本目录不会改变本地启动方式。没有 Harness 账号、Delegate、Kubernetes 集群或镜像仓库时，继续使用：

```powershell
.\infra\scripts\start-local-full.ps1 -Build
```

官方文档：

- [Harness CI pipeline YAML](https://developer.harness.io/docs/continuous-integration/use-ci/prep-ci-pipeline-components/)
- [Harness CI dependencies](https://developer.harness.io/docs/continuous-integration/use-ci/manage-dependencies/)
- [Harness Kubernetes deployment](https://developer.harness.io/docs/continuous-delivery/deploy-srv-diff-platforms/kubernetes/kubernetes-quickstart/)
