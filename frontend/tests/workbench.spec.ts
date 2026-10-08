import { test, expect } from "@playwright/test";

test("Chinese workbench navigation, task creation, and layouts", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "代码任务", exact: true })).toBeVisible();
  await expect(page.getByText("正在加载", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "新建", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "创建代码任务" });
  await expect(dialog).toBeVisible();
  const name = `界面验收-${testInfo.project.name}-${Date.now()}`;
  await dialog.getByLabel("任务名称", { exact: true }).fill(name);
  await dialog.getByLabel("仓库路径", { exact: true }).fill("./benchmarks");
  await dialog.getByLabel("修复目标", { exact: true }).fill("中文表单验收，不启动运行。");
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.locator(".detail")).toBeVisible();
  await page.screenshot({ path: `test-results/${testInfo.project.name}-tasks.png`, fullPage: true });
  for (const label of ["仓库接入", "运行记录", "审批中心", "智能体策略", "模型网关", "评测发布", "研究工作台", "项目记忆", "数据审核", "训练任务", "模型版本", "扩展工具", "平台管理"]) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
    await expect(page.getByText("正在加载", { exact: true })).toHaveCount(0);
    await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
  }
  await page.screenshot({ path: `test-results/${testInfo.project.name}-system.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("run records show project and branch context", async ({ page }) => {
  const taskResponse = await page.request.post("/api/v1/tasks", {
    data: {
      title: `运行记录分支验收-${Date.now()}`,
      goal: "验证运行记录显示项目、分支和时间。",
      repo_path: "./benchmarks",
      execution_config: { branch: "researchforge/ui-acceptance" },
    },
  });
  expect(taskResponse.ok()).toBeTruthy();
  const task = await taskResponse.json();
  const runResponse = await page.request.post(`/api/v1/tasks/${task.id}/runs`, {
    data: { model_name: "mock-coding-agent" },
  });
  expect(runResponse.ok()).toBeTruthy();
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "运行记录", exact: true })).toBeVisible();
  await expect(page.getByLabel("分支筛选", { exact: true })).toBeVisible();
  await expect(page.getByLabel("分支筛选", { exact: true }).locator("option", { hasText: "researchforge/ui-acceptance" })).toHaveCount(1);
  await page.getByLabel("分支筛选", { exact: true }).selectOption("researchforge/ui-acceptance");
  await expect(page.getByRole("link", { name: "导出当前结果", exact: true })).toHaveAttribute("href", /branch=researchforge%2Fui-acceptance/);
  const runRows = page.locator(".run-table tbody tr");
  await expect(runRows.first().locator(".run-project > span")).toBeVisible();
  await expect(runRows.first().locator(".run-branch")).toContainText("researchforge/ui-acceptance");
  await runRows.first().getByRole("button", { name: "复制运行链接" }).click({ force: true });
  await expect(page).toHaveURL(/run=run_/);
  await page.reload();
  await expect(page.locator(".detail-drawer")).toBeVisible();
  await page.getByRole("button", { name: "关闭详情", exact: true }).click();
  await expect(page).not.toHaveURL(/run=run_/);
  await runRows.first().getByLabel("选择操作", { exact: true }).check({ force: true });
  await expect(page.getByText("已选择 1 条运行记录", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "清除选择", exact: true }).last().click({ force: true });
  await runRows.first().getByRole("button", { name: "查看详情" }).click({ force: true });
  await expect(page.locator(".detail-drawer")).toBeVisible();
  await expect(page.locator(".detail-drawer")).not.toContainText("[object Object]");
});

test("Chinese repository workspace creates and inspects a connection", async ({ page }, testInfo) => {
  await page.goto("/repositories");
  await expect(page.getByRole("heading", { name: "仓库接入", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "接入代码仓库" });
  const name = `仓库验收-${testInfo.project.name}-${Date.now()}`;
  await dialog.getByLabel("仓库名称", { exact: true }).fill(name);
  await dialog.getByLabel("仓库类型", { exact: true }).selectOption("local");
  await dialog.getByLabel("本地工作目录", { exact: true }).fill("./");
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("button", { name: "健康检查", exact: true })).toBeVisible();
  await expect(page.locator(".detail")).toBeVisible();
  await page.getByRole("button", { name: "自动修复", exact: true }).click();
  const repairDialog = page.getByRole("dialog", { name: "自动修复仓库" });
  await expect(repairDialog).toBeVisible();
  await expect(repairDialog.getByLabel("远端同步失败时使用最近成功缓存验收", { exact: true })).toBeVisible();
  await repairDialog.getByRole("button", { name: "取消", exact: true }).click();
});

test("Chinese model gateway registers and inspects a model", async ({ page }, testInfo) => {
  await page.goto("/models");
  await expect(page.getByRole("heading", { name: "模型网关", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "登记模型网关" });
  const id = `ui-model-${testInfo.project.name}-${Date.now()}`;
  const modelName = `ui-model-name-${Date.now()}`;
  await dialog.getByLabel("模型配置 ID", { exact: true }).fill(id);
  await dialog.getByLabel("模型提供方", { exact: true }).selectOption("mock");
  await dialog.getByLabel("模型名称", { exact: true }).fill(modelName);
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: modelName, exact: true }).click();
  await expect(page.getByRole("button", { name: "健康检查", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "模型用量账本", exact: true })).toBeVisible();
  await expect(page.locator(".detail")).toBeVisible();
  await expect(page.locator(".detail")).not.toContainText("[object Object]");
});

test("Chinese training topology and billing import controls are available", async ({ page }) => {
  await page.goto("/training");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "准备训练任务" });
  await expect(dialog.getByLabel("每节点 GPU 数（0 使用平台默认）", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("每节点 DDP 进程数", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("训练节点数（大于 1 使用 Kubernetes PyTorchJob）", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();

  await page.goto("/models");
  await expect(page.getByRole("heading", { name: "模型用量账本", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "批量导入", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "批量导入供应商账单" });
  await expect(dialog.getByLabel("默认币种", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("账单导出内容", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "同步账单", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "同步已配置供应商账单" });
  await expect(dialog.getByLabel("模型配置 ID（有多个导出配置时必填）", { exact: true })).toBeVisible();
});

test("Chinese user editor persists role and disabled state", async ({ page }, testInfo) => {
  await page.goto("/system");
  await page.getByRole("button", { name: "生产检查", exact: true }).click();
  await expect(page.getByRole("heading", { name: "生产上线检查", exact: true })).toBeVisible();
  await expect(page.getByText("运行环境为 production", { exact: true })).toBeVisible();
  await expect(page.getByText("隔离沙箱可用", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "创建用户" });
  const name = `验收成员-${testInfo.project.name}-${Date.now()}`;
  await dialog.getByLabel("姓名", { exact: true }).fill(name);
  await dialog.getByLabel("邮箱", { exact: true }).fill(`acceptance-${Date.now()}@example.invalid`);
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name, exact: true }).click();
  await page.getByRole("button", { name: "编辑用户", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "编辑用户" });
  await dialog.getByLabel("角色", { exact: true }).selectOption("operator");
  await dialog.getByLabel("账户状态", { exact: true }).selectOption("disabled");
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  await expect(row).toContainText("已停用");
  await expect(row).toContainText("操作员");
  await page.reload();
  await page.getByPlaceholder("搜索全部记录", { exact: true }).fill(name);
  await expect(row).toContainText("已停用");
});

test("Trace review and preference creation use real API records", async ({ page }, testInfo) => {
  const taskResponse = await page.request.post("/api/v1/tasks", { data: { title: `标注验收-${testInfo.project.name}-${Date.now()}`, goal: "界面数据验证", budget: { max_steps: 1, max_tokens: 1 } } });
  expect(taskResponse.ok()).toBeTruthy();
  const task = await taskResponse.json();
  const runs = [];
  for (let index = 0; index < 2; index++) {
    const response = await page.request.post(`/api/v1/tasks/${task.id}/runs`, { data: { model_name: "mock-coding-agent" } });
    expect(response.ok()).toBeTruthy();
    runs.push(await response.json());
  }
  await page.goto("/datasets");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "创建标注样本" });
  await dialog.getByLabel("运行 ID", { exact: true }).fill(runs[0].id);
  await dialog.getByLabel("质量标签", { exact: true }).selectOption("bad");
  await dialog.getByLabel("轨迹类型", { exact: true }).selectOption("FAILURE_TRACE");
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const samples = await (await page.request.get("/api/v1/datasets/trace-items?limit=1000")).json();
  const sample = samples.items.find((item: { agent_run_id: string }) => item.agent_run_id === runs[0].id);
  expect(sample).toBeTruthy();
  await page.getByRole("button", { name: sample.id, exact: true }).click();
  await page.getByRole("button", { name: "人工审核", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "人工审核" });
  await dialog.getByLabel("审核状态", { exact: true }).selectOption("approved");
  await dialog.getByLabel("根本原因", { exact: true }).fill("预算边界验收");
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const updated = await (await page.request.get(`/api/v1/datasets/trace-items/${sample.id}`)).json();
  expect(updated.status).toBe("approved");
  expect(updated.root_cause).toBe("预算边界验收");
  expect(updated.usable_for_sft).toBe(false);
  await page.getByRole("tab", { name: "偏好对", exact: true }).click();
  await page.getByRole("button", { name: "新建", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "创建偏好对" });
  await dialog.getByLabel("优选运行 ID", { exact: true }).fill(runs[0].id);
  await dialog.getByLabel("对照运行 ID", { exact: true }).fill(runs[1].id);
  await dialog.getByLabel("偏好依据", { exact: true }).fill("仅验证标注流程，保留候选状态");
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const pairs = await (await page.request.get(`/api/v1/datasets/preference-pairs?task_id=${task.id}`)).json();
  expect(pairs.items).toHaveLength(1);
  await expect(page.getByRole("button", { name: pairs.items[0].id, exact: true })).toBeVisible();
  await page.screenshot({ path: `test-results/${testInfo.project.name}-datasets.png`, fullPage: true });
});
