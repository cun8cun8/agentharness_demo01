# ResearchForge Sandbox 与 Policy 规格

> 文档定位：P0 工具权限、沙箱执行和安全边界

## 1. 设计目标

Agent 不直接拥有执行权。所有动作必须经过：

```text
Tool Registry
-> Policy Center
-> Sandbox Runner
-> Trace Recorder
```

目标：

- 限制危险命令；
- 限制访问路径；
- 限制资源消耗；
- 保证工具调用可审计；
- 保证失败可复盘。

## 2. 风险等级

| 等级 | 描述 | 示例 |
| --- | --- | --- |
| L0 | 只读 | `file.read`、`git.diff` |
| L1 | 低风险执行 | `test.run`、受限 `shell.run` |
| L2 | 代码修改 | `file.write_patch` |
| L3 | 高风险 | 联网、安装依赖、删除文件、访问仓库外路径 |

P0 默认：

```text
L0: 允许
L1: 允许但受命令和超时限制
L2: 仅允许 patch 且限制范围
L3: 默认拒绝或需要审批
```

## 3. 命令规则

默认允许命令：

```text
pytest
python -m pytest
npm test
pnpm test
yarn test
go test
git status
git diff
git log
ls
dir
```

默认拦截命令：

```text
rm -rf
del /s
rmdir /s
format
curl | sh
wget | bash
ssh
scp
chmod -R
chown -R
sudo
powershell -EncodedCommand
Invoke-WebRequest
Invoke-Expression
```

P0 对 `shell.run` 的要求：

- 不允许 shell 字符串直接透传；
- 优先使用参数数组；
- 必须设置 `cwd`；
- 必须设置 timeout；
- stdout/stderr 超长时截断并保存 artifact；
- 所有命令必须写入 `tool_calls`。

## 4. 路径规则

允许访问：

```text
任务仓库目录
任务专属 artifact 目录
任务专属临时目录
```

禁止访问：

```text
仓库外任意目录
用户主目录
系统目录
SSH key
云厂商凭证
环境变量密钥文件
其他任务目录
```

路径校验要求：

- 必须解析为绝对路径；
- 必须处理 `..`；
- 必须处理软链接；
- Windows 下必须处理盘符和大小写；
- 最终 resolved path 必须位于任务允许目录内。

## 5. Patch 规则

`file.write_patch` 必须满足：

```text
只允许 unified diff
只允许修改任务仓库内文件
默认最多修改 3 个文件
默认最多修改 80 行
默认禁止修改测试文件
Patch 应用前保存快照
Patch 应用失败自动回滚
Patch 应用后生成 git diff artifact
```

允许在特殊任务中放开测试文件修改，但必须在 `task.yaml` 中显式声明。

## 6. 网络规则

P0 默认：

```text
network_enabled = false
```

需要网络的动作进入：

```text
REQUIRE_APPROVAL
```

典型场景：

```text
安装依赖
下载数据
访问 GitHub
访问论文 API
访问包管理器
```

## 7. 预算规则

任务级预算：

```text
max_steps
max_runtime_seconds
max_tokens
max_model_cost
max_tool_calls
max_patch_files
max_changed_lines
```

超过预算：

```text
run.status = failed
run.error_summary = BUDGET_EXCEEDED
```

同时写入：

```text
agent_steps
tool_calls
audit_logs
failure report artifact
```

## 8. 审批规则

P0 审批状态：

```text
PENDING_APPROVAL
APPROVED
REJECTED
EXPIRED
```

触发审批：

```text
未知命令
联网请求
安装依赖
修改超过限制
访问仓库外路径
长时间运行
删除文件
```

P0 可以先只实现 `REJECTED` 和 `APPROVAL_REQUIRED` 记录，不做完整人工审批 UI。

## 9. 审计日志

必须记录：

```text
actor_id
action
resource_type
resource_id
policy_decision
tool_name
command
path
reason
created_at
```

审计原则：

- 被允许的高风险动作要记录；
- 被拒绝的动作更要记录；
- Policy Block 必须能从 Trace 页面看到。

## 10. 默认 Policy

```json
{
  "id": "policy_default_v1",
  "name": "Default Policy V1",
  "allowed_tools": [
    "file.read",
    "file.write_patch",
    "shell.run",
    "git.diff",
    "test.run",
    "report.write"
  ],
  "blocked_commands": [
    "rm -rf",
    "del /s",
    "rmdir /s",
    "format",
    "curl | sh",
    "wget | bash",
    "ssh",
    "scp",
    "sudo",
    "powershell -EncodedCommand",
    "Invoke-Expression"
  ],
  "max_steps": 20,
  "max_runtime_seconds": 600,
  "max_patch_files": 3,
  "max_changed_lines": 80,
  "network_enabled": false
}
```

