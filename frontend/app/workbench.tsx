"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  BookOpen,
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Database,
  Download,
  FlaskConical,
  GitBranch,
  HeartPulse,
  LogIn,
  LogOut,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  Square,
  X,
  type LucideIcon,
} from "lucide-react";

type Row = { id?: string; [key: string]: unknown };
type Page = {
  items: Row[];
  total?: number;
  facets?: Row;
  summary?: Row;
  limit?: number;
  offset?: number;
  has_more?: boolean;
  next_offset?: number | null;
};

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
type Field = {
  name: string;
  label: string;
  type?: string;
  value?: string;
  required?: boolean;
  options?: string[];
};
type PublishPayload = {
  run_id: string;
  branch: string;
  title: string;
  body: string;
  push: boolean;
  create_pull_request: boolean;
};
type PublishConfirmationState = {
  repositoryId: string;
  payload: PublishPayload;
  preview: Row;
};
type PendingAction = {
  title: string;
  description: string;
  path: string;
  body: unknown;
  onComplete?: (result?: Row) => Promise<void>;
};
const serverFilteredSections = ["tasks", "runs", "repositories", "datasets", "system", "audit"];
const optimisticCreatePaths = new Set([
  "/tasks",
  "/integrations/repositories",
  "/models",
  "/users",
  "/strategies",
  "/memory/items",
  "/extensions",
  "/datasets/trace-items",
  "/datasets/preference-pairs",
  "/research/briefs",
  "/training/jobs",
]);
const navGroups: [string, [string, string, LucideIcon][]][] = [
  [
    "执行",
    [
      ["tasks", "代码任务", GitBranch],
      ["runs", "运行记录", Activity],
      ["approvals", "审批中心", Check],
    ],
  ],
  [
    "智能体",
    [
      ["repositories", "仓库接入", GitBranch],
      ["strategies", "智能体策略", Settings],
      ["models", "模型网关", Bot],
    ],
  ],
  [
    "研究",
    [
      ["research", "研究工作台", BookOpen],
      ["health", "健康 Demo", HeartPulse],
      ["memory", "项目记忆", Database],
    ],
  ],
  [
    "评测与训练",
    [
      ["datasets", "数据审核", Database],
      ["evaluations", "评测发布", Activity],
      ["training", "训练任务", FlaskConical],
      ["registry", "模型版本", GitBranch],
    ],
  ],
  [
    "平台",
    [
      ["audit", "操作审计", Activity],
      ["extensions", "扩展工具", Settings],
      ["system", "平台管理", Settings],
    ],
  ],
];
const nav = navGroups.flatMap(([, entries]) => entries);
const paths: Record<string, string> = {
  tasks: "/tasks",
  repositories: "/integrations/repositories",
  runs: "/runs",
  approvals: "/approvals",
  strategies: "/strategies",
  models: "/models?status=",
  evaluations: "/evaluations/runs",
  research: "/research/briefs",
  health: "/health-demo/sessions",
  memory: "/memory/items",
  datasets: "/datasets/trace-items",
  training: "/training/jobs",
  registry: "/model-registry/versions",
  extensions: "/extensions",
  system: "/users",
  audit: "/audit-logs",
};
const labels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  frozen: "已冻结",
  failed: "失败",
  cancelled: "已取消",
  paused: "已暂停",
  blocked: "待审批",
  pending: "待审核",
  approved: "已通过",
  rejected: "已拒绝",
  active: "已启用",
  inactive: "未启用",
  candidate: "候选",
  prepared: "待启动",
  starting: "启动中",
  succeeded: "成功",
  operator: "操作员",
  admin: "管理员",
  viewer: "只读",
  created: "已创建",
  success: "成功",
  coding: "代码",
  research: "研究",
  disabled: "已停用",
};
const errors: Record<string, string> = {
  DEPENDENCY_PREPARATION_FAILED: "依赖准备失败，请检查离线缓存、锁文件和沙箱环境",
  QUEUE_WAIT_TIMEOUT: "任务等待超时，请检查 Worker 和工作区并发额度",
  ARTIFACT_CONTENT_EXPIRED: "此日志内容已按保留策略清理，元数据仍可查看",
  REPOSITORY_SYNC_REQUIRED: "请先同步仓库再进行项目诊断",
  AUTHENTICATION_REQUIRED: "请先登录",
  UNAUTHENTICATED: "登录凭据无效",
  AUTHENTICATION_FAILED: "登录失败",
  NOT_FOUND: "请求的记录不存在或已被删除",
  CONFLICT: "数据已被其他操作更新，请刷新后重试",
  VALIDATION_ERROR: "提交内容不符合要求，请检查后重试",
  INTERNAL_ERROR: "服务内部错误，请稍后重试",
  API_ERROR: "服务暂时不可用，请稍后重试",
  STORAGE_UNAVAILABLE: "存储服务暂时不可用，请稍后重试",
  CSRF_TOKEN_REQUIRED: "登录会话已过期，请重新登录",
  TRAINING_BASE_MODEL_NOT_ALLOWED: "基础模型未列入训练白名单",
  TRAINING_DATA_REQUIRED: "请选择训练数据",
  TRAINING_DATA_SOURCE_AMBIGUOUS: "已审核样本与在线 RL 会话不能同时使用",
  TRAINING_BACKEND_UNAVAILABLE: "训练容器不可用",
  TRAINING_KUBERNETES_BACKEND_REQUIRED: "当前未启用 Kubernetes 训练后端",
  TRAINING_KUBERNETES_SHARED_WORKSPACE_REQUIRED:
    "Kubernetes 训练需要配置共享工作区 PVC 与根目录",
  TRAINING_KUBERNETES_PATH_NOT_SHARED: "训练文件或模型目录不在共享工作区内",
  TRAINING_MULTINODE_KUBERNETES_REQUIRED: "多节点训练需要 Kubernetes 后端",
  APPROVED_SFT_DATA_REQUIRED: "请选择已审核的训练样本",
  APPROVED_RL_DATA_REQUIRED: "请选择已审核且允许强化学习的样本",
  NO_REAL_MODEL_CONFIGURED: "尚未配置真实模型",
  ONLINE_RL_COST_BUDGET_EXCEEDED: "在线 RL 成本预算已用尽",
  ONLINE_RL_ROLLOUT_LIMIT_REACHED: "在线 RL Rollout 次数已用尽",
  ONLINE_RL_SESSION_NOT_ACTIVE: "在线 RL 会话已停止",
  ONLINE_RL_ROLLOUTS_REQUIRED: "请先完成至少一次 Rollout",
  ONLINE_RL_REWARD_THRESHOLD_EMPTY: "没有达到奖励阈值的样本",
  ONLINE_RL_REQUIRES_GRPO: "在线 RL 会话只能用于 GRPO 训练",
  ONLINE_RL_SESSION_NOT_FOUND: "在线 RL 会话不存在或不属于当前工作区",
  ONLINE_RL_DATA_NOT_FROZEN: "请先导出并冻结在线 RL 会话",
  ONLINE_RL_TRAINING_DATA_INVALID: "在线 RL 训练数据不完整",
  MODEL_RELEASE_GATE_FAILED: "模型发布门禁未通过",
  REGISTER_CANDIDATE_INFERENCE_ENDPOINT_FIRST: "请先登记候选模型的推理服务",
  SSO_USER_NOT_PROVISIONED: "此身份尚未分配账户",
  RATE_LIMIT_EXCEEDED: "请求过于频繁，请稍后重试",
  MODEL_BILLING_FX_RATE_REQUIRED: "非美元账单需要提交汇率或配置平台汇率",
  MODEL_BILLING_IMPORT_INVALID: "账单导入包含无效行",
  MODEL_BILLING_EXPORT_NOT_CONFIGURED: "当前提供方没有已配置的账单导出地址",
  MODEL_BILLING_EXPORT_CONFIG_AMBIGUOUS: "存在多个账单导出配置，请选择模型配置",
  MODEL_BILLING_EXPORT_FETCH_FAILED: "拉取供应商账单失败",
  MODEL_API_KEY_MISSING: "模型密钥未配置",
  MODEL_PROVIDER_UNSUPPORTED: "模型提供方暂不支持",
  MODEL_GATEWAY_TIMEOUT: "模型网关响应超时，请稍后重试",
  MODEL_REQUEST_FAILED: "模型请求失败，请检查模型配置和网络",
  SANDBOX_UNAVAILABLE: "隔离沙箱不可用，请检查 Docker 或 Kubernetes 配置",
  LOCAL_SANDBOX_NOT_ISOLATED: "当前使用本地临时目录，不能执行生产级隔离任务",
  PRODUCTION_SANDBOX_ISOLATION_REQUIRED: "生产环境必须启用 Docker 或 Kubernetes 隔离沙箱",
  REPOSITORY_NOT_FOUND: "仓库工作区不存在，请先同步仓库",
  REPOSITORY_REMOTE_UNREACHABLE: "无法访问远程仓库，请检查网络或凭据",
  GITHUB_AUTH_REQUIRED: "GitHub App 凭据未配置或已过期",
  JOB_QUEUE_UNAVAILABLE: "任务队列暂时不可用，请稍后重试",
  JOB_VISIBILITY_TIMEOUT: "任务租约已过期，系统正在恢复任务",
  FORBIDDEN: "当前账户无此操作权限",
};
const sensitiveKey = /(api[_-]?key|token|secret|password|authorization|credential)/i;
const safeDiagnosticKey = /(?:_env|_reference|_present)$/i;
function redactForDisplay(input: unknown, key = ""): unknown {
  if (sensitiveKey.test(key) && !safeDiagnosticKey.test(key)) return "<已隐藏>";
  if (Array.isArray(input)) return input.map((item) => redactForDisplay(item));
  if (input && typeof input === "object") {
    return Object.fromEntries(
      Object.entries(input as Record<string, unknown>).map(([childKey, value]) => [
        childKey,
        redactForDisplay(value, childKey),
      ]),
    );
  }
  return input;
}
function safeJson(input: unknown) {
  try {
    return JSON.stringify(redactForDisplay(input), null, 2) ?? String(input ?? "");
  } catch {
    return String(input ?? "");
  }
}
const value = (item: Row, key: string) => {
  const input = item[key];
  return input && typeof input === "object" ? safeJson(input) : String(input ?? "");
};
Object.assign(labels, {
  workspace_admin: "工作区管理员",
  platform_admin: "平台管理员",
  revoked: "已撤销",
  accepted: "已接受",
  canary: "灰度中",
  promoted: "已全量",
  accept_result: "采用已有结果",
  skip: "跳过此轮",
  retry: "重新执行",
  draft: "草稿",
  published: "已发布",
  open: "待审核",
  closed: "已关闭",
  merged: "已合并",
  rolled_back: "已回滚",
  enabled: "已启用",
  error: "错误",
  denied: "已拒绝",
  allow: "已允许",
  matched: "已匹配",
  under_estimated: "实际高于估算",
  over_estimated: "实际低于估算",
  kubernetes: "Kubernetes",
  docker: "Docker",
});
const display = (input: unknown) =>
  labels[String(input)] ||
  (
    {
      good: "优质",
      medium: "一般",
      bad: "低质量",
      high: "高",
      low: "低",
      SUCCESS_TRACE: "成功轨迹",
      FAILURE_TRACE: "失败轨迹",
      sft_candidate: "监督训练候选",
      preference_candidate: "偏好训练候选",
      failure_case: "失败样本",
      rolled_back: "已回滚",
      grpo: "在线强化学习",
    } as Record<string, string>
  )[String(input)] ||
  String(input ?? "");
const rowData = (input: unknown): Row =>
  input && typeof input === "object" && !Array.isArray(input)
    ? (input as Row)
    : {};
function projectLabel(run: Row) {
  const explicit = value(run, "project_name");
  if (explicit) return explicit;
  const repoPath = value(run, "repo_path")
    .replace(/\\/g, "/")
    .replace(/\/+$/, "");
  return repoPath.split("/").at(-1) || "未关联项目";
}
function formatRunTimestamp(run: Row) {
  const raw =
    value(run, "finished_at") ||
    value(run, "started_at") ||
    value(run, "created_at");
  const timestamp = new Date(raw);
  return Number.isNaN(timestamp.getTime())
    ? "未记录"
    : timestamp.toLocaleString("zh-CN", { hour12: false });
}
function relativeRunTimestamp(run: Row) {
  const raw =
    value(run, "finished_at") ||
    value(run, "started_at") ||
    value(run, "created_at");
  const delta = Date.now() - new Date(raw).getTime();
  if (!Number.isFinite(delta)) return "";
  const minutes = Math.floor(Math.abs(delta) / 60000);
  const prefix = delta < 0 ? "约 " : "";
  if (minutes < 1) return delta < 0 ? "即将开始" : "刚刚";
  if (minutes < 60) return `${prefix}${minutes} 分钟${delta < 0 ? "后" : "前"}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${prefix}${hours} 小时${delta < 0 ? "后" : "前"}`;
  return `${prefix}${Math.floor(hours / 24)} 天${delta < 0 ? "后" : "前"}`;
}
function formatRunDuration(duration: unknown) {
  const seconds = Math.max(0, Math.round(Number(duration || 0) / 1000));
  if (!seconds) return "未结束";
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}
function formatRunCost(cost: unknown) {
  const amount = Number(cost || 0);
  return amount > 0 ? `$${amount.toFixed(amount < 0.01 ? 4 : 2)}` : "未计费";
}
function browserQueryValue(name: string) {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(name) || "";
}
function dateTimeInputValue(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (input: number) => String(input).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function dateTimeQueryValue(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}
function setSearchParameter(params: URLSearchParams, key: string, value: string) {
  if (value) params.set(key, value);
  else params.delete(key);
}
function runOutcome(run: Row) {
  const metrics = rowData(run.metrics);
  const total = Number(
    metrics.validation_tests_total || metrics.tests_total || 0,
  );
  const passed = Number(
    metrics.validation_tests_passed || metrics.tests_passed || 0,
  );
  if (value(run, "status") === "completed" && total)
    return `验证 ${passed}/${total} 通过`;
  if (value(run, "status") === "failed")
    return value(run, "error_summary") || "执行失败";
  return display(run.phase) || "等待执行";
}
function runRangeStart(range: string) {
  const hours = { "24h": 24, "7d": 24 * 7, "30d": 24 * 30 }[range];
  return hours ? new Date(Date.now() - hours * 60 * 60 * 1000).toISOString() : "";
}
const sectionHints: Record<string, string> = {
  tasks: "任务与修复循环",
  repositories: "代码源与自动修复",
  runs: "按项目、完成时间与结果查看执行历史",
  approvals: "待处理的高风险操作",
  strategies: "策略版本与发布",
  models: "模型配置与调用账本",
  evaluations: "基准评测与发布门禁",
  research: "研究问题与实验",
  health: "健康数据与人工复核",
  memory: "项目级可复用记忆",
  datasets: "轨迹标注与训练数据",
  training: "训练作业与资源",
  registry: "模型版本与灰度发布",
  extensions: "MCP、Skills 与 Hooks",
  system: "成员、依赖与上线检查",
  audit: "追踪平台操作、资源变更和策略决策",
};
const csrf = () =>
  document.cookie
    .split("; ")
    .find((part) => part.startsWith("researchforge_csrf="))
    ?.split("=")
    .slice(1)
    .join("=") || "";
const clientRequestId = () =>
  `web-${typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
const getCache = new Map<string, { expiresAt: number; data: unknown }>();
const GET_CACHE_TTL_MS = 1200;
async function request<T = Row>(
  path: string,
  method = "GET",
  body?: unknown,
  extra: Record<string, string> = {},
  externalSignal?: AbortSignal,
): Promise<T> {
  const cacheable =
    method === "GET" &&
    !path.startsWith("/events") &&
    !path.startsWith("/telemetry") &&
    path !== "/auth/session";
  const cacheKey = `${path}|${JSON.stringify(extra)}`;
  if (cacheable) {
    const cached = getCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) return cached.data as T;
    if (cached) getCache.delete(cacheKey);
  }
  const controller = new AbortController();
  let abortedByCaller = false;
  const abortFromCaller = () => {
    abortedByCaller = true;
    controller.abort();
  };
  if (externalSignal) {
    if (externalSignal.aborted) abortFromCaller();
    else externalSignal.addEventListener("abort", abortFromCaller, { once: true });
  }
  // PostgreSQL writes serialize the shared record projection while the worker
  // is committing a run. Keep reads snappy, but give mutations enough time to
  // finish instead of showing a false timeout after the server has accepted it.
  const timeout = window.setTimeout(
    () => controller.abort(),
    method === "GET" ? 15000 : 45000,
  );
  try {
    const init: RequestInit = {
      method,
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf(),
        "X-Request-ID": clientRequestId(),
        ...extra,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    };
    let response: Response;
    for (let attempt = 0; ; attempt += 1) {
      try {
        response = await fetch(`/api/v1${path}`, init);
      } catch (error) {
        if (
          method === "GET" &&
          attempt < 2 &&
          error instanceof TypeError &&
          navigator.onLine
        ) {
          await new Promise((resolve) => window.setTimeout(resolve, 250 * 2 ** attempt));
          continue;
        }
        throw error;
      }
      if (
        method !== "GET" ||
        ![502, 503, 504].includes(response.status) ||
        attempt >= 2
      )
        break;
      await new Promise((resolve) => window.setTimeout(resolve, 250 * 2 ** attempt));
    }
    const data =
      response.status === 204 ? {} : await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      const code =
        typeof detail === "string"
          ? detail
          : typeof detail?.code === "string"
            ? detail.code
            : data.error?.code || `HTTP_${response.status}`;
      const issue =
        Array.isArray(detail?.issues) && detail.issues.length
          ? `（第 ${detail.issues[0].row_number} 行：${detail.issues[0].code}）`
          : "";
      const requestId =
        typeof data.error?.details?.request_id === "string"
          ? `（错误编号：${data.error.details.request_id}）`
          : "";
      if (response.status === 401 && path !== "/auth/session") redirectToLogin();
      throw new Error(
        (errors[code] ||
          (code.includes(" ") || /[\u4e00-\u9fff]/.test(code)
            ? code
            : `操作失败（${code}）`)) +
          issue +
          requestId,
      );
    }
    if (cacheable) getCache.set(cacheKey, { expiresAt: Date.now() + GET_CACHE_TTL_MS, data });
    return data as T;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError" && abortedByCaller)
      throw err;
    if (err instanceof DOMException && err.name === "AbortError")
      throw new Error("请求超时，请检查 API 服务或点击重试。");
    if (err instanceof TypeError && /fetch/i.test(err.message)) {
      throw new Error(
        typeof navigator !== "undefined" && !navigator.onLine
          ? "网络连接已断开，恢复网络后请重试。"
          : "无法连接工作台 API，请确认服务已启动后重试。",
      );
    }
    throw err;
  } finally {
    window.clearTimeout(timeout);
    externalSignal?.removeEventListener("abort", abortFromCaller);
  }
}
function redirectToLogin() {
  if (typeof window === "undefined") return;
  getCache.clear(); sessionRead = null; sessionExpires = 0;
  window.dispatchEvent(new Event("researchforge:authentication-required"));
  window.location.replace(`/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
}
let sessionRead: Promise<Row> | null = null;
let sessionExpires = 0;
function api<T = Row>(
  path: string,
  method = "GET",
  body?: unknown,
  extra: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<T> {
  if (method !== "GET") getCache.clear();
  if (
    method !== "GET" &&
    (path.startsWith("/auth/") || path.startsWith("/users/"))
  )
    sessionRead = null;
  if (path === "/auth/session" && method === "GET") {
    if (
      !sessionRead ||
      Date.now() >= sessionExpires ||
      Object.keys(extra).length > 0
    ) {
      sessionExpires = Date.now() + 30000;
      sessionRead = request<Row>(path, method, body, extra, signal).catch((error) => {
        sessionRead = null;
        throw error;
      });
    }
    return sessionRead as Promise<T>;
  }
  return request<T>(path, method, body, extra, signal);
}
function Raw({ data }: { data: unknown }) {
  return (
    <pre className="raw-record">
      {typeof data === "string" ? data : safeJson(data)}
    </pre>
  );
}
function formatRecordValue(input: unknown) {
  if (typeof input === "boolean") return input ? "是" : "否";
  if (input && typeof input === "object") return safeJson(input);
  return display(input);
}
function RecordValue({ input }: { input: unknown }) {
  if (input && typeof input === "object") {
    return <pre className="record-value-json">{formatRecordValue(input)}</pre>;
  }
  return <>{formatRecordValue(input)}</>;
}
function ProductionReadiness({ data }: { data: Row }) {
  const checks = Array.isArray(data.checks) ? (data.checks as Row[]) : [];
  const actions = Array.isArray(data.next_actions)
    ? (data.next_actions as Row[])
    : [];
  const ready = data.status === "ready";
  return (
    <>
      <div className="secondary-actions">
        <span className={`status status-${ready ? "completed" : "failed"}`}>
          {ready ? "可上线" : "尚未满足上线条件"}
        </span>
        <span>
          通过 {String(data.passed || 0)} / {String(data.total || 0)} 项
        </span>
        <span>
          {data.verified_dependencies
            ? "已执行依赖连接验证"
            : "未执行依赖连接验证"}
        </span>
      </div>
      {!ready && (
        <section className="action-plan">
          <h3>上线待办</h3>
          {actions.map((action) => (
            <article key={String(action.check_id)}>
              <h4>{String(action.title || action.check_id)}</h4>
              <p>{String(action.configuration || "请检查相关配置。")}</p>
              <small>
                验收：{String(action.validation || "重新执行生产检查。")}
              </small>
            </article>
          ))}
          {!actions.length && <p>请查看未通过项的证据并完成对应环境配置。</p>}
        </section>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>检查项</th>
              <th>状态</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((check) => (
              <tr key={String(check.id)}>
                <td>{String(check.label || check.id)}</td>
                <td>
                  <span
                    className={`status status-${check.passed ? "completed" : "failed"}`}
                  >
                    {check.passed ? "通过" : "未通过"}
                  </span>
                </td>
                <td>{String(check.evidence || "未记录")}</td>
              </tr>
            ))}
            {!checks.length && (
              <tr>
                <td colSpan={3} className="empty">
                  暂无检查结果
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <details>
        <summary>原始检查结果</summary>
        <Raw data={data} />
      </details>
    </>
  );
}
function RecordDetails({ row }: { row: Row }) {
  const names: Record<string, string> = {
    id: "记录标识",
    title: "名称",
    task_title: "任务",
    project_name: "项目",
    repository_id: "仓库连接",
    repository_url: "仓库地址",
    branch: "分支",
    default_branch: "默认分支",
    commit: "提交",
    question: "研究问题",
    goal: "目标",
    repo_path: "仓库路径",
    test_command: "测试命令",
    status: "状态",
    agent_run_id: "运行标识",
    quality_label: "质量标签",
    trace_type: "轨迹类型",
    use_case: "用途",
    root_cause: "根本原因",
    failure_type: "失败类型",
    human_preferred_action: "人工建议",
    notes: "审核备注",
    usable_for_sft: "用于监督训练",
    usable_for_preference: "用于偏好训练",
    rationale: "审核依据",
    chosen_run_id: "优选运行",
    rejected_run_id: "对照运行",
    email: "邮箱",
    name: "姓名",
    role: "角色",
    workspace_id: "工作区",
    base_model: "基础模型",
    method: "训练方法",
    sample_count: "样本数",
    dataset_sha256: "数据校验值",
    gpu_count: "每节点 GPU 数",
    world_size: "每节点 DDP 进程数",
    node_count: "训练节点数",
    backend: "执行后端",
    backend_resource: "集群训练资源",
    model_name: "模型名称",
    agent_strategy_id: "智能体策略",
    policy_version_id: "策略版本",
    provider: "模型提供方",
    context_window: "上下文窗口",
    cost_per_1k_tokens: "每千 Token 成本",
    config: "连接配置",
    error: "错误",
    created_at: "创建时间",
    started_at: "开始时间",
    finished_at: "完成时间",
    updated_at: "更新时间",
    duration_ms: "运行耗时（毫秒）",
    total_tokens: "Token 用量",
    total_cost: "估算成本",
    actor_id: "执行者",
    action: "操作",
    resource_type: "资源类型",
    resource_id: "资源标识",
    decision: "决策结果",
    detail_json: "操作详情",
  };
  return (
    <>
      <dl className="record-fields">
        {Object.entries(names)
          .filter(([key]) => row[key] !== undefined && row[key] !== null)
          .map(([key, label]) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd>
                <RecordValue input={row[key]} />
              </dd>
            </div>
          ))}
      </dl>
      {row.budget !== undefined && (
        <>
          <h3>运行预算</h3>
          <dl className="record-fields">
            {Object.entries(row.budget as Row).map(([key, input]) => (
              <div key={key}>
                <dt>
                  {(
                    {
                      max_steps: "最大步骤",
                      max_runtime_seconds: "最长运行秒数",
                      max_tokens: "Token 上限",
                      max_model_cost: "成本上限",
                      max_tool_calls: "工具调用上限",
                    } as Record<string, string>
                  )[key] || key}
                </dt>
                <dd>{String(input)}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      <details>
        <summary>原始记录</summary>
        <Raw data={row} />
      </details>
    </>
  );
}
function artifactData(artifact: Row | undefined): Row {
  if (!artifact) return {};
  const content = artifact.content;
  if (typeof content !== "string") return {};
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Row)
      : {};
  } catch {
    return {};
  }
}
function ProjectProfilePanel({ profile }: { profile: Row }) {
  const recipes = (profile.recipes || []) as Row[];
  return <section className="repository-health"><p className="eyebrow">项目诊断</p><h3>{((profile.languages || []) as string[]).join(" / ") || "等待诊断"}</h3>
    <p>默认关闭沙箱网络。依赖准备命令在隔离工作区执行，离线缓存需要随项目提供。</p>
    {recipes.map((recipe) => <article key={String(recipe.language)}><strong>{String(recipe.language)}</strong><p>测试入口：<code>{String(recipe.test_command || "需要配置测试入口")}</code></p><p>依赖准备：<code>{((recipe.setup_commands || []) as string[]).join("\n") || "使用沙箱预装依赖"}</code></p></article>)}
    {((profile.warnings || []) as string[]).map((warning) => <p key={warning} role="status">{warning}</p>)}
  </section>;
}
function RunProgressPanel({ progress }: { progress: Row }) {
  if (!progress.run_id) return null;
  const remaining = (progress.remaining || {}) as Row;
  const phases: Record<string, string> = {created:"等待执行",planning:"生成修复方案",precheck:"环境与依赖准备",run_tests:"基线测试",analyze_failure:"分析失败",edit_code:"修改代码",rerun_tests:"验证修复",evaluate:"独立评审",report:"生成报告"};
  return <section className="repository-health" aria-label="运行进度"><h3>{phases[String(progress.phase)] || String(progress.phase)}</h3>
    <p>{progress.current_tool ? `正在执行 ${String(progress.current_tool)}` : progress.current_goal ? String(progress.current_goal) : display(progress.status)}</p>
    <div className="capability-grid"><div><span>剩余时间</span><strong>{remaining.seconds == null ? "不限" : `${Math.round(Number(remaining.seconds))} 秒`}</strong></div><div><span>剩余 Token</span><strong>{remaining.tokens == null ? "不限" : String(remaining.tokens)}</strong></div><div><span>剩余成本预算（估算）</span><strong>{remaining.model_cost == null ? "不限" : `$${Number(remaining.model_cost).toFixed(4)}`}</strong></div><div><span>剩余工具调用</span><strong>{remaining.tool_calls == null ? "不限" : String(remaining.tool_calls)}</strong></div></div>
    {Boolean(progress.recovery_hint) && <p role="status">{String(progress.recovery_hint)}</p>}
  </section>;
}
function WorkspaceQualityPanel({ data }: { data: Row }) {
  return <section className="repository-health"><h3>项目质量与成本（最近 {String(data.days || 30)} 天）</h3><table><thead><tr><th>项目</th><th>运行数</th><th>成功率</th><th>成本（估算）</th><th>平均耗时</th></tr></thead><tbody>{((data.projects || []) as Row[]).map((project) => <tr key={String(project.project)}><td>{String(project.project)}</td><td>{String(project.runs)}</td><td>{Math.round(Number(project.success_rate) * 100)}%</td><td>${Number(project.cost).toFixed(4)}</td><td>{Math.round(Number(project.average_duration_ms) / 1000)} 秒</td></tr>)}</tbody></table></section>;
}
function RunSummary({ run, artifacts }: { run: Row; artifacts: Row[] }) {
  const byName = new Map(artifacts.map((item) => [value(item, "name"), item]));
  const baseline = artifactData(byName.get("baseline-test.log"));
  const validation = artifactData(byName.get("validation-test.log"));
  const patch = byName.get("fix.patch");
  const metrics = (run.metrics || {}) as Row;
  const parentRunId = value(metrics, "a2a_parent_run_id");
  const delegationDepth = value(metrics, "a2a_delegation_depth");
  const baselineFailed = Number(baseline.exit_code) !== 0;
  const validated =
    Number(validation.exit_code) === 0 && Object.keys(validation).length > 0;
  return (
    <section className="run-summary">
      <div className="run-summary-header">
        <div>
          <p className="eyebrow">修复结论</p>
          <h3>{value(run, "task_title") || "代码修复运行"}</h3>
        </div>
        <span className={`status status-${value(run, "status")}`}>
          {display(run.status)}
        </span>
      </div>
      <div className="run-stage-grid">
        <article
          className={baselineFailed ? "run-stage expected" : "run-stage"}
        >
          <span>基线</span>
          <strong>{baselineFailed ? "发现待修复问题" : "基线通过"}</strong>
          <small>
            {baselineFailed ? "预期失败，作为修复依据" : "未发现失败用例"}
          </small>
        </article>
        <article className={patch ? "run-stage complete" : "run-stage"}>
          <span>补丁</span>
          <strong>
            {patch
              ? `${String((patch.metadata as Row)?.changed_files || metrics.changed_files || 0)} 个文件`
              : "未生成补丁"}
          </strong>
          <small>
            {patch
              ? `${String((patch.metadata as Row)?.changed_lines || metrics.changed_lines || 0)} 行代码变更`
              : "等待 Agent 修改"}
          </small>
        </article>
        <article className={validated ? "run-stage complete" : "run-stage"}>
          <span>验证</span>
          <strong>
            {validated
              ? `${String(validation.tests_passed || metrics.tests_passed || 0)}/${String(validation.tests_total || metrics.tests_total || 0)} 测试通过`
              : "等待验证"}
          </strong>
          <small>
            {metrics.touched_tests ? "检测到测试文件变更" : "未修改测试文件"}
          </small>
        </article>
      </div>
      <dl className="summary-metrics">
        <div>
          <dt>耗时</dt>
          <dd>{Math.round(Number(run.duration_ms || 0) / 1000)} 秒</dd>
        </div>
        <div>
          <dt>Token</dt>
          <dd>{String(run.total_tokens || 0)}</dd>
        </div>
        <div>
          <dt>估算成本</dt>
          <dd>{Number(run.total_cost || 0).toFixed(6)}</dd>
        </div>
        <div>
          <dt>策略</dt>
          <dd>{value(run, "agent_strategy_id")}</dd>
        </div>
      </dl>
      {parentRunId && (
        <p className="run-delegation">
          委派子运行：第 {delegationDepth || "1"} 层，父运行 {parentRunId}
        </p>
      )}
      {patch && (
        <details>
          <summary>查看已验证的代码差异</summary>
          <Raw data={patch.content} />
        </details>
      )}
    </section>
  );
}
function RunListRow({
  row,
  selected,
  busy,
  onInspect,
  onShare,
  onRunAction,
  comparisonSelected,
  onToggleComparison,
  bulkSelected,
  onToggleBulk,
}: {
  row: Row;
  selected: boolean;
  busy: boolean;
  onInspect: (row: Row) => void;
  onShare: (row: Row) => void;
  onRunAction: (path: string) => void;
  comparisonSelected: boolean;
  onToggleComparison: (row: Row, checked: boolean) => void;
  bulkSelected: boolean;
  onToggleBulk: (row: Row, checked: boolean) => void;
}) {
  const active = ["queued", "running", "paused"].includes(value(row, "status"));
  const retryable = ["failed", "cancelled"].includes(value(row, "status"));
  const runId = value(row, "id");
  const metrics = rowData(row.metrics);
  const sourceRevision = value(metrics, "source_revision");
  return (
    <tr className={selected ? "selected" : ""}>
      <td>
        <button className="row-title" onClick={() => onInspect(row)}>
          {value(row, "task_title") || "未命名代码任务"}
        </button>
        <div className="run-project">
          <span>{projectLabel(row)}</span>
          {value(row, "repository_id") && (
            <small title={value(row, "repository_id")}>仓库 {value(row, "repository_id").slice(-10)}</small>
          )}
          {value(row, "branch") && (
            <small className="run-branch" title={value(row, "branch")}>分支 {value(row, "branch")}</small>
          )}
          {value(metrics, "a2a_parent_run_id") && (
            <small>
              子任务 · 第 {value(metrics, "a2a_delegation_depth") || "1"} 层
            </small>
          )}
        </div>
        {sourceRevision && <small className="run-revision">基线提交 {sourceRevision.slice(0, 12)}</small>}
        <label className="compare-toggle">
          <input
            type="checkbox"
            checked={comparisonSelected}
            onChange={(event) => onToggleComparison(row, event.target.checked)}
          />
          加入对比
        </label>
        <label className="compare-toggle bulk-toggle">
          <input
            type="checkbox"
            checked={bulkSelected}
            onChange={(event) => onToggleBulk(row, event.target.checked)}
          />
          选择操作
        </label>
        <small>{runId}</small>
      </td>
      <td>
        <span className={`status status-${value(row, "status")}`}>
          {display(row.status)}
        </span>
        <small className="run-outcome">{runOutcome(row)}</small>
      </td>
      <td className="run-time">
        <time
          dateTime={
            value(row, "finished_at") ||
            value(row, "started_at") ||
            value(row, "created_at")
          }
        >
          {formatRunTimestamp(row)}
        </time>
        <small>{relativeRunTimestamp(row)}</small>
      </td>
      <td>
        <strong className="run-metric">
          {formatRunDuration(row.duration_ms)}
        </strong>
        <small>
          {formatRunCost(row.total_cost)} · {value(row, "total_tokens") || "0"}{" "}
          Token
        </small>
      </td>
      <td>
        <span>{value(row, "model_name") || "未路由模型"}</span>
        <small>{value(row, "agent_strategy_id")}</small>
      </td>
      <td className="right">
        <div className="row-actions">
          {active && (
            <IconButton
              label={row.status === "paused" ? "恢复运行" : "取消运行"}
              icon={row.status === "paused" ? Play : Square}
              onClick={() =>
                onRunAction(
                  `/runs/${runId}/${row.status === "paused" ? "resume" : "cancel"}`,
                )
              }
              disabled={busy}
            />
          )}
          {retryable && (
            <IconButton
              label="重试运行"
              icon={RotateCcw}
              onClick={() => onRunAction(`/runs/${runId}/retry`)}
              disabled={busy}
            />
          )}
          <IconButton
            label="复制运行链接"
            icon={Copy}
            onClick={() => onShare(row)}
          />
          <IconButton
            label="查看详情"
            icon={ChevronRight}
            onClick={() => onInspect(row)}
          />
        </div>
      </td>
    </tr>
  );
}
function RunOverview({
  data,
  onSelectProject,
}: {
  data: Row;
  onSelectProject: (project: string) => void;
}) {
  const summary = rowData(data.summary);
  const projects = Array.isArray(summary.projects)
    ? (summary.projects as Row[])
    : [];
  const successRate = summary.success_rate;
  return (
    <section className="run-overview" aria-label="运行概览">
      <header>
        <div>
          <p className="eyebrow">项目运行概览</p>
          <h2>当前筛选范围</h2>
        </div>
        <span className="record-count">{String(summary.total_runs || 0)} 次运行</span>
      </header>
      <dl className="run-overview-metrics">
        <div>
          <dt>成功率</dt>
          <dd>
            {typeof successRate === "number"
              ? `${Math.round(successRate * 100)}%`
              : "暂无"}
          </dd>
        </div>
        <div>
          <dt>成功 / 失败</dt>
          <dd>
            {String(summary.completed_runs || 0)} / {String(summary.failed_runs || 0)}
          </dd>
        </div>
        <div>
          <dt>平均耗时</dt>
          <dd>{formatRunDuration(summary.average_duration_ms)}</dd>
        </div>
        <div>
          <dt>累计成本</dt>
          <dd>{formatRunCost(summary.total_cost)}</dd>
        </div>
      </dl>
      {projects.length > 0 && (
        <div className="project-summary-list">
          {projects.slice(0, 6).map((project) => (
            <button
              type="button"
              className="project-summary-row"
              key={value(project, "project_name")}
              onClick={() => onSelectProject(value(project, "project_name"))}
            >
              <span>{value(project, "project_name")}</span>
              <small>
                {String(project.completed_count || 0)}/
                {String(project.run_count || 0)} 成功 · {formatRunCost(project.total_cost)}
              </small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
function RunComparisonPanel({ data, onClose }: { data: Row; onClose: () => void }) {
  const left = rowData(data.left);
  const right = rowData(data.right);
  const delta = rowData(data.delta);
  const fields = [
    ["状态", display(left.status), display(right.status)],
    ["模型", value(left, "model_name"), value(right, "model_name")],
    ["策略", value(left, "agent_strategy_id"), value(right, "agent_strategy_id")],
    ["验证", runOutcome(left), runOutcome(right)],
    ["耗时", formatRunDuration(left.duration_ms), formatRunDuration(right.duration_ms)],
    ["成本", formatRunCost(left.total_cost), formatRunCost(right.total_cost)],
    ["Token", value(left, "total_tokens"), value(right, "total_tokens")],
  ];
  const durationDelta = Number(delta.duration_ms || 0);
  return (
    <section className="run-comparison" aria-label="运行对比">
      <header>
        <div>
          <p className="eyebrow">运行对比</p>
          <h2>{data.same_task ? "同一任务的两次执行" : "跨任务执行对比"}</h2>
        </div>
        <IconButton label="关闭对比" icon={X} onClick={onClose} />
      </header>
      <div className="comparison-headings">
        <span>维度</span>
        <strong>{value(left, "task_title") || value(left, "id")}</strong>
        <strong>{value(right, "task_title") || value(right, "id")}</strong>
      </div>
      {fields.map(([label, before, after]) => (
        <div className="comparison-row" key={label}>
          <span>{label}</span>
          <strong>{before}</strong>
          <strong>{after}</strong>
        </div>
      ))}
      <p className="comparison-delta">
        后一次相较前一次：耗时
        {durationDelta === 0
          ? "相同"
          : `${durationDelta > 0 ? "增加 " : "减少 "}${formatRunDuration(Math.abs(durationDelta))}`}
        ，成本
        {Number(delta.total_cost || 0) >= 0 ? "增加 " : "减少 "}
        {formatRunCost(Math.abs(Number(delta.total_cost || 0)))}。
      </p>
    </section>
  );
}
function RepositoryPublicationPanel({
  publications,
  busy,
  onRefresh,
  onRollback,
}: {
  publications: Row[];
  busy: boolean;
  onRefresh: (jobId: string) => void;
  onRollback: (jobId: string) => void;
}) {
  return (
    <section className="publication-history">
      <header>
        <div>
          <p className="eyebrow">发布历史</p>
          <h3>修复 Pull Request</h3>
        </div>
        <span className="record-count">{publications.length} 条</span>
      </header>
      {!publications.length && <p className="capability-message">暂无已发布的修复补丁。</p>}
      {publications.map((publication) => {
        const pullRequest = rowData(publication.pull_request);
        const status = rowData(publication.pull_request_status);
        const state = value(status, "state") || value(publication, "publication_status");
        const merged = status.merged === true;
        return (
          <article className="publication-row" key={value(publication, "job_id")}>
            <div>
              <strong>{value(publication, "branch") || "未记录分支"}</strong>
              <small>
                {value(publication, "commit").slice(0, 12) || "未记录提交"} · {display(state || "published")}
              </small>
            </div>
            <div className="publication-actions">
              {typeof pullRequest.url === "string" && (
                <a href={pullRequest.url} target="_blank" rel="noreferrer">查看 PR</a>
              )}
              {Boolean(pullRequest.number) && (
                <IconButton label="刷新 Pull Request 状态" icon={RefreshCw} onClick={() => onRefresh(value(publication, "job_id"))} disabled={busy} />
              )}
              {Boolean(pullRequest.number) && state === "open" && !merged && (
                <IconButton label="关闭未合并的 Pull Request" icon={Square} onClick={() => onRollback(value(publication, "job_id"))} disabled={busy} />
              )}
            </div>
          </article>
        );
      })}
    </section>
  );
}
function RepositoryHealthPanel({ health }: { health: Row }) {
  const state = (input: unknown, positive: string, pending: string) =>
    input === true ? positive : input === false ? "不可用" : pending;
  const entries = [
    ["远端读取", state(health.read_access, "已验证", "未验证")],
    ["发布凭据", health.auth_configured ? "已配置" : "未配置"],
    ["分支推送", state(health.write_access, "已验证", "待验证")],
    ["草稿 PR", state(health.pull_request_access, "已验证", "待验证")],
  ];
  return (
    <section className="repository-health">
      <div className="run-summary-header">
        <div>
          <p className="eyebrow">仓库能力</p>
          <h3>
            {value(health, "status") === "healthy" ? "已就绪" : "需要处理"}
          </h3>
        </div>
        <span
          className={`status status-${value(health, "status") === "healthy" ? "completed" : "failed"}`}
        >
          {display(health.status)}
        </span>
      </div>
      <div className="capability-grid">
        {entries.map(([label, result]) => (
          <div key={label}>
            <span>{label}</span>
            <strong
              className={
                result === "已验证" || result === "已配置"
                  ? "positive"
                  : result === "不可用" || result === "未配置"
                    ? "negative"
                    : "pending"
              }
            >
              {result}
            </strong>
          </div>
        ))}
      </div>
      <p className="capability-message">
        {value(health, "capability_message") ||
          value(health, "message") ||
          "尚未执行健康检查。"}
      </p>
    </section>
  );
}
function RepositoryIssueAutomationPanel({
  repository,
  webhook,
}: {
  repository: Row;
  webhook: Row;
}) {
  if (value(repository, "provider") !== "github") return null;
  const configured = webhook.configured === true;
  const callbackPath = value(webhook, "callback_url") || value(webhook, "callback_path");
  const copyCallback = () => {
    if (!callbackPath || !navigator.clipboard) return;
    void navigator.clipboard.writeText(callbackPath);
  };
  return (
    <section className="repository-health">
      <div className="run-summary-header">
        <div>
          <p className="eyebrow">Issue 自动修复</p>
          <h3>{configured ? "已配置" : "等待配置"}</h3>
        </div>
        <span className={`status status-${configured ? "completed" : "failed"}`}>
          {configured ? "已启用" : "未启用"}
        </span>
      </div>
      <div className="capability-grid">
        <div>
          <span>自动触发标签</span>
          <strong>{value(webhook, "issue_trigger_label") || "researchforge"}</strong>
        </div>
        <div>
          <span>显式评论命令</span>
          <strong>{value(webhook, "issue_comment_command") || "/researchforge fix"}</strong>
        </div>
        <div>
          <span>Pull Request 评论</span>
          <strong className="pending">
            {webhook.pull_request_comments_supported === true ? "已支持" : "未启用"}
          </strong>
        </div>
        <div>
          <span>评论授权</span>
          <strong>成员与协作者</strong>
        </div>
      </div>
      <div className="repository-webhook-path">
        <code>{callbackPath || "/api/v1/integrations/github/webhook"}</code>
        <IconButton label="复制 Webhook 回调路径" icon={Copy} onClick={copyCallback} />
      </div>
    </section>
  );
}
function PublishResult({ result }: { result: Row }) {
  const pullRequest = (result.pull_request || {}) as Row;
  const prUrl = typeof pullRequest.url === "string" ? pullRequest.url : "";
  return (
    <section className="publish-result">
      <div>
        <p className="eyebrow">发布结果</p>
        <h3>{result.reused ? "已复用此前发布" : "补丁已发布"}</h3>
      </div>
      <dl className="summary-metrics">
        <div>
          <dt>分支</dt>
          <dd>{value(result, "branch")}</dd>
        </div>
        <div>
          <dt>提交</dt>
          <dd>{value(result, "commit").slice(0, 12) || "未记录"}</dd>
        </div>
        <div>
          <dt>推送</dt>
          <dd>{result.pushed ? "已完成" : "未推送"}</dd>
        </div>
      </dl>
      <div className="secondary-actions">
        {prUrl && (
          <a
            className="primary-link"
            href={prUrl}
            target="_blank"
            rel="noreferrer"
          >
            打开草稿 Pull Request
          </a>
        )}
        {typeof result.download_path === "string" && (
          <a href={result.download_path}>下载补丁包</a>
        )}
      </div>
      {Boolean(result.pull_request_error) && (
        <p className="error">
          分支已推送，但创建 Pull Request 失败，可在 GitHub 手动创建。
        </p>
      )}
    </section>
  );
}
function ConfirmationDialog({
  title,
  description,
  confirmLabel,
  onConfirm,
  onClose,
  children,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  children?: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function confirm() {
    setBusy(true);
    setError("");
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="modal confirmation-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <IconButton label="关闭" icon={X} onClick={onClose} disabled={busy} />
        </header>
        <p className="confirmation-copy">{description}</p>
        {children}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button type="button" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className="primary"
            disabled={busy}
            autoFocus
            onClick={() => void confirm()}
          >
            {busy ? "正在执行" : confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );
}
function PublishConfirmation({
  state,
  onConfirm,
  onClose,
}: {
  state: PublishConfirmationState;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  return (
    <ConfirmationDialog
      title="确认发布修复补丁"
      description="预检已通过。确认后将创建提交、推送修复分支，并创建 GitHub 草稿 Pull Request。"
      confirmLabel="确认发布"
      onConfirm={onConfirm}
      onClose={onClose}
    >
      <div className="publish-preview">
        <div>
          <span>修复分支</span>
          <strong>{value(state.preview, "branch")}</strong>
        </div>
        <div>
          <span>基础提交</span>
          <strong>{value(state.preview, "base_commit").slice(0, 12)}</strong>
        </div>
        <div>
          <span>代码变更</span>
          <strong>
            {String(state.preview.changed_files || 0)} 个文件，
            {String(state.preview.changed_lines || 0)} 行
          </strong>
        </div>
        <div>
          <span>验证状态</span>
          <strong>
            {state.preview.policy_allowed ? "策略预检通过" : "策略拒绝"}
          </strong>
        </div>
      </div>
    </ConfirmationDialog>
  );
}
function IconButton({
  label,
  icon: Icon,
  onClick,
  disabled = false,
}: {
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="icon-button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      <Icon size={17} />
    </button>
  );
}
function Form({
  title,
  fields,
  onSubmit,
  onClose,
  loginOptions = false,
}: {
  title: string;
  fields: Field[];
  onSubmit: (data: Record<string, string>) => Promise<void>;
  onClose: () => void;
  loginOptions?: boolean;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current
      ?.querySelector<HTMLElement>("form input, form select, form textarea")
      ?.focus();
    return () => previous?.focus();
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSubmit(
        Object.fromEntries(new FormData(event.currentTarget)) as Record<
          string,
          string
        >,
      );
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section
        ref={dialog}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) onClose();
          if (event.key === "Tab") {
            const elements = [
              ...(dialog.current?.querySelectorAll<HTMLElement>(
                "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]",
              ) || []),
            ];
            const target = event.shiftKey ? elements.at(-1) : elements[0];
            if (
              document.activeElement ===
              (event.shiftKey ? elements[0] : elements.at(-1))
            ) {
              event.preventDefault();
              target?.focus();
            }
          }
        }}
      >
        <header>
          <h2>{title}</h2>
          <IconButton label="关闭" icon={X} onClick={onClose} disabled={busy} />
        </header>
        <form onSubmit={submit}>
          {loginOptions && (
            <div className="login-options">
              <p>企业用户请优先使用组织的单点登录。</p>
              <div>
                <a className="button primary" href="/api/v1/auth/enterprise/oidc/login">
                  企业 SSO（OIDC）
                </a>
                <a className="button" href="/api/v1/auth/enterprise/saml/login">
                  企业 SSO（SAML）
                </a>
              </div>
              <p className="login-help">API 密钥仅用于运维、开发者和自动化服务账号。</p>
            </div>
          )}
          {fields.map((field, index) => (
            <label
              className={field.type === "checkbox" ? "checkbox-field" : ""}
              key={field.name}
            >
              {field.label}
              {field.type === "checkbox" ? (
                <input
                  aria-label={field.label}
                  type="checkbox"
                  name={field.name}
                  defaultChecked={field.value === "true"}
                />
              ) : field.options ? (
                <select
                  aria-label={field.label}
                  name={field.name}
                  defaultValue={field.value}
                >
                  {field.options.map((option) => (
                    <option key={option} value={option}>
                      {display(option)}
                    </option>
                  ))}
                </select>
              ) : field.type === "textarea" ? (
                <textarea
                  aria-label={field.label}
                  name={field.name}
                  defaultValue={field.value}
                  required={field.required}
                  rows={4}
                />
              ) : (
                <input
                  aria-label={field.label}
                  name={field.name}
                  type={field.type || "text"}
                  defaultValue={field.value}
                  required={field.required}
                  autoFocus={index === 0}
                />
              )}
            </label>
          ))}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <footer>
            <button type="button" disabled={busy} onClick={onClose}>
              取消
            </button>
            <button className="primary" disabled={busy}>
              <Check size={16} />
              {busy ? "提交中" : "确认"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

export default function Workbench({ section }: { section: string }) {
  const [items, setItems] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState(() => browserQueryValue("query"));
  const deferredQuery = useDebouncedValue(query, 300);
  const [status, setStatus] = useState(() => browserQueryValue("status"));
  const [runProjectFilter, setRunProjectFilter] = useState(() => browserQueryValue("project"));
  const [runBranchFilter, setRunBranchFilter] = useState(() => browserQueryValue("branch"));
  const [runModelFilter, setRunModelFilter] = useState(() => browserQueryValue("model"));
  const [runStrategyFilter, setRunStrategyFilter] = useState(() => browserQueryValue("strategy"));
  const [runRange, setRunRange] = useState(() => browserQueryValue("range") || "all");
  const [runFromTime, setRunFromTime] = useState(() => dateTimeInputValue(browserQueryValue("from_time")));
  const [runToTime, setRunToTime] = useState(() => dateTimeInputValue(browserQueryValue("to_time")));
  const [runSort, setRunSort] = useState(() => browserQueryValue("sort") || "completed_desc");
  const [auditAction, setAuditAction] = useState(() => browserQueryValue("action"));
  const [auditResourceType, setAuditResourceType] = useState(() => browserQueryValue("resource"));
  const [auditDecision, setAuditDecision] = useState(() => browserQueryValue("decision"));
  const [deepLinkedRunId, setDeepLinkedRunId] = useState(() => browserQueryValue("run"));
  const [deepLinkedJobId, setDeepLinkedJobId] = useState(() => browserQueryValue("job"));
  const [deepLinkedJob, setDeepLinkedJob] = useState<Row | null>(null);
  const [runFacets, setRunFacets] = useState<Row>({});
  const [runSummary, setRunSummary] = useState<Row>({});
  const [bulkRunIds, setBulkRunIds] = useState<string[]>([]);
  const [comparisonRunIds, setComparisonRunIds] = useState<string[]>([]);
  const [runComparison, setRunComparison] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<Row | null>(null);
  const [workspaceHint, setWorkspaceHint] = useState(() =>
    typeof window === "undefined"
      ? ""
      : window.sessionStorage.getItem("researchforge.workspace-id") || "",
  );
  const [sessionReady, setSessionReady] = useState(false);
  const [loadedSection, setLoadedSection] = useState("");
  const [secondarySection, setSecondarySection] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const [details, setDetails] = useState<Row>({});
  const [modal, setModal] = useState<{
    title: string;
    fields: Field[];
    submit: (data: Record<string, string>) => Promise<void>;
    loginOptions?: boolean;
  } | null>(null);
  const [publishConfirmation, setPublishConfirmation] =
    useState<PublishConfirmationState | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(
    null,
  );
  const [notice, setNotice] = useState("");
  const [offline, setOffline] = useState(false);
  const loadSequence = useRef(0);
  const loadedRequestKey = useRef("");
  const inspectedDeepLink = useRef("");
  const refreshTimer = useRef<number | null>(null);
  const inspectedId = useRef<unknown>(null);
  const runStartKeys = useRef<Record<string, string>>({});
  const loadAbort = useRef<AbortController | null>(null);
  const [datasetView, setDatasetView] = useState("trace-items");
  const user: Row = {
    ...((session?.user || {}) as Row),
    workspace_id: (session?.workspace as Row)?.id,
  };
  const admin = user.role === "admin";
  const workspace = (session?.workspace || {}) as Row;
  const activeWorkspaceId = String(workspace.id || workspaceHint || "");
  const authenticated = useRef(false);
  const load = useCallback(async () => {
    if (!authenticated.current) return;
    const sequence = ++loadSequence.current;
    loadAbort.current?.abort();
    const abortController = new AbortController();
    loadAbort.current = abortController;
    setBusy(true);
    setError("");
    try {
      const scope = activeWorkspaceId
        ? `&workspace_id=${encodeURIComponent(activeWorkspaceId)}`
        : "";
      const search = serverFilteredSections.includes(section) && section !== "audit"
        ? `&query=${encodeURIComponent(deferredQuery)}&status=${encodeURIComponent(status)}`
        : "";
      const auditFilters = section === "audit"
        ? `&query=${encodeURIComponent(deferredQuery)}${auditAction ? `&action=${encodeURIComponent(auditAction)}` : ""}${auditResourceType ? `&resource_type=${encodeURIComponent(auditResourceType)}` : ""}${auditDecision ? `&decision=${encodeURIComponent(auditDecision)}` : ""}`
        : "";
      const explicitFromTime = dateTimeQueryValue(runFromTime);
      const explicitToTime = dateTimeQueryValue(runToTime);
      const rangeFromTime = runRangeStart(runRange);
      const runFilters =
        section === "runs"
          ? `${runProjectFilter ? `&project_name=${encodeURIComponent(runProjectFilter)}` : ""}${runBranchFilter ? `&branch=${encodeURIComponent(runBranchFilter)}` : ""}${runModelFilter ? `&model_name=${encodeURIComponent(runModelFilter)}` : ""}${runStrategyFilter ? `&strategy_id=${encodeURIComponent(runStrategyFilter)}` : ""}${explicitFromTime || rangeFromTime ? `&from_time=${encodeURIComponent(explicitFromTime || rangeFromTime)}` : ""}${explicitToTime ? `&to_time=${encodeURIComponent(explicitToTime)}` : ""}&sort=${encodeURIComponent(runSort)}`
          : "";
      const endpoint =
        section === "datasets" ? `/datasets/${datasetView}` : paths[section];
      const separator = endpoint.includes("?") ? "&" : "?";
      const page = await api<Page>(
        `${endpoint}${separator}limit=25&offset=${offset}${scope}${search}${auditFilters}${runFilters}`,
        "GET",
        undefined,
        {},
        abortController.signal,
      );
      if (sequence === loadSequence.current) {
        setItems(page.items || []);
        setTotal(page.total ?? page.items?.length ?? 0);
        if (section === "runs") {
          setRunSummary(rowData(page.summary));
          setRunFacets(rowData(page.facets));
        }
        setLoadedSection(section);
      }
    } catch (err) {
      if (
        sequence === loadSequence.current &&
        !(err instanceof DOMException && err.name === "AbortError")
      )
        setError((err as Error).message);
    } finally {
      if (loadAbort.current === abortController) loadAbort.current = null;
      if (sequence === loadSequence.current) setBusy(false);
    }
  }, [section, offset, datasetView, activeWorkspaceId, deferredQuery, status, auditAction, auditResourceType, auditDecision, runProjectFilter, runBranchFilter, runModelFilter, runStrategyFilter, runRange, runFromTime, runToTime, runSort]);
  const openLogin = useCallback(() => redirectToLogin(), []);
  useEffect(() => {
    const clear = () => {
      authenticated.current = false;
      ++loadSequence.current;
      loadAbort.current?.abort(); inspectedId.current = null;
      getCache.clear();
      setSession(null); setItems([]); setTotal(0); setSelected(null); setDetails({});
      setRunSummary({}); setRunFacets({}); setRunComparison(null);
      setBulkRunIds([]); setComparisonRunIds([]); setModal(null);
      setPublishConfirmation(null); setPendingAction(null); setNotice("");
      window.sessionStorage.removeItem("researchforge.workspace-id"); setWorkspaceHint("");
    };
    window.addEventListener("researchforge:authentication-required", clear);
    return () => window.removeEventListener("researchforge:authentication-required", clear);
  }, []);
  useEffect(() => {
    if (section !== "training") return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 10000);
    return () => clearInterval(timer);
  }, [section, load]);
  useEffect(() => {
    if (section !== "runs") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 8000);
    return () => window.clearInterval(timer);
  }, [section, load]);
  useEffect(() => {
    let active = true;
    void api("/auth/session")
      .then((data) => {
        if (!active) return;
        authenticated.current = true;
        setSession(data);
        const workspaceId = String((data.workspace as Row)?.id || "");
        if (workspaceId) {
          window.sessionStorage.setItem(
            "researchforge.workspace-id",
            workspaceId,
          );
          setWorkspaceHint(workspaceId);
        }
      })
      .catch(() => {
        if (active) {
          setSession(null);
          openLogin();
        }
      })
      .finally(() => {
        if (active) setSessionReady(true);
      });
    return () => {
      active = false;
    };
  }, [openLogin]);
  useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    const refresh = () => {
      if (document.visibilityState === "visible" && loadedSection === section)
        void load();
    };
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load, loadedSection, section]);
  // Resolve the current workspace before fetching page records. Previously each
  // route rendered an unscoped request and then immediately fetched the same
  // page again after the session arrived, leaving forms busy under load.
  useEffect(() => {
    if (!sessionReady || !session) return;
    const requestKey = [
      section,
      activeWorkspaceId,
      offset,
      datasetView,
      deferredQuery,
      status,
      runProjectFilter,
      runBranchFilter,
      runModelFilter,
      runStrategyFilter,
      runRange,
      runFromTime,
      runToTime,
      runSort,
      auditAction,
      auditResourceType,
      auditDecision,
    ].join("|");
    if (loadedRequestKey.current === requestKey) return;
    loadedRequestKey.current = requestKey;
    void load();
  }, [
    load,
    sessionReady,
    session,
    workspaceHint,
    section,
    activeWorkspaceId,
    offset,
    datasetView,
    deferredQuery,
    status,
    runProjectFilter,
    runBranchFilter,
    runModelFilter,
    runStrategyFilter,
    runRange,
    runFromTime,
    runToTime,
    runSort,
    auditAction,
    auditResourceType,
    auditDecision,
  ]);
  useEffect(() => {
    if (loadedSection !== section) return;
    const timer = window.setTimeout(() => setSecondarySection(section), 300);
    return () => window.clearTimeout(timer);
  }, [loadedSection, section]);
  useEffect(
    () => () => {
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
    },
    [section, datasetView],
  );
  useEffect(() => {
    if (section !== "runs" || !selected?.id || !["running", "queued"].includes(String(selected.status)) || !session) return;
    const id = String(selected.id);
    const timer = window.setInterval(() => {
      if (!authenticated.current || document.visibilityState !== "visible") return;
      void Promise.all([api<Row>(`/runs/${id}`), api<Row>(`/runs/${id}/progress`), api<Page>(`/runs/${id}/tool-calls`), api<Page>(`/runs/${id}/artifacts`)])
        .then(([run, progress, calls, artifacts]) => { if (authenticated.current && inspectedId.current === id) {setSelected(run);setDetails((current) => ({...current,progress,calls:calls.items,artifacts:artifacts.items}));} })
        .catch((err) => { if (authenticated.current) setError(err.message); });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [section, selected?.id, selected?.status, session]);
  const inspect = useCallback(
    async (row: Row) => {
      inspectedId.current = row.id;
      setSelected(row);
      setDetails({});
      try {
        if (section === "runs") {
          const [run, calls, artifacts, progress] = await Promise.all([
            api<Row>(`/runs/${row.id}`),
            api<Page>(`/runs/${row.id}/tool-calls`),
            api<Page>(`/runs/${row.id}/artifacts`),
            api<Row>(`/runs/${row.id}/progress`),
          ]);
          if (inspectedId.current !== row.id) return;
          const metrics = rowData(run.metrics);
          const delegation = metrics.a2a_parent_run_id
            ? await api(`/a2a/delegations/${row.id}`).catch(() => null)
            : null;
          if (inspectedId.current !== row.id) return;
          setSelected(run);
          setDetails({
            calls: calls.items,
            artifacts: artifacts.items,
            progress,
            ...(delegation ? { delegation } : {}),
          });
        } else if (section === "training") {
          const job = await api(`/training/jobs/${row.id}`);
          setSelected(job);
          if (
            ["running", "succeeded", "failed", "cancelled"].includes(
              value(job, "status"),
            )
          )
            setDetails(await api(`/training/jobs/${row.id}/logs`));
        } else if (section === "tasks")
          setSelected(await api(`/tasks/${row.id}`));
        else if (section === "repositories") {
          const [repository, health, publications, webhook, projectProfile] = await Promise.all([
            api(`/integrations/repositories/${row.id}`),
            api(`/integrations/repositories/${row.id}/health`),
            api<Page>(`/integrations/repositories/${row.id}/publications`),
            api<Row>("/integrations/github/webhook/status").catch(() => ({})),
            api<Row>(`/integrations/repositories/${row.id}/profile`).catch(() => ({})),
          ]);
          setSelected(repository);
          setDetails({ health, publications: publications.items, webhook, projectProfile });
        } else if (section === "models") {
          const health = await api<Page>(
            "/models/health?status=active&verify_connectivity=true",
          );
          setDetails({
            health: health.items.find((item) => item.model_id === row.id) || {
              reason: "MODEL_HEALTH_NOT_FOUND",
            },
          });
        } else if (section === "strategies")
          setSelected(await api(`/strategies/${row.id}`));
        else if (section === "evaluations")
          setSelected(await api(`/evaluations/runs/${row.id}`));
        else if (section === "memory")
          setSelected(await api(`/memory/items/${row.id}`));
        else if (section === "registry") {
          const deployments = await api<Page>("/model-registry/deployments");
          setDetails({
            deployments: deployments.items.filter(
              (item) => item.version_id === row.id,
            ),
          });
        }
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [section],
  );
  useEffect(() => {
    if (
      section !== "runs" ||
      !deepLinkedRunId ||
      !sessionReady ||
      !session ||
      inspectedDeepLink.current === deepLinkedRunId
    )
      return;
    inspectedDeepLink.current = deepLinkedRunId;
    void api<Row>(`/runs/${encodeURIComponent(deepLinkedRunId)}`)
      .then((run) => inspect(run))
      .catch((err) => {
        inspectedDeepLink.current = "";
        setError(err.message);
      });
  }, [section, deepLinkedRunId, sessionReady, session, inspect]);
  useEffect(() => {
    if (section !== "runs" || !deepLinkedJobId || !sessionReady || !session) return;
    let cancelled = false;
    let timer: number | undefined;
    const resolveJob = async () => {
      try {
        const job = await api<Row>(`/jobs/${encodeURIComponent(deepLinkedJobId)}`);
        if (!cancelled) setDeepLinkedJob(job);
        const metadata = rowData(job.metadata);
        const result = rowData(job.result_json);
        const runId = value(metadata, "agent_run_id") || value(result, "agent_run_id");
        if (runId) {
          if (!cancelled) {
            setDeepLinkedRunId(runId);
            setDeepLinkedJobId("");
          }
          return;
        }
        if (!cancelled && !["failed", "cancelled"].includes(value(job, "status"))) {
          timer = window.setTimeout(() => void resolveJob(), 1500);
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    };
    void resolveJob();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [section, deepLinkedJobId, sessionReady, session]);
  useEffect(() => {
    if (section !== "runs" || typeof window === "undefined") return;
    const url = new URL(window.location.href);
    setSearchParameter(url.searchParams, "query", query);
    setSearchParameter(url.searchParams, "status", status);
    setSearchParameter(url.searchParams, "project", runProjectFilter);
    setSearchParameter(url.searchParams, "branch", runBranchFilter);
    setSearchParameter(url.searchParams, "model", runModelFilter);
    setSearchParameter(url.searchParams, "strategy", runStrategyFilter);
    setSearchParameter(url.searchParams, "range", runRange === "all" ? "" : runRange);
    setSearchParameter(url.searchParams, "from_time", dateTimeQueryValue(runFromTime));
    setSearchParameter(url.searchParams, "to_time", dateTimeQueryValue(runToTime));
    setSearchParameter(url.searchParams, "sort", runSort === "completed_desc" ? "" : runSort);
    setSearchParameter(url.searchParams, "run", deepLinkedRunId);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, [section, query, status, runProjectFilter, runBranchFilter, runModelFilter, runStrategyFilter, runRange, runFromTime, runToTime, runSort, deepLinkedRunId]);
  useEffect(() => {
    if (section !== "audit" || typeof window === "undefined") return;
    const url = new URL(window.location.href);
    setSearchParameter(url.searchParams, "query", query);
    setSearchParameter(url.searchParams, "action", auditAction);
    setSearchParameter(url.searchParams, "resource", auditResourceType);
    setSearchParameter(url.searchParams, "decision", auditDecision);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, [section, query, auditAction, auditResourceType, auditDecision]);
  useEffect(() => {
    if (section !== "runs" || typeof window === "undefined") return;
    const url = new URL(window.location.href);
    setSearchParameter(url.searchParams, "run", deepLinkedRunId);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, [section, deepLinkedRunId]);
  useEffect(() => {
    if (
      section !== "runs" ||
      !selected?.id ||
      !["queued", "running"].includes(value(selected, "status"))
    )
      return;
    const source = new EventSource(`/api/v1/runs/${selected.id}/events`, {
      withCredentials: true,
    });
    const update = () => {
      if (document.visibilityState !== "visible") return;
      void api(`/runs/${selected.id}`)
        .then((row) => {
          if (inspectedId.current !== selected.id) return;
          void inspect(row);
        })
        .catch((err) => setError(err.message));
    };
    const names = [
      "run.completed",
      "run.failed",
      "run.cancelled",
      "run.updated",
      "tool.completed",
      "step.completed",
    ];
    names.forEach((name) => source.addEventListener(name, update));
    source.onmessage = update;
    const timer = setInterval(update, 5000);
    return () => {
      source.close();
      clearInterval(timer);
    };
  }, [section, selected?.id, selected?.status, inspect]);
  function mergeListResult(path: string, result: Row) {
    const createPaths = new Set([
      "/tasks",
      "/integrations/repositories",
      "/models",
      "/users",
      "/strategies",
      "/memory/items",
      "/extensions",
      "/datasets/trace-items",
      "/datasets/preference-pairs",
      "/research/briefs",
      "/training/jobs",
    ]);
    if (!createPaths.has(path) || !result.id) return;
    setItems((current) => {
      const existing = current.findIndex((item) => item.id === result.id);
      if (existing >= 0)
        return current.map((item) =>
          item.id === result.id ? { ...item, ...result } : item,
        );
      return [result, ...current].slice(0, 25);
    });
    setTotal((current) =>
      Math.max(
        current,
        items.some((item) => item.id === result.id) ? current : current + 1,
      ),
    );
  }
  function scheduleRefresh() {
    if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null;
      void load();
    }, 750);
  }
  async function confirmPublish() {
    if (!publishConfirmation) return;
    const { repositoryId, payload } = publishConfirmation;
    setNotice("正在创建提交、推送修复分支并创建草稿 Pull Request...");
    const result = await action(
      `/integrations/repositories/${repositoryId}/publish`,
      payload,
    );
    setDetails((current) => ({ ...current, publish: result }));
    const pullRequest = (result.pull_request || {}) as Row;
    setNotice(
      typeof pullRequest.url === "string"
        ? "修复已发布，草稿 Pull Request 已创建。"
        : result.reused
          ? "已显示此前的发布结果。"
          : "修复补丁已创建，可下载补丁包或继续推送。",
    );
    if (typeof pullRequest.url === "string" && payload.run_id) {
      window.location.assign(`/runs?run=${encodeURIComponent(payload.run_id)}`);
    }
  }
  function inspectProductionReadiness() {
    setSelected({ id: "production-readiness", name: "生产上线检查" });
    setDetails({
      status: "checking",
      passed: 0,
      total: 2,
      checks: [
        {
          id: "production_environment",
          label: "运行环境为 production",
          passed: false,
          evidence: "正在读取运行配置",
        },
        {
          id: "sandbox_probe",
          label: "隔离沙箱可用",
          passed: false,
          evidence: "正在检查沙箱状态",
        },
      ],
    });
    setBusy(true);
    setError("");
    void api("/system/production-readiness")
      .then((data) => setDetails(data))
      .catch((err) => setError(err.message))
      .finally(() => setBusy(false));
  }
  async function action(path: string, body: unknown = {}) {
    setBusy(true);
    setError("");
    setNotice("");
    const isRunStart = /^\/tasks\/[^/]+\/runs$/.test(path);
    const idempotencyKey = isRunStart
      ? (runStartKeys.current[path] ||= `ui-run-${clientRequestId().slice(4)}`)
      : "";
    try {
      const result = await api(
        path,
        "POST",
        body,
        idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {},
      );
      if (isRunStart) delete runStartKeys.current[path];
      loadSequence.current += 1;
      setNotice("操作已完成");
      mergeListResult(path, result);
      // Closing a form must not wait behind optional dashboard requests. Let a
      // short idle window preserve responsiveness for a following user action.
      if (!optimisticCreatePaths.has(path)) scheduleRefresh();
      if (selected && result.id === selected.id) setSelected(result);
      return result;
    } catch (err) {
      if (isRunStart) delete runStartKeys.current[path];
      setError((err as Error).message);
      throw err;
    } finally {
      setBusy(false);
    }
  }
  const runAction = (path: string, body: unknown = {}) => {
    const destructive = /\/(?:cancel|stop|rollback)(?:\/|$)/.test(path);
    if (destructive) {
      const label = path.includes("rollback")
        ? "回滚"
        : path.includes("stop")
          ? "停止"
          : "取消";
      setPendingAction({
        title: `确认${label}`,
        description: `${label}后将无法继续当前执行。确认继续吗？`,
        path,
        body,
      });
      return;
    }
    void action(path, body).catch(() => {});
  };
  function toggleRunComparison(row: Row, checked: boolean) {
    const runId = value(row, "id");
    if (!runId) return;
    if (checked && !comparisonRunIds.includes(runId) && comparisonRunIds.length >= 2) {
      setNotice("一次最多对比两条运行记录，请先取消已选择的记录。");
      return;
    }
    setComparisonRunIds((current) =>
      checked
        ? current.includes(runId)
          ? current
          : [...current, runId]
        : current.filter((id) => id !== runId),
    );
  }
  function toggleBulkRun(row: Row, checked: boolean) {
    const runId = value(row, "id");
    if (!runId) return;
    if (checked && !bulkRunIds.includes(runId) && bulkRunIds.length >= 50) {
      setNotice("一次最多选择 50 条运行记录。");
      return;
    }
    setBulkRunIds((current) =>
      checked
        ? current.includes(runId)
          ? current
          : [...current, runId]
        : current.filter((id) => id !== runId),
    );
  }
  async function bulkRunAction(operation: "cancel" | "retry") {
    if (!bulkRunIds.length) return;
    const result = await action(`/runs/batch-${operation}`, { run_ids: bulkRunIds });
    const changed = Number(result.changed ?? result.queued ?? 0);
    const skipped = bulkRunIds.length - changed;
    setBulkRunIds([]);
    setNotice(
      operation === "cancel"
        ? `批量取消完成：${changed} 条已取消${skipped ? `，${skipped} 条无需取消` : ""}`
        : `批量重试已排队：${changed} 条${skipped ? `，${skipped} 条未执行` : ""}`,
    );
    loadSequence.current += 1;
    scheduleRefresh();
  }
  function requestBulkCancel() {
    if (!bulkRunIds.length) return;
    const ids = [...bulkRunIds];
    setPendingAction({
      title: "确认批量取消",
      description: `将取消选中的 ${ids.length} 条运行记录；已完成记录会被逐项跳过。确认继续吗？`,
      path: "/runs/batch-cancel",
      body: { run_ids: ids },
      onComplete: async (result) => {
        const changed = Number(result?.changed ?? 0);
        const skipped = Math.max(0, ids.length - changed);
        setBulkRunIds([]);
        setNotice(
          `批量取消完成：${changed} 条已取消${skipped ? `，${skipped} 条无需取消` : ""}`,
        );
      },
    });
  }
  async function compareSelectedRuns() {
    if (comparisonRunIds.length !== 2) return;
    setBusy(true);
    setError("");
    try {
      const comparison = await api<Row>(
        `/run-comparison?left_run_id=${encodeURIComponent(comparisonRunIds[0])}&right_run_id=${encodeURIComponent(comparisonRunIds[1])}`,
      );
      setRunComparison(comparison);
      setNotice("已生成两条运行记录的对比结果。");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function updateRecord(path: string, data: unknown) {
    const row = await api<Row>(path, "PATCH", data);
    loadSequence.current += 1;
    setSelected(row);
    setItems((current) => {
      const index = current.findIndex((item) => item.id === row.id);
      if (index < 0) return [row, ...current].slice(0, 25);
      return current.map((item) =>
        item.id === row.id ? { ...item, ...row } : item,
      );
    });
    setNotice("已保存");
    scheduleRefresh();
  }
  function review() {
    if (!selected) return;
    const fields: Field[] = [
      {
        name: "status",
        label: "审核状态",
        options: ["candidate", "approved", "rejected"],
        value: value(selected, "status"),
      },
    ];
    if (datasetView === "preference-pairs")
      fields.push({
        name: "rationale",
        label: "偏好依据",
        type: "textarea",
        value: value(selected, "rationale"),
        required: true,
      });
    else
      fields.push(
        {
          name: "quality_label",
          label: "质量标签",
          options: ["good", "medium", "bad"],
          value: value(selected, "quality_label"),
        },
        {
          name: "root_cause",
          label: "根本原因",
          type: "textarea",
          value: value(selected, "root_cause"),
        },
        {
          name: "human_preferred_action",
          label: "人工建议",
          type: "textarea",
          value: value(selected, "human_preferred_action"),
        },
        {
          name: "notes",
          label: "审核备注",
          type: "textarea",
          value: value(selected, "notes"),
        },
        {
          name: "usable_for_sft",
          label: "用于监督训练",
          type: "checkbox",
          value: value(selected, "usable_for_sft"),
        },
        {
          name: "usable_for_preference",
          label: "用于偏好训练",
          type: "checkbox",
          value: value(selected, "usable_for_preference"),
        },
        {
          name: "usable_for_rl",
          label: "用于在线强化学习",
          type: "checkbox",
          value: value(selected, "usable_for_rl"),
        },
      );
    setModal({
      title: "人工审核",
      fields,
      submit: async (data) => {
        await updateRecord(
          `/datasets/${datasetView}/${selected.id}`,
          datasetView === "preference-pairs"
            ? data
            : {
                ...data,
                usable_for_sft: data.usable_for_sft === "on",
                usable_for_preference: data.usable_for_preference === "on",
                usable_for_rl: data.usable_for_rl === "on",
              },
        );
      },
    });
  }
  function create() {
    if (selected?.id === "production-readiness") setSelected(null);
    if (section === "tasks")
      setModal({
        title: "创建代码任务",
        fields: [
          { name: "title", label: "任务名称", required: true },
          { name: "repo_path", label: "仓库路径", required: true },
          {
            name: "test_command",
            label: "测试命令",
            value: "pytest -q",
            required: true,
          },
          { name: "goal", label: "修复目标", type: "textarea", required: true },
          { name: "max_steps", label: "最大步骤", type: "number", value: "30" },
          {
            name: "max_tokens",
            label: "Token 预算",
            type: "number",
            value: "80000",
          },
        ],
        submit: async (data) => {
          await action("/tasks", {
            title: data.title,
            repo_path: data.repo_path,
            test_command: data.test_command,
            goal: data.goal,
            workspace_id: user.workspace_id || "workspace_default",
            budget: {
              max_steps: Number(data.max_steps),
              max_tokens: Number(data.max_tokens),
            },
          });
        },
      });
    else if (section === "research")
      setModal({
        title: "创建研究",
        fields: [
          {
            name: "question",
            label: "研究问题",
            type: "textarea",
            required: true,
          },
          { name: "domain", label: "研究领域", value: "general" },
        ],
        submit: async (data) => {
          await action("/research/briefs", {
            ...data,
            workspace_id: user.workspace_id || "workspace_default",
            max_papers: 5,
          });
        },
      });
    else if (section === "training")
      setModal({
        title: "准备训练任务",
        fields: [
          { name: "base_model", label: "基础模型白名单名称", required: true },
          {
            name: "method",
            label: "训练方法",
            options: ["sft", "dpo", "grpo"],
          },
          {
            name: "item_ids",
            label: "已审核样本 ID（每行一个；与在线会话二选一）",
            type: "textarea",
          },
          {
            name: "online_rl_session_id",
            label: "已冻结在线 RL 会话 ID（仅 GRPO）",
          },
          {
            name: "max_steps",
            label: "训练步数",
            type: "number",
            value: "100",
          },
          {
            name: "max_seconds",
            label: "最长运行秒数",
            type: "number",
            value: "3600",
          },
          {
            name: "gpu_count",
            label: "每节点 GPU 数（0 使用平台默认）",
            type: "number",
            value: "0",
          },
          {
            name: "world_size",
            label: "每节点 DDP 进程数",
            type: "number",
            value: "1",
          },
          {
            name: "node_count",
            label: "训练节点数（大于 1 使用 Kubernetes PyTorchJob）",
            type: "number",
            value: "1",
          },
        ],
        submit: async (data) => {
          const itemIds = data.item_ids.split(/\s+/).filter(Boolean);
          const gpuCount = Number(data.gpu_count);
          const worldSize = Number(data.world_size);
          const nodeCount = Number(data.node_count);
          if (!itemIds.length && !data.online_rl_session_id.trim())
            throw new Error("请填写已审核样本 ID 或已冻结在线 RL 会话 ID");
          if (worldSize > 1 && gpuCount > 0 && gpuCount < worldSize)
            throw new Error("每节点 GPU 数不能小于每节点 DDP 进程数");
          if (nodeCount < 1) throw new Error("训练节点数至少为 1");
          await action("/training/jobs", {
            ...data,
            workspace_id: user.workspace_id || "workspace_default",
            item_ids: itemIds,
            online_rl_session_id: data.online_rl_session_id.trim() || null,
            max_steps: Number(data.max_steps),
            max_seconds: Number(data.max_seconds),
            gpu_count: gpuCount,
            world_size: worldSize,
            node_count: nodeCount,
          });
        },
      });
    else if (section === "health")
      setModal({
        title: "创建健康 Demo 会话",
        fields: [
          {
            name: "user_id",
            label: "用户标识",
            value: "demo-user",
            required: true,
          },
          {
            name: "signals",
            label: "传感器 JSON（心率、血氧、步数、睡眠）",
            type: "textarea",
            value:
              '{"heart_rate": 72, "spo2": 98, "steps": 6800, "sleep_hours": 7}',
          },
          {
            name: "multimodal_text",
            label: "文本或语音转写内容",
            type: "textarea",
          },
        ],
        submit: async (data) => {
          let signals: unknown;
          try {
            signals = JSON.parse(data.signals || "{}");
          } catch {
            throw new Error("传感器 JSON 格式无效");
          }
          if (!signals || Array.isArray(signals) || typeof signals !== "object")
            throw new Error("传感器 JSON 必须是对象");
          await action("/health-demo/sessions", {
            user_id: data.user_id,
            signals,
            multimodal_text: data.multimodal_text,
          });
        },
      });
    else if (section === "repositories")
      setModal({
        title: "接入代码仓库",
        fields: [
          { name: "name", label: "仓库名称", required: true },
          {
            name: "provider",
            label: "仓库类型",
            options: ["local", "github", "generic_git"],
          },
          { name: "url", label: "远端地址或本地源路径" },
          { name: "local_path", label: "本地工作目录" },
          {
            name: "default_branch",
            label: "默认分支",
            value: "main",
            required: true,
          },
          { name: "credential_ref", label: "凭据引用（环境变量或密钥 URI）" },
          { name: "github_installation_id", label: "GitHub App 安装 ID" },
        ],
        submit: async (data) => {
          if (data.credential_ref.trim() && data.github_installation_id.trim())
            throw new Error("凭据引用与 GitHub App 安装 ID 只能填写一个");
          await action("/integrations/repositories", {
            name: data.name,
            provider: data.provider,
            url: data.url.trim() || null,
            local_path: data.local_path.trim() || null,
            default_branch: data.default_branch,
            credential_ref: data.credential_ref.trim() || null,
            github_installation_id: data.github_installation_id.trim()
              ? Number(data.github_installation_id)
              : null,
            workspace_id: user.workspace_id || "workspace_default",
          });
        },
      });
    else if (section === "models")
      setModal({
        title: "登记模型网关",
        fields: [
          { name: "id", label: "模型配置 ID", required: true },
          {
            name: "provider",
            label: "模型提供方",
            options: [
              "openai_compatible",
              "qwen",
              "dashscope",
              "anthropic",
              "gemini",
              "mock",
            ],
          },
          { name: "model_name", label: "模型名称", required: true },
          {
            name: "role",
            label: "用于任务类型",
            options: ["coding", "research", "critic"],
          },
          { name: "api_key_env", label: "密钥环境变量" },
          { name: "base_url", label: "服务地址（可选）" },
          { name: "billing_export_url", label: "账单导出 HTTPS 地址（可选）" },
          {
            name: "billing_export_format",
            label: "账单导出格式",
            options: ["csv", "json"],
            value: "csv",
          },
          {
            name: "billing_export_api_key_env",
            label: "账单导出密钥环境变量（可选）",
          },
          {
            name: "strategy_ids",
            label: "限定策略 ID（每行一个）",
            type: "textarea",
          },
          {
            name: "context_window",
            label: "上下文窗口",
            type: "number",
            value: "128000",
            required: true,
          },
          {
            name: "cost_per_1k_tokens",
            label: "每千 Token 成本",
            type: "number",
            value: "0.002",
            required: true,
          },
          {
            name: "status",
            label: "配置状态",
            options: ["active", "candidate", "disabled"],
          },
        ],
        submit: async (data) => {
          const config: Row = { source: "manual" };
          if (data.api_key_env.trim())
            config.api_key_env = data.api_key_env.trim();
          if (data.base_url.trim()) config.base_url = data.base_url.trim();
          if (data.billing_export_url.trim()) {
            config.billing_export = {
              url: data.billing_export_url.trim(),
              format: data.billing_export_format,
              api_key_env: data.billing_export_api_key_env.trim() || undefined,
            };
          }
          const strategyIds = data.strategy_ids.split(/\s+/).filter(Boolean);
          if (strategyIds.length) config.strategy_ids = strategyIds;
          await action("/models", {
            id: data.id,
            provider: data.provider,
            model_name: data.model_name,
            role: data.role,
            context_window: Number(data.context_window),
            cost_per_1k_tokens: Number(data.cost_per_1k_tokens),
            config,
            status: data.status,
          });
        },
      });
    else if (section === "strategies")
      setModal({
        title: "创建智能体策略",
        fields: [
          { name: "id", label: "策略 ID", required: true },
          { name: "name", label: "策略名称", required: true },
          { name: "description", label: "策略说明", type: "textarea" },
          { name: "repair_prompt", label: "修复提示词", type: "textarea" },
          { name: "critic_prompt", label: "审查提示词", type: "textarea" },
          { name: "max_steps", label: "最大步骤", type: "number", value: "20" },
        ],
        submit: async (data) => {
          await action("/strategies", {
            ...data,
            task_type: "coding",
            planner_prompt: "",
            tool_selection_policy: {},
            runtime_config: {},
            memory_enabled: false,
            status: "draft",
            max_steps: Number(data.max_steps),
          });
        },
      });
    else if (section === "evaluations")
      setModal({
        title: "创建评测",
        fields: [
          {
            name: "benchmark_name",
            label: "评测集名称",
            value: "coding_golden_v1",
            required: true,
          },
          {
            name: "task_ids",
            label: "任务 ID（每行一个）",
            type: "textarea",
            required: true,
          },
          {
            name: "agent_strategy_id",
            label: "策略 ID",
            value: "repair_with_critic_v3",
            required: true,
          },
          {
            name: "policy_version_id",
            label: "策略版本 ID",
            value: "policy_default_v1",
            required: true,
          },
          { name: "model_name", label: "模型名称（可选）" },
        ],
        submit: async (data) => {
          await action("/evaluations/runs", {
            ...data,
            task_ids: data.task_ids.split(/\s+/).filter(Boolean),
            model_name: data.model_name || null,
          });
        },
      });
    else if (section === "memory")
      setModal({
        title: "创建项目记忆",
        fields: [
          { name: "key", label: "记忆键", required: true },
          { name: "summary", label: "摘要", type: "textarea", required: true },
          {
            name: "memory_type",
            label: "记忆类型",
            options: ["failure", "project", "strategy"],
          },
          {
            name: "scope",
            label: "作用范围",
            options: ["project", "task", "run"],
          },
        ],
        submit: async (data) => {
          await action("/memory/items", {
            ...data,
            workspace_id: user.workspace_id || "workspace_default",
            status: "active",
            detail_json: {},
          });
        },
      });
    else if (section === "extensions")
      setModal({
        title: "登记扩展工具",
        fields: [
          { name: "id", label: "扩展 ID", required: true },
          { name: "name", label: "扩展名称", required: true },
          {
            name: "type",
            label: "扩展类型",
            options: ["mcp_tool", "skill", "hook"],
          },
          { name: "description", label: "说明", type: "textarea" },
          { name: "config", label: "配置 JSON", type: "textarea", value: "{}" },
        ],
        submit: async (data) => {
          let config: unknown;
          try {
            config = JSON.parse(data.config || "{}");
          } catch {
            throw new Error("扩展配置必须是 JSON 对象");
          }
          if (!config || Array.isArray(config) || typeof config !== "object")
            throw new Error("扩展配置必须是 JSON 对象");
          await action("/extensions", { ...data, config, status: "enabled" });
        },
      });
    else if (section === "system")
      setModal({
        title: "创建用户",
        fields: [
          { name: "name", label: "姓名", required: true },
          { name: "email", label: "邮箱", type: "email", required: true },
          {
            name: "workspace_id",
            label: "工作区 ID",
            value: String(user.workspace_id || "workspace_default"),
          },
          {
            name: "role",
            label: "角色",
            options: ["viewer", "operator", "admin"],
          },
        ],
        submit: async (data) => {
          await action("/users", data);
        },
      });
    else if (section === "datasets")
      setModal(
        datasetView === "preference-pairs"
          ? {
              title: "创建偏好对",
              fields: [
                { name: "chosen_run_id", label: "优选运行 ID", required: true },
                {
                  name: "rejected_run_id",
                  label: "对照运行 ID",
                  required: true,
                },
                {
                  name: "rationale",
                  label: "偏好依据",
                  type: "textarea",
                  required: true,
                },
              ],
              submit: async (data) => {
                await action("/datasets/preference-pairs", data);
              },
            }
          : {
              title: "创建标注样本",
              fields: [
                { name: "agent_run_id", label: "运行 ID", required: true },
                {
                  name: "quality_label",
                  label: "质量标签",
                  options: ["good", "medium", "bad"],
                },
                {
                  name: "trace_type",
                  label: "轨迹类型",
                  options: ["SUCCESS_TRACE", "FAILURE_TRACE"],
                },
                { name: "notes", label: "标注备注", type: "textarea" },
              ],
              submit: async (data) => {
                await action("/datasets/trace-items", {
                  ...data,
                  use_case:
                    data.trace_type === "FAILURE_TRACE"
                      ? "failure_case"
                      : "sft_candidate",
                });
              },
            },
      );
  }
  function login() {
    openLogin();
  }
  const shown = serverFilteredSections.includes(section)
    ? items
    : items.filter(
        (item) =>
          (!query ||
            JSON.stringify(item).toLowerCase().includes(query.toLowerCase())) &&
          (!status || item.status === status),
      );
  const title = nav.find((item) => item[0] === section)?.[1];
  const sectionNumber = String(
    nav.findIndex((item) => item[0] === section) + 1,
  ).padStart(2, "0");
  const tableLoading =
    busy || !sessionReady || (loadedSection !== section && !error);
  const runExportParams = new URLSearchParams();
  if (activeWorkspaceId) runExportParams.set("workspace_id", activeWorkspaceId);
  if (deferredQuery) runExportParams.set("query", deferredQuery);
  if (status) runExportParams.set("status", status);
  if (runProjectFilter) runExportParams.set("project_name", runProjectFilter);
  if (runBranchFilter) runExportParams.set("branch", runBranchFilter);
  if (runModelFilter) runExportParams.set("model_name", runModelFilter);
  if (runStrategyFilter) runExportParams.set("strategy_id", runStrategyFilter);
  const runFromQuery = dateTimeQueryValue(runFromTime) || runRangeStart(runRange);
  const runToQuery = dateTimeQueryValue(runToTime);
  if (runFromQuery) runExportParams.set("from_time", runFromQuery);
  if (runToQuery) runExportParams.set("to_time", runToQuery);
  runExportParams.set("sort", runSort);
  const runExportHref = `/api/v1/run-records/export.csv?${runExportParams.toString()}`;
  useEffect(() => {
    document.title = title
      ? `${title} · ResearchForge`
      : "ResearchForge · 智能体工作台";
  }, [title]);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/tasks" prefetch={false}>
          <img
            className="brand-mark"
            src="/icon"
            alt=""
            width={36}
            height={36}
          />
          <span>
            ResearchForge<small>智能体工作台</small>
          </span>
        </Link>
        <nav>
          {navGroups.map(([group, entries]) => (
            <div className="nav-group" key={group}>
              <p className="nav-group-label">{group}</p>
              {entries.map(([path, name, Icon]) => (
                <Link
                  key={path}
                  href={`/${path}`}
                  prefetch={false}
                  className={section === path ? "active" : ""}
                >
                  <Icon size={17} />
                  {name}
                  {section === path && <span className="nav-dot" />}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="online-dot" />
          {session
            ? `${value(user, "name")} · ${display(user.role)}`
            : "未登录"}
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="topbar-left">
            <span className="topbar-product">ResearchForge</span>
            <span className="topbar-divider">/</span>
            {session ? (
              <label className="workspace-switch">
                <span>工作区</span>
                <select
                  aria-label="当前工作区"
                  value={String(workspace.id)}
                  onChange={(event) => {
                    void api("/auth/workspace", "POST", {
                      workspace_id: event.target.value,
                    })
                      .then((data) => {
                        window.sessionStorage.setItem(
                          "researchforge.workspace-id",
                          event.target.value,
                        );
                        setWorkspaceHint(event.target.value);
                        setSession(data);
                        setSelected(null);
                        setOffset(0);
                      })
                      .catch((err) => setError(err.message));
                  }}
                >
                  {((session.available_workspaces || []) as Row[]).map(
                    (item) => (
                      <option key={item.id} value={item.id}>
                        {String(item.name)}
                      </option>
                    ),
                  )}
                </select>
              </label>
            ) : (
              <span className="workspace-guest">默认工作区</span>
            )}
          </div>
          <div className="topbar-right">
            {session && (
              <span className="session-chip">
                <span className="online-dot" />
                {value(user, "name") || "管理员"}
              </span>
            )}
            {session ? (
              <button
                onClick={() => {
                  void api("/auth/logout", "POST", {})
                    .then(() => {
                      window.sessionStorage.removeItem(
                        "researchforge.workspace-id",
                      );
                      setWorkspaceHint("");
                      setSession(null);
                      redirectToLogin();
                    })
                    .catch((err) => setError(err.message));
                }}
              >
                <LogOut size={15} />
                退出
              </button>
            ) : (
              <button onClick={login}>
                <LogIn size={15} />
                登录
              </button>
            )}
          </div>
        </header>
        <section className="page-content">
          <div className="heading">
            <div className="heading-copy">
              <p className="eyebrow">RESEARCHFORGE / {sectionNumber}</p>
              <h1>{title}</h1>
              <p className="page-subtitle">{sectionHints[section]}</p>
            </div>
            <div className="actions">
              <IconButton
                label="刷新"
                icon={RefreshCw}
                disabled={busy}
                onClick={() => void load()}
              />
              {[
                "tasks",
                "repositories",
                "research",
                "training",
                "system",
                "datasets",
                "strategies",
                "models",
                "evaluations",
                "memory",
                "extensions",
              ].includes(section) &&
                (admin ||
                  ([
                    "tasks",
                    "repositories",
                    "research",
                    "datasets",
                    "memory",
                  ].includes(section) &&
                    user.role === "operator")) && (
                  <button className="primary" disabled={busy} onClick={create}>
                    <Plus size={16} />
                    新建
                  </button>
                )}
            </div>
          </div>
          {offline && (
            <div className="offline-banner" role="status">
              网络连接已断开，恢复后可点击刷新。
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              <span>{error}</span>
              <button
                type="button"
                onClick={() => {
                  setError("");
                  void load();
                }}
                disabled={busy}
              >
                重试
              </button>
            </div>
          )}
          {notice && (
            <div className="notice" role="status">
              {notice}
            </div>
          )}
          <div className={`workspace-layout${selected ? " has-detail" : ""}`}>
            <div className="workspace-primary">
              {section === "datasets" && (
                <>
                  <div
                    className="view-tabs"
                    role="tablist"
                    aria-label="数据类型"
                  >
                    {[
                      ["trace-items", "轨迹样本"],
                      ["preference-pairs", "偏好对"],
                    ].map(([key, name]) => (
                      <button
                        key={key}
                        role="tab"
                        aria-selected={datasetView === key}
                        onClick={() => {
                          setDatasetView(key);
                          setSelected(null);
                          setOffset(0);
                          setStatus("");
                          setQuery("");
                        }}
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                  <div className="secondary-actions">
                    <a
                      href={
                        datasetView === "trace-items"
                          ? "/api/v1/datasets/export?status=approved"
                          : "/api/v1/datasets/preference-pairs/export"
                      }
                    >
                      导出审核数据
                    </a>
                    <a href="/api/v1/datasets/export?trace_type=FAILURE_TRACE">
                      导出失败样本
                    </a>
                    <a href="/api/v1/datasets/training-bundle/export">
                      导出训练数据包
                    </a>
                  </div>
                </>
              )}
              <div className="filters">
                <label className="search-field">
                  <Search size={16} />
                  <input
                    aria-label={
                      serverFilteredSections.includes(section)
                        ? "搜索全部记录"
                        : "搜索当前页"
                    }
                    placeholder={
                      serverFilteredSections.includes(section)
                        ? "搜索全部记录"
                        : "搜索当前页"
                    }
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setOffset(0);
                    }}
                  />
                </label>
                {query && (
                  <IconButton
                    label="清除搜索"
                    icon={X}
                    onClick={() => {
                      setQuery("");
                      setOffset(0);
                    }}
                  />
                )}
                {section !== "audit" && (
                  <label className="status-filter">
                    <span>状态</span>
                    <select
                      aria-label="状态筛选"
                      value={status}
                      onChange={(e) => {
                        setStatus(e.target.value);
                        setOffset(0);
                      }}
                    >
                      <option value="">全部状态</option>
                      {[
                        ...new Set([
                          status,
                          ...items.map((item) => value(item, "status")),
                          ...(serverFilteredSections.includes(section)
                            ? [
                                "queued",
                                "running",
                                "paused",
                                "completed",
                                "failed",
                                "cancelled",
                                "blocked",
                              ]
                            : []),
                        ]),
                      ]
                        .filter(Boolean)
                        .map((item) => (
                          <option key={item} value={item}>
                            {display(item)}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
                {section === "audit" && (
                  <>
                    <label className="status-filter">
                      <span>操作</span>
                      <input
                        aria-label="操作筛选"
                        placeholder="例如 repository.publish"
                        value={auditAction}
                        onChange={(event) => {
                          setAuditAction(event.target.value);
                          setOffset(0);
                        }}
                      />
                    </label>
                    <label className="status-filter">
                      <span>资源</span>
                      <input
                        aria-label="资源类型筛选"
                        placeholder="例如 repository"
                        value={auditResourceType}
                        onChange={(event) => {
                          setAuditResourceType(event.target.value);
                          setOffset(0);
                        }}
                      />
                    </label>
                    <label className="status-filter">
                      <span>决策</span>
                      <select
                        aria-label="决策筛选"
                        value={auditDecision}
                        onChange={(event) => {
                          setAuditDecision(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="">全部决策</option>
                        {["allow", "approved", "completed", "queued", "denied", "rejected", "failed", "revoked"].map((item) => (
                          <option key={item} value={item}>{display(item)}</option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
                {(query || status || auditAction || auditResourceType || auditDecision) && (
                  <button
                    className="filter-reset"
                    onClick={() => {
                      setQuery("");
                      setStatus("");
                      setAuditAction("");
                      setAuditResourceType("");
                      setAuditDecision("");
                      setOffset(0);
                    }}
                  >
                    重置筛选
                  </button>
                )}
                <span className="record-count">
                  {tableLoading ? "正在加载" : `${total} 条记录`}
                </span>
              </div>
              {section === "runs" && (
                <>
                  <div className="run-filters" aria-label="运行记录高级筛选">
                    <label>
                      <span>项目</span>
                      <select
                        aria-label="项目筛选"
                        value={runProjectFilter}
                        onChange={(event) => {
                          setRunProjectFilter(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="">全部项目</option>
                        {(Array.isArray(runFacets.projects)
                          ? (runFacets.projects as unknown[])
                          : []
                        ).map((project) => (
                          <option key={String(project)} value={String(project)}>
                            {String(project)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>分支</span>
                      <select
                        aria-label="分支筛选"
                        value={runBranchFilter}
                        onChange={(event) => {
                          setRunBranchFilter(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="">全部分支</option>
                        {(Array.isArray(runFacets.branches)
                          ? (runFacets.branches as unknown[])
                          : []
                        ).map((branch) => (
                          <option key={String(branch)} value={String(branch)}>
                            {String(branch)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>模型</span>
                      <select
                        aria-label="模型筛选"
                        value={runModelFilter}
                        onChange={(event) => {
                          setRunModelFilter(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="">全部模型</option>
                        {(Array.isArray(runFacets.models)
                          ? (runFacets.models as unknown[])
                          : []
                        ).map((model) => (
                          <option key={String(model)} value={String(model)}>
                            {String(model)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>策略</span>
                      <select
                        aria-label="策略筛选"
                        value={runStrategyFilter}
                        onChange={(event) => {
                          setRunStrategyFilter(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="">全部策略</option>
                        {(Array.isArray(runFacets.strategies)
                          ? (runFacets.strategies as unknown[])
                          : []
                        ).map((strategy) => (
                          <option key={String(strategy)} value={String(strategy)}>
                            {String(strategy)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>时间范围</span>
                      <select
                        aria-label="时间范围"
                        value={runRange}
                        onChange={(event) => {
                          setRunRange(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="all">全部时间</option>
                        <option value="24h">最近 24 小时</option>
                        <option value="7d">最近 7 天</option>
                        <option value="30d">最近 30 天</option>
                      </select>
                    </label>
                    <label>
                      <span>开始时间</span>
                      <input
                        aria-label="开始时间"
                        type="datetime-local"
                        value={runFromTime}
                        onChange={(event) => {
                          setRunFromTime(event.target.value);
                          setOffset(0);
                        }}
                      />
                    </label>
                    <label>
                      <span>结束时间</span>
                      <input
                        aria-label="结束时间"
                        type="datetime-local"
                        value={runToTime}
                        onChange={(event) => {
                          setRunToTime(event.target.value);
                          setOffset(0);
                        }}
                      />
                    </label>
                    <label>
                      <span>排序</span>
                      <select
                        aria-label="运行记录排序"
                        value={runSort}
                        onChange={(event) => {
                          setRunSort(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="completed_desc">最近完成</option>
                        <option value="created_desc">最近创建</option>
                        <option value="duration_desc">耗时最长</option>
                        <option value="cost_desc">成本最高</option>
                      </select>
                    </label>
                    {(runProjectFilter || runBranchFilter || runModelFilter || runStrategyFilter || runRange !== "all" || runFromTime || runToTime || runSort !== "completed_desc") && (
                      <button
                        className="filter-reset"
                        onClick={() => {
                          setRunProjectFilter("");
                          setRunBranchFilter("");
                          setRunModelFilter("");
                          setRunStrategyFilter("");
                          setRunRange("all");
                          setRunFromTime("");
                          setRunToTime("");
                          setRunSort("completed_desc");
                          setOffset(0);
                        }}
                      >
                        清除高级筛选
                      </button>
                    )}
                    <a className="run-export" href={runExportHref} download>
                      <Download size={15} /> 导出当前结果
                    </a>
                    <button
                      className="run-export"
                      type="button"
                      onClick={() => {
                        if (!navigator.clipboard) {
                          setNotice("当前浏览器不允许复制链接。");
                          return;
                        }
                        void navigator.clipboard.writeText(window.location.href)
                          .then(() => setNotice("已复制当前筛选链接。"))
                          .catch(() => setNotice("当前浏览器不允许复制链接。"));
                      }}
                    >
                      <Copy size={15} /> 复制筛选链接
                    </button>
                  </div>
                  <RunOverview
                    data={{ summary: runSummary }}
                    onSelectProject={(project) => {
                      setRunProjectFilter(project);
                      setOffset(0);
                    }}
                  />
                  {comparisonRunIds.length > 0 && (
                    <div className="run-comparison-toolbar">
                      <span>已选择 {comparisonRunIds.length}/2 条运行记录</span>
                      <button
                        disabled={comparisonRunIds.length !== 2 || busy}
                        onClick={() => void compareSelectedRuns()}
                      >
                        <GitBranch size={16} /> 对比运行
                      </button>
                      <button
                        className="filter-reset"
                        onClick={() => {
                          setComparisonRunIds([]);
                          setRunComparison(null);
                        }}
                      >
                        清除选择
                      </button>
                    </div>
                  )}
                  {bulkRunIds.length > 0 && (
                    <div className="run-comparison-toolbar run-bulk-toolbar">
                      <span>已选择 {bulkRunIds.length} 条运行记录</span>
                      <button
                        disabled={busy}
                        onClick={requestBulkCancel}
                      >
                        <Square size={15} /> 批量取消
                      </button>
                      <button
                        disabled={busy}
                        onClick={() => void bulkRunAction("retry")}
                      >
                        <RotateCcw size={15} /> 批量重试
                      </button>
                      <button
                        className="filter-reset"
                        onClick={() => setBulkRunIds([])}
                      >
                        清除选择
                      </button>
                    </div>
                  )}
                  {runComparison && (
                    <RunComparisonPanel
                      data={runComparison}
                      onClose={() => setRunComparison(null)}
                    />
                  )}
                </>
              )}
              {section === "system" && (
                <div className="secondary-actions">
                  <button disabled={busy} onClick={() => void api(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/quality`).then((data) => {setSelected({id:"workspace-quality",name:"项目质量与成本"});setDetails(data);}).catch((err) => setError(err.message))}>质量与成本</button>
                  <button disabled={!admin || busy} onClick={() => void api(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/retention`).then((data) => {setSelected({id:"retention-preview",name:"产物保留预览"});setDetails(data);}).catch((err) => setError(err.message))}>产物保留预览</button>

                  <button
                    onClick={() => {
                      void api("/system/dependencies")
                        .then((data) => {
                          setSelected({
                            id: "dependencies",
                            name: "依赖连接检查",
                          });
                          setDetails(data);
                        })
                        .catch((err) => setError(err.message));
                    }}
                    disabled={!admin || busy}
                  >
                    检查依赖
                  </button>
                  <button
                    onClick={inspectProductionReadiness}
                    disabled={!admin || busy}
                  >
                    生产检查
                  </button>
                  <button
                    onClick={() => {
                      void api("/integrations/github/automation-readiness")
                        .then((data) => {
                          setSelected({
                            id: "github-automation-readiness",
                            name: "GitHub 自动修复就绪检查",
                          });
                          setDetails(data);
                        })
                        .catch((err) => setError(err.message));
                    }}
                    disabled={!admin || busy}
                  >
                    GitHub 检查
                  </button>
                  <button
                    onClick={() => {
                      void api("/auth/enterprise/status")
                        .then((data) => {
                          setSelected({ id: "sso", name: "企业登录" });
                          setDetails(data);
                        })
                        .catch((err) => setError(err.message));
                    }}
                    disabled={busy}
                  >
                    企业身份
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      setModal({
                        title: "LDAP 登录",
                        fields: [
                          { name: "username", label: "用户名", required: true },
                          {
                            name: "password",
                            label: "密码",
                            type: "password",
                            required: true,
                          },
                        ],
                        submit: async (data) => {
                          await api(
                            "/auth/enterprise/ldap/login",
                            "POST",
                            data,
                          );
                          setSession(await api("/auth/session"));
                          await load();
                        },
                      })
                    }
                  >
                    LDAP 登录
                  </button>
                  <a href="/api/v1/auth/enterprise/oidc/login">OIDC 登录</a>
                  <a href="/api/v1/auth/enterprise/saml/login">SAML 登录</a>
                </div>
              )}
              <div
                className={`table-wrap ${section === "runs" ? "run-table" : ""} ${selected?.id === "production-readiness" ? "readiness-table" : ""}`}
                aria-busy={tableLoading}
              >
                <table>
                  <thead>
                    {section === "runs" ? (
                      <tr>
                        <th>任务 / 项目</th>
                        <th>结果</th>
                        <th>完成时间</th>
                        <th>耗时 / 成本</th>
                        <th>模型 / 策略</th>
                        <th className="right">操作</th>
                      </tr>
                    ) : section === "audit" ? (
                      <tr>
                        <th>操作 / 资源</th>
                        <th>执行者</th>
                        <th>决策 / 时间</th>
                        <th className="right">操作</th>
                      </tr>
                    ) : (
                      <tr>
                        <th>
                          {section === "tasks"
                            ? "任务"
                            : section === "research"
                              ? "研究问题"
                              : section === "repositories"
                                ? "名称 / 类型"
                                : "名称 / 标识"}
                        </th>
                        <th>状态</th>
                        <th>
                          {section === "training"
                            ? "方法 / 样本数"
                            : section === "system"
                              ? "角色 / 工作区"
                              : "创建时间"}
                        </th>
                        <th className="right">操作</th>
                      </tr>
                    )}
                  </thead>
                  <tbody>
                    {tableLoading && !shown.length
                      ? [0, 1, 2, 3, 4].map((index) => (
                          <tr key={`loading-${index}`} className="skeleton-row">
                            {Array.from(
                              { length: section === "runs" ? 6 : 4 },
                              (_, cell) => (
                                <td key={cell}>
                                  <span
                                    className={`skeleton-bar ${cell === 0 ? "skeleton-long" : cell === (section === "runs" ? 5 : 3) ? "skeleton-action" : cell === 1 ? "skeleton-short" : ""}`}
                                    aria-label={
                                      cell === 0 ? "正在加载" : undefined
                                    }
                                  />
                                </td>
                              ),
                            )}
                          </tr>
                        ))
                      : shown.map((row) =>
                          section === "runs" ? (
                            <RunListRow
                              key={row.id}
                              row={row}
                              selected={selected?.id === row.id}
                              busy={busy}
                              onInspect={(entry) => void inspect(entry)}
                              onShare={(entry) => {
                                const runId = value(entry, "id");
                                if (!runId) return;
                                inspectedDeepLink.current = runId;
                                setDeepLinkedRunId(runId);
                                void inspect(entry);
                                const url = new URL(window.location.href);
                                url.searchParams.set("run", runId);
                                window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
                                if (!navigator.clipboard) {
                                  setNotice("链接已生成，但浏览器不允许自动复制。");
                                  return;
                                }
                                void navigator.clipboard.writeText(url.toString())
                                  .then(() => setNotice("已复制可直达该运行详情的链接。"))
                                  .catch(() => setNotice("链接已生成，但浏览器不允许自动复制。"));
                              }}
                              onRunAction={(path) => runAction(path)}
                              comparisonSelected={comparisonRunIds.includes(value(row, "id"))}
                              onToggleComparison={toggleRunComparison}
                              bulkSelected={bulkRunIds.includes(value(row, "id"))}
                              onToggleBulk={toggleBulkRun}
                            />
                          ) : section === "audit" ? (
                            <tr
                              key={row.id}
                              className={selected?.id === row.id ? "selected" : ""}
                            >
                              <td>
                                <button className="row-title" onClick={() => void inspect(row)}>
                                  {value(row, "action") || "未命名操作"}
                                </button>
                                <small>
                                  {value(row, "resource_type") || "未知资源"} · {value(row, "resource_id") || "未关联记录"}
                                </small>
                              </td>
                              <td>{value(row, "actor_id") || "系统"}</td>
                              <td>
                                <span className={`status status-${value(row, "decision") === "failed" || value(row, "decision") === "denied" ? "failed" : "completed"}`}>
                                  {display(value(row, "decision")) || "已记录"}
                                </span>
                                <small>
                                  {value(row, "created_at")
                                    ? new Date(value(row, "created_at")).toLocaleString("zh-CN", { hour12: false })
                                    : "未记录"}
                                </small>
                              </td>
                              <td className="right">
                                <div className="row-actions">
                                  <IconButton label="查看详情" icon={ChevronRight} onClick={() => void inspect(row)} />
                                </div>
                              </td>
                            </tr>
                          ) : (
                            <tr
                              key={row.id}
                              className={
                                selected?.id === row.id ? "selected" : ""
                              }
                            >
                              <td>
                                <button
                                  className="row-title"
                                  onClick={() => void inspect(row)}
                                >
                                  {value(row, "title") ||
                                    value(row, "task_title") ||
                                    value(row, "question") ||
                                    value(row, "name") ||
                                    value(row, "base_model") ||
                                    value(row, "model_name") ||
                                    row.id}
                                </button>
                                <small>{row.id}</small>
                              </td>
                              <td>
                                <span
                                  className={`status status-${value(row, "status")}`}
                                >
                                  {display(row.status)}
                                </span>
                              </td>
                              <td>
                                {section === "repositories" ? (
                                  <>
                                    <div className="repository-list-meta">
                                      <span className={`provider-badge provider-${value(row, "provider") || "unknown"}`}>
                                        {value(row, "provider") === "github" ? "GitHub" : value(row, "provider") === "local" ? "本地仓库" : display(row.provider) || "未知来源"}
                                      </span>
                                      <span className="repository-capability-hint">
                                        {value(row, "provider") === "github" ? "支持推送与 Draft PR" : "不支持 GitHub PR"}
                                      </span>
                                    </div>
                                    {value(row, "url") && <small title={value(row, "url")}>{value(row, "url")}</small>}
                                  </>
                                ) : section === "runs" ? (
                                  <>
                                    {value(row, "model_name")}
                                    <small>
                                      {value(row, "agent_strategy_id")}
                                    </small>
                                  </>
                                ) : section === "training" ? (
                                  `${value(row, "method").toUpperCase()} · ${value(row, "sample_count")}`
                                ) : section === "system" ? (
                                  <>
                                    {display(row.role)}
                                    <small>{value(row, "workspace_id")}</small>
                                  </>
                                ) : value(row, "created_at") ? (
                                  new Date(
                                    value(row, "created_at"),
                                  ).toLocaleString("zh-CN", { hour12: false })
                                ) : (
                                  "未记录"
                                )}
                              </td>
                              <td className="right">
                                <div className="row-actions">
                                  {section === "tasks" && (
                                    <IconButton
                                      label="运行任务"
                                      icon={Play}
                                      disabled={busy}
                                      onClick={() =>
                                        runAction(`/tasks/${row.id}/runs`, {
                                          agent_strategy_id:
                                            "repair_with_critic_v3",
                                        })
                                      }
                                    />
                                  )}
                                  {section === "training" && admin && (
                                    <>
                                      <IconButton
                                        label={
                                          row.status === "prepared"
                                            ? "开始训练"
                                            : "同步训练状态"
                                        }
                                        icon={
                                          row.status === "prepared"
                                            ? Play
                                            : RefreshCw
                                        }
                                        onClick={() =>
                                          runAction(
                                            `/training/jobs/${row.id}/${row.status === "prepared" ? "start" : "refresh"}`,
                                          )
                                        }
                                        disabled={busy}
                                      />
                                      <IconButton
                                        label="取消训练"
                                        icon={Square}
                                        disabled={
                                          busy ||
                                          [
                                            "succeeded",
                                            "failed",
                                            "cancelled",
                                          ].includes(value(row, "status"))
                                        }
                                        onClick={() =>
                                          runAction(
                                            `/training/jobs/${row.id}/cancel`,
                                          )
                                        }
                                      />
                                    </>
                                  )}
                                  {section === "runs" &&
                                    ["queued", "running", "paused"].includes(
                                      value(row, "status"),
                                    ) && (
                                      <IconButton
                                        label={
                                          row.status === "paused"
                                            ? "恢复运行"
                                            : "取消运行"
                                        }
                                        icon={
                                          row.status === "paused"
                                            ? Play
                                            : Square
                                        }
                                        onClick={() =>
                                          runAction(
                                            `/runs/${row.id}/${row.status === "paused" ? "resume" : "cancel"}`,
                                          )
                                        }
                                        disabled={busy}
                                      />
                                    )}
                                  <IconButton
                                    label="查看详情"
                                    icon={ChevronRight}
                                    onClick={() => void inspect(row)}
                                  />
                                </div>
                              </td>
                            </tr>
                          ),
                        )}
                    {!tableLoading && !shown.length && (
                      <tr>
                        <td
                          colSpan={section === "runs" ? 6 : 4}
                          className="empty"
                        >
                          <div className="empty-state">
                            <span className="empty-icon">
                              <Database size={20} />
                            </span>
                            <strong>
                              {query || status ? "没有匹配记录" : "暂无记录"}
                            </strong>
                            <span>
                              {query || status
                                ? "请调整搜索词或状态筛选"
                                : "当前工作区还没有数据"}
                            </span>
                            {!query &&
                              !status &&
                              [
                                "tasks",
                                "repositories",
                                "research",
                                "training",
                                "system",
                                "datasets",
                                "strategies",
                                "models",
                                "evaluations",
                                "memory",
                                "extensions",
                              ].includes(section) &&
                              (admin ||
                                ([
                                  "tasks",
                                  "repositories",
                                  "research",
                                  "datasets",
                                  "memory",
                                ].includes(section) &&
                                  user.role === "operator")) && (
                                <button className="primary" onClick={create}>
                                  <Plus size={16} />
                                  创建第一条
                                </button>
                              )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="pagination">
                <span>第 {Math.floor(offset / 25) + 1} 页</span>
                <IconButton
                  label="上一页"
                  icon={ChevronLeft}
                  disabled={offset === 0 || tableLoading}
                  onClick={() => setOffset(Math.max(0, offset - 25))}
                />
                <IconButton
                  label="下一页"
                  icon={ChevronRight}
                  disabled={offset + 25 >= total || tableLoading}
                  onClick={() => setOffset(offset + 25)}
                />
              </div>
              {/* Load the primary table before secondary panels. This keeps navigation responsive
            when the same-origin proxy has several database-backed requests to forward. */}
              {secondarySection === section &&
                section === "system" &&
                session &&
                selected?.id !== "production-readiness" && (
                  <WorkspaceMembers
                    key={String(workspace.id)}
                    session={session}
                    onSession={setSession}
                  />
                )}
              {secondarySection === section && section === "training" && (
                <>
                  <TrainingStatus />
                  <OnlineRLPanel
                    workspaceId={String(workspace.id || "workspace_default")}
                  />
                </>
              )}
              {secondarySection === section && section === "models" && (
                <ModelUsagePanel
                  workspaceId={String(workspace.id || "workspace_default")}
                  admin={admin}
                />
              )}
              {secondarySection === section && section === "registry" && (
                <CanaryPanel
                  admin={admin}
                  workspaceId={String(workspace.id || "")}
                />
              )}
            </div>
            {selected && (
              <section
                className="detail detail-drawer"
                role="dialog"
                aria-label="记录详情"
              >
                <header>
                  <div>
                    <p className="eyebrow">记录详情</p>
                    <h2>
                      {value(selected, "title") ||
                        value(selected, "task_title") ||
                        value(selected, "name") ||
                        selected.id}
                    </h2>
                  </div>
                  <IconButton
                    label="关闭详情"
                    icon={X}
                    onClick={() => {
                      setSelected(null);
                      if (section !== "runs") return;
                      inspectedDeepLink.current = "";
                      setDeepLinkedRunId("");
                    }}
                  />
                </header>
                {section === "research" && (
                  <ResearchRecovery
                    briefId={String(selected.id)}
                    writable={(
                      (session?.permissions || []) as string[]
                    ).includes("research:write")}
                  />
                )}
                {section === "datasets" && user.role !== "viewer" && (
                  <button onClick={review}>
                    <Check size={16} />
                    人工审核
                  </button>
                )}
                {section === "health" && user.role !== "viewer" && (
                  <button
                    onClick={() =>
                      setModal({
                        title: "人工复核健康结果",
                        fields: [
                          {
                            name: "decision",
                            label: "复核决定",
                            options: ["accept", "follow_up", "escalate"],
                          },
                          {
                            name: "notes",
                            label: "复核备注",
                            type: "textarea",
                          },
                        ],
                        submit: async (data) => {
                          await action(
                            `/health-demo/sessions/${selected.id}/review`,
                            data,
                          );
                          await inspect(selected);
                        },
                      })
                    }
                  >
                    <Check size={16} />
                    人工复核
                  </button>
                )}
                {section === "approvals" &&
                  ((session?.permissions || []) as string[]).includes(
                    "approvals:decide",
                  ) &&
                  value(selected, "status") === "pending" && (
                    <button
                      onClick={() =>
                        setModal({
                          title: "审批工具调用",
                          fields: [
                            {
                              name: "decision",
                              label: "审批决定",
                              options: ["approved", "denied"],
                            },
                            {
                              name: "reason",
                              label: "审批理由",
                              type: "textarea",
                            },
                          ],
                          submit: async (data) => {
                            await action(`/approvals/${selected.id}/decision`, {
                              ...data,
                              decided_by: value(user, "name") || "operator",
                            });
                            await inspect(selected);
                          },
                        })
                      }
                    >
                      <Check size={16} />
                      审批
                    </button>
                  )}
                {section === "strategies" && admin && (
                  <div className="secondary-actions">
                    {[
                      ["candidate", "设为候选"],
                      ["promote", "发布策略"],
                      ["rollback", "回滚策略"],
                    ]
                      .filter(
                        ([operation]) =>
                          operation !== "candidate" ||
                          value(selected, "status") === "draft",
                      )
                      .map(([operation, name]) => (
                        <button
                          key={operation}
                          onClick={() =>
                            setModal({
                              title: name,
                              fields: [
                                {
                                  name: "evaluation_run_id",
                                  label: "评测运行 ID（发布时必填）",
                                  required: operation === "promote",
                                },
                                {
                                  name: "reason",
                                  label: "操作理由",
                                  type: "textarea",
                                },
                              ],
                              submit: async (data) => {
                                await action(
                                  `/strategies/${selected.id}/${operation}`,
                                  {
                                    ...data,
                                    evaluation_run_id:
                                      data.evaluation_run_id || null,
                                  },
                                );
                                await inspect(selected);
                              },
                            })
                          }
                        >
                          {name}
                        </button>
                      ))}
                  </div>
                )}
                {section === "evaluations" && admin && (
                  <div className="secondary-actions">
                    <a href={`/api/v1/evaluations/runs/${selected.id}/report`}>
                      下载评测报告
                    </a>
                    <button
                      onClick={() =>
                        setModal({
                          title: "执行发布门禁",
                          fields: [
                            {
                              name: "min_success_rate",
                              label: "最低成功率",
                              value: "0.8",
                            },
                            {
                              name: "min_avg_score",
                              label: "最低平均分",
                              value: "0",
                            },
                            {
                              name: "release_stage",
                              label: "发布阶段",
                              options: ["candidate", "canary", "active"],
                            },
                            {
                              name: "canary_percentage",
                              label: "灰度百分比",
                              type: "number",
                              value: "0",
                            },
                          ],
                          submit: async (data) => {
                            await action("/evaluations/release-gate", {
                              evaluation_run_id: selected.id,
                              min_success_rate: Number(data.min_success_rate),
                              min_avg_score: Number(data.min_avg_score),
                              release_stage: data.release_stage,
                              canary_percentage: Number(data.canary_percentage),
                            });
                          },
                        })
                      }
                    >
                      <Check size={16} />
                      发布门禁
                    </button>
                  </div>
                )}
                {section === "memory" && user.role !== "viewer" && (
                  <button
                    onClick={() =>
                      setModal({
                        title: "编辑项目记忆",
                        fields: [
                          {
                            name: "key",
                            label: "记忆键",
                            value: value(selected, "key"),
                            required: true,
                          },
                          {
                            name: "summary",
                            label: "摘要",
                            type: "textarea",
                            value: value(selected, "summary"),
                            required: true,
                          },
                          {
                            name: "memory_type",
                            label: "记忆类型",
                            options: ["failure", "project", "strategy"],
                            value: value(selected, "memory_type"),
                          },
                          {
                            name: "scope",
                            label: "作用范围",
                            options: ["project", "task", "run"],
                            value: value(selected, "scope"),
                          },
                          {
                            name: "status",
                            label: "状态",
                            options: ["active", "inactive"],
                            value: value(selected, "status"),
                          },
                        ],
                        submit: async (data) => {
                          await updateRecord(`/memory/items/${selected.id}`, {
                            ...data,
                            workspace_id: user.workspace_id,
                          });
                        },
                      })
                    }
                  >
                    <Settings size={16} />
                    编辑记忆
                  </button>
                )}
                {section === "models" && admin && (
                  <div className="secondary-actions">
                    <button
                      onClick={() => {
                        void api<Page>(
                          "/models/health?status=active&verify_connectivity=true",
                        )
                          .then((result) =>
                            setDetails({
                              health: result.items.find(
                                (item) => item.model_id === selected.id,
                              ) || { reason: "MODEL_HEALTH_NOT_FOUND" },
                            }),
                          )
                          .catch((err) => setError(err.message));
                      }}
                    >
                      <Activity size={16} />
                      健康检查
                    </button>
                    <button
                      onClick={() => {
                        void api("/models/route", "POST", {
                          task_type:
                            value(selected, "role") === "research"
                              ? "research"
                              : "coding",
                          requested_model: value(selected, "model_name"),
                          estimated_tokens: 1024,
                        })
                          .then((route) => setDetails({ ...details, route }))
                          .catch((err) => setError(err.message));
                      }}
                    >
                      路由预检
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "测试模型调用",
                          fields: [
                            {
                              name: "prompt",
                              label: "测试提示",
                              type: "textarea",
                              required: true,
                            },
                            {
                              name: "max_tokens",
                              label: "最大输出 Token",
                              type: "number",
                              value: "128",
                              required: true,
                            },
                          ],
                          submit: async (data) => {
                            const invocation = await api(
                              "/models/invoke",
                              "POST",
                              {
                                task_type:
                                  value(selected, "role") === "research"
                                    ? "research"
                                    : "coding",
                                requested_model: value(selected, "model_name"),
                                prompt: data.prompt,
                                max_tokens: Number(data.max_tokens),
                              },
                            );
                            setDetails({ ...details, invocation });
                            setNotice("模型测试调用已完成");
                          },
                        })
                      }
                    >
                      <Play size={16} />
                      测试调用
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "更新模型网关",
                          fields: [
                            {
                              name: "provider",
                              label: "模型提供方",
                              options: [
                                "openai_compatible",
                                "qwen",
                                "dashscope",
                                "anthropic",
                                "gemini",
                                "mock",
                              ],
                              value: value(selected, "provider"),
                            },
                            {
                              name: "model_name",
                              label: "模型名称",
                              value: value(selected, "model_name"),
                              required: true,
                            },
                            {
                              name: "role",
                              label: "用于任务类型",
                              options: ["coding", "research", "critic"],
                              value: value(selected, "role"),
                            },
                            {
                              name: "context_window",
                              label: "上下文窗口",
                              type: "number",
                              value: value(selected, "context_window"),
                              required: true,
                            },
                            {
                              name: "cost_per_1k_tokens",
                              label: "每千 Token 成本",
                              type: "number",
                              value: value(selected, "cost_per_1k_tokens"),
                              required: true,
                            },
                            {
                              name: "config",
                              label: "连接配置 JSON",
                              type: "textarea",
                              value: JSON.stringify(
                                selected.config || {},
                                null,
                                2,
                              ),
                              required: true,
                            },
                            {
                              name: "status",
                              label: "配置状态",
                              options: ["active", "candidate", "disabled"],
                              value: value(selected, "status"),
                            },
                          ],
                          submit: async (data) => {
                            let config: unknown;
                            try {
                              config = JSON.parse(data.config);
                            } catch {
                              throw new Error("连接配置必须是 JSON 对象");
                            }
                            if (
                              !config ||
                              Array.isArray(config) ||
                              typeof config !== "object"
                            )
                              throw new Error("连接配置必须是 JSON 对象");
                            const model = await api<Row>("/models", "POST", {
                              id: selected.id,
                              provider: data.provider,
                              model_name: data.model_name,
                              role: data.role,
                              context_window: Number(data.context_window),
                              cost_per_1k_tokens: Number(
                                data.cost_per_1k_tokens,
                              ),
                              config,
                              status: data.status,
                            });
                            setSelected(model);
                            setNotice("模型配置已保存");
                            await load();
                          },
                        })
                      }
                    >
                      <Settings size={16} />
                      更新配置
                    </button>
                  </div>
                )}
                {section === "extensions" && admin && (
                  <div className="secondary-actions">
                    <button
                      onClick={() => {
                        void api(`/extensions/${selected.id}/health`)
                          .then((data) => setDetails(data))
                          .catch((err) => setError(err.message));
                      }}
                    >
                      健康检查
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "调用扩展",
                          fields: [
                            {
                              name: "action",
                              label: "操作名称",
                              value: "inspect",
                              required: true,
                            },
                            {
                              name: "input",
                              label: "输入 JSON",
                              type: "textarea",
                              value: "{}",
                            },
                          ],
                          submit: async (data) => {
                            let input: unknown;
                            try {
                              input = JSON.parse(data.input || "{}");
                            } catch {
                              throw new Error("扩展输入必须是 JSON 对象");
                            }
                            if (
                              !input ||
                              Array.isArray(input) ||
                              typeof input !== "object"
                            )
                              throw new Error("扩展输入必须是 JSON 对象");
                            setDetails(
                              await action(
                                `/extensions/${selected.id}/invoke`,
                                { action: data.action, input },
                              ),
                            );
                          },
                        })
                      }
                    >
                      调用扩展
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "更新扩展状态",
                          fields: [
                            {
                              name: "status",
                              label: "扩展状态",
                              options: ["enabled", "disabled", "error"],
                              value: value(selected, "status"),
                            },
                            {
                              name: "reason",
                              label: "变更原因",
                              type: "textarea",
                            },
                          ],
                          submit: async (data) => {
                            await api(
                              `/extensions/${selected.id}/status`,
                              "PATCH",
                              data,
                            );
                            await load();
                          },
                        })
                      }
                    >
                      更新状态
                    </button>
                  </div>
                )}
                {section === "repositories" && user.role !== "viewer" && (
                  <div className="secondary-actions">
                    <button
                      onClick={() => {
                        void api(
                          `/integrations/repositories/${selected.id}/health?verify_access=true`,
                        )
                          .then((health) => setDetails({ ...details, health }))
                          .catch((err) => setError(err.message));
                      }}
                    >
                      <Activity size={16} />
                      健康检查
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        runAction(
                          `/integrations/repositories/${selected.id}/sync`,
                        )
                      }
                    >
                      <RefreshCw size={16} />
                      同步仓库
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "自动修复仓库",
                          fields: [
                            {
                              name: "title",
                              label: "任务标题",
                              value: "代码修复",
                              required: true,
                            },
                            {
                              name: "goal",
                              label: "修复目标",
                              type: "textarea",
                              required: true,
                            },
                            {
                              name: "test_command",
                              label: "测试命令",
                              value: String((((details.projectProfile as Row)?.recipes || []) as Row[])[0]?.test_command || "pytest -q"),
                            },
                            { name: "setup_commands", label: "依赖准备命令（每行一条，可选）", type: "textarea", value: (((((details.projectProfile as Row)?.recipes || []) as Row[])[0]?.setup_commands || []) as string[]).join("\n") },
                            { name: "policy_version_id", label: "工具策略 ID（Node 项目需允许 node/npm）", value: "policy_default_v1", required: true },
                            { name: "model_name", label: "模型名称（可选）" },
                            {
                              name: "max_runtime_seconds",
                              label: "运行时间预算（秒，30–7200）",
                              type: "number",
                              value: "1800",
                              required: true,
                            },
                            {
                              name: "publish",
                              label: "生成并发布补丁",
                              type: "checkbox",
                            },
                            {
                              name: "push",
                              label: "推送到远端",
                              type: "checkbox",
                            },
                            {
                              name: "create_pull_request",
                              label: "创建 GitHub 草稿 Pull Request",
                              type: "checkbox",
                            },
                            {
                              name: "allow_cached_on_sync_failure",
                              label: "远端同步失败时使用最近成功缓存验收",
                              type: "checkbox",
                            },
                          ],
                          submit: async (data) => {
                            const publish = data.publish === "on";
                            const push = data.push === "on";
                            const createPullRequest =
                              data.create_pull_request === "on";
                            const allowCachedOnSyncFailure =
                              data.allow_cached_on_sync_failure === "on";
                            if (createPullRequest && !push)
                              throw new Error(
                                "创建 Pull Request 前必须确认推送到远端",
                              );
                            if (/&&|\|\||[;|<>]/.test(data.test_command || ""))
                              throw new Error("测试命令不支持命令连接符，请使用单一命令或仓库验证脚本，例如 python verify.py");
                            const setupCommands = (data.setup_commands || "").split("\n").map((command) => command.trim()).filter(Boolean);
                            if (setupCommands.length > 5 || setupCommands.some((command) => command.length > 2000 || /&&|\|\||[;|<>]/.test(command)))
                              throw new Error("依赖准备最多填写 5 条单一命令，每条不超过 2000 字符");
                            const runtimeSeconds = Number(data.max_runtime_seconds);
                            if (!Number.isInteger(runtimeSeconds) || runtimeSeconds < 30 || runtimeSeconds > 7200)
                              throw new Error("运行时间预算必须为 30–7200 秒的整数");
                            const repairResult = await action(
                              `/integrations/repositories/${selected.id}/repair`,
                              {
                                title: data.title,
                                goal: data.goal,
                                test_command: data.test_command || null,
                                setup_commands: setupCommands,
                                policy_version_id: data.policy_version_id.trim() || "policy_default_v1",
                                model_name: data.model_name.trim() || null,
                                budget: { max_runtime_seconds: runtimeSeconds },
                                publish,
                                push,
                                create_pull_request: createPullRequest,
                                allow_cached_on_sync_failure: allowCachedOnSyncFailure,
                                branch: "researchforge/repair",
                                body: "由 ResearchForge 自动生成，请在合并前完成代码审查。",
                              },
                            );
                            const repairJob = (repairResult.job || {}) as Row;
                            const repairJobId = value(repairJob, "id");
                            const query = encodeURIComponent(value(selected, "name"));
                            window.location.assign(`/runs?query=${query}${repairJobId ? `&job=${encodeURIComponent(repairJobId)}` : ""}`);
                          },
                        })
                      }
                    >
                      <Bot size={16} />
                      自动修复
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "编辑仓库接入",
                          fields: [
                            {
                              name: "name",
                              label: "仓库名称",
                              value: value(selected, "name"),
                              required: true,
                            },
                            {
                              name: "provider",
                              label: "仓库类型",
                              options: ["local", "github", "generic_git"],
                              value: value(selected, "provider"),
                            },
                            {
                              name: "url",
                              label: "远端地址或本地源路径",
                              value: value(selected, "url"),
                            },
                            {
                              name: "local_path",
                              label: "本地工作目录",
                              value: value(selected, "local_path"),
                            },
                            {
                              name: "default_branch",
                              label: "默认分支",
                              value: value(selected, "default_branch"),
                              required: true,
                            },
                            {
                              name: "credential_ref",
                              label: "凭据引用（环境变量或密钥 URI）",
                              value: value(selected, "credential_ref"),
                            },
                            {
                              name: "github_installation_id",
                              label: "GitHub App 安装 ID",
                              value: value(selected, "github_installation_id"),
                            },
                            {
                              name: "status",
                              label: "接入状态",
                              options: ["active", "disabled"],
                              value: value(selected, "status"),
                            },
                          ],
                          submit: async (data) => {
                            if (
                              data.credential_ref.trim() &&
                              data.github_installation_id.trim()
                            )
                              throw new Error(
                                "凭据引用与 GitHub App 安装 ID 只能填写一个",
                              );
                            await updateRecord(
                              `/integrations/repositories/${selected.id}`,
                              {
                                ...data,
                                url: data.url.trim() || null,
                                local_path: data.local_path.trim() || null,
                                credential_ref:
                                  data.credential_ref.trim() || null,
                                github_installation_id:
                                  data.github_installation_id.trim()
                                    ? Number(data.github_installation_id)
                                    : null,
                              },
                            );
                          },
                        })
                      }
                    >
                      <Settings size={16} />
                      编辑接入
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "发布修复补丁",
                          fields: [
                            {
                              name: "run_id",
                              label: "已完成运行 ID",
                              required: true,
                            },
                            {
                              name: "branch",
                              label: "发布分支（留空自动生成）",
                            },
                            {
                              name: "title",
                              label: "提交标题",
                              required: true,
                            },
                            {
                              name: "body",
                              label: "Pull Request 说明",
                              type: "textarea",
                            },
                            {
                              name: "push",
                              label: "推送到远端",
                              type: "checkbox",
                            },
                            {
                              name: "create_pull_request",
                              label: "创建 GitHub 草稿 Pull Request",
                              type: "checkbox",
                            },
                          ],
                          submit: async (data) => {
                            const push = data.push === "on";
                            const createPullRequest =
                              data.create_pull_request === "on";
                            if (createPullRequest && !push)
                              throw new Error(
                                "创建 Pull Request 前必须确认推送到远端",
                              );
                            const requestedBranch =
                              data.branch.trim() || "researchforge/repair";
                            const payload: PublishPayload = {
                              run_id: data.run_id,
                              branch: requestedBranch,
                              title: data.title,
                              body: data.body,
                              push,
                              create_pull_request: createPullRequest,
                            };
                            const preview = await api<Row>(
                              `/integrations/repositories/${selected.id}/publish/preview`,
                              "POST",
                              payload,
                            );
                            const summary = (preview.preview || {}) as Row;
                            setPublishConfirmation({
                              repositoryId: String(selected.id),
                              payload: {
                                ...payload,
                                branch:
                                  value(summary, "branch") || requestedBranch,
                              },
                              preview: summary,
                            });
                            setNotice("补丁预检通过，请确认发布。");
                          },
                        })
                      }
                    >
                      <GitBranch size={16} />
                      发布补丁
                    </button>
                  </div>
                )}
                {section === "system" &&
                  admin &&
                  selected.email !== undefined && (
                    <button
                      onClick={() =>
                        setModal({
                          title: "编辑用户",
                          fields: [
                            {
                              name: "name",
                              label: "姓名",
                              value: value(selected, "name"),
                              required: true,
                            },
                            {
                              name: "role",
                              label: "角色",
                              options: ["viewer", "operator", "admin"],
                              value: value(selected, "role"),
                            },
                            {
                              name: "status",
                              label: "账户状态",
                              options: ["active", "disabled"],
                              value: value(selected, "status"),
                            },
                            {
                              name: "workspace_id",
                              label: "工作区 ID",
                              value: value(selected, "workspace_id"),
                              required: true,
                            },
                          ],
                          submit: async (data) => {
                            await updateRecord(`/users/${selected.id}`, data);
                            void api("/auth/session")
                              .then(setSession)
                              .catch((err) => setError(err.message));
                          },
                        })
                      }
                    >
                      <Settings size={16} />
                      编辑用户
                    </button>
                  )}
                {section === "registry" && (
                  <>
                    <div className="secondary-actions">
                      {admin && (
                        <button
                          onClick={() =>
                            setModal({
                              title: "发布模型",
                              fields: [
                                {
                                  name: "model_config_id",
                                  label: "推理服务配置 ID",
                                  required: true,
                                },
                                {
                                  name: "evaluation_id",
                                  label: "真实评测 ID",
                                  required: true,
                                },
                              ],
                              submit: async (data) => {
                                await action(
                                  `/model-registry/versions/${selected.id}/promote`,
                                  data,
                                );
                                await inspect(selected);
                              },
                            })
                          }
                        >
                          <Check size={16} />
                          评测门禁与发布
                        </button>
                      )}
                    </div>
                    <h3>发布历史</h3>
                    {((details.deployments || []) as Row[]).length === 0 && (
                      <p>暂无发布记录</p>
                    )}
                    {((details.deployments || []) as Row[]).map(
                      (deployment) => (
                        <div className="deployment-row" key={deployment.id}>
                          <span>
                            {deployment.id}
                            <small>{display(deployment.status)}</small>
                          </span>
                          {admin && deployment.status === "active" && (
                            <button
                              disabled={busy}
                              onClick={() =>
                                setModal({
                                  title: "回滚模型发布",
                                  fields: [
                                    {
                                      name: "confirm",
                                      label: "确认回滚此发布",
                                      type: "checkbox",
                                    },
                                  ],
                                  submit: async (data) => {
                                    if (data.confirm !== "on")
                                      throw new Error("请确认回滚操作");
                                    await action(
                                      `/model-registry/deployments/${deployment.id}/rollback`,
                                    );
                                    await inspect(
                                      await api(
                                        `/model-registry/versions/${selected.id}`,
                                      ),
                                    );
                                  },
                                })
                              }
                            >
                              回滚
                            </button>
                          )}
                        </div>
                      ),
                    )}
                  </>
                )}
                {section === "research" && (
                  <div className="secondary-actions">
                    <button
                      onClick={() =>
                        setModal({
                          title: "执行实验",
                          fields: [
                            {
                              name: "source",
                              label: "Python 实验代码",
                              type: "textarea",
                              required: true,
                            },
                            {
                              name: "timeout",
                              label: "最长运行秒数",
                              type: "number",
                              value: "60",
                            },
                          ],
                          submit: async (data) => {
                            const result = await action(
                              `/research/briefs/${selected.id}/execute`,
                              {
                                cells: [data.source],
                                timeout_seconds: Number(data.timeout),
                              },
                            );
                            setDetails(result || {});
                          },
                        })
                      }
                    >
                      <Play size={16} />
                      执行实验
                    </button>
                    <button
                      onClick={() =>
                        setModal({
                          title: "启动自主研究",
                          fields: [
                            {
                              name: "max_iterations",
                              label: "最大实验轮数",
                              type: "number",
                              value: "3",
                            },
                            {
                              name: "timeout_seconds",
                              label: "每轮最长秒数",
                              type: "number",
                              value: "60",
                            },
                          ],
                          submit: async (data) => {
                            const result = await action(
                              `/research/briefs/${selected.id}/cycles`,
                              {
                                max_iterations: Number(data.max_iterations),
                                timeout_seconds: Number(data.timeout_seconds),
                              },
                            );
                            setDetails(result || {});
                          },
                        })
                      }
                    >
                      <FlaskConical size={16} />
                      自主研究循环
                    </button>
                  </div>
                )}
                {section === "runs" ? (
                  <>
                    {value(selected, "status") !== "cancelled" &&
                      user.role !== "viewer" && (
                        <div className="secondary-actions">
                          <button
                            disabled={busy}
                            onClick={() =>
                              setModal({
                                title: "委派子任务",
                                fields: [
                                  {
                                    name: "title",
                                    label: "子任务名称",
                                    value: `协作修复：${value(selected, "task_title") || selected.id}`,
                                    required: true,
                                  },
                                  {
                                    name: "goal",
                                    label: "子任务目标",
                                    type: "textarea",
                                    required: true,
                                  },
                                  {
                                    name: "test_command",
                                    label: "测试命令（留空继承父任务）",
                                  },
                                  {
                                    name: "agent_backend",
                                    label: "执行 Runtime",
                                    options: [
                                      "langgraph",
                                      "native",
                                      "openhands",
                                      "mini_swe_agent",
                                    ],
                                    value: "langgraph",
                                  },
                                  {
                                    name: "model_name",
                                    label: "模型名称（留空继承父运行）",
                                  },
                                ],
                                submit: async (data) => {
                                  const delegated = await action(
                                    "/a2a/delegations",
                                    {
                                      parent_run_id: selected.id,
                                      title: data.title,
                                      goal: data.goal,
                                      test_command:
                                        data.test_command.trim() || null,
                                      agent_backend: data.agent_backend,
                                      model_name: data.model_name.trim() || null,
                                    },
                                  );
                                  setDetails((current) => ({
                                    ...current,
                                    delegation: delegated,
                                  }));
                                  setNotice(
                                    `已创建受父任务预算约束的子运行 ${value(delegated || {}, "id")}。`,
                                  );
                                },
                              })
                            }
                          >
                            <GitBranch size={16} />
                            委派子任务
                          </button>
                        </div>
                      )}
                    {!['queued', 'running', 'paused'].includes(value(selected, 'status')) && user.role !== 'viewer' && (
                      <div className="secondary-actions">
                        <button
                          disabled={busy}
                          onClick={() =>
                            setModal({
                              title: "使用新配置复跑",
                              fields: [
                                {
                                  name: "model_name",
                                  label: "模型名称",
                                  value: value(selected, "model_name"),
                                },
                                {
                                  name: "agent_strategy_id",
                                  label: "智能体策略",
                                  value: value(selected, "agent_strategy_id"),
                                },
                                {
                                  name: "policy_version_id",
                                  label: "策略版本",
                                  value: value(selected, "policy_version_id"),
                                },
                              ],
                              submit: async (data) => {
                                const retried = await action(`/runs/${selected.id}/retry`, {
                                  model_name: data.model_name.trim() || null,
                                  agent_strategy_id: data.agent_strategy_id.trim() || null,
                                  policy_version_id: data.policy_version_id.trim() || null,
                                });
                                setNotice(`已创建复跑记录 ${value(retried || {}, "id")}。`);
                                setModal(null);
                              },
                            })
                          }
                        >
                          <Play size={16} /> 使用新配置复跑
                        </button>
                      </div>
                    )}
                    <RunProgressPanel progress={(details.progress || {}) as Row} />
                    {deepLinkedJob && (rowData(deepLinkedJob.result_json).sync_fallback || rowData(deepLinkedJob.result_json).publish) && (
                      <section className="repository-health" aria-label="仓库修复发布结果">
                        <p className="eyebrow">仓库修复结果</p>
                        {Boolean(rowData(deepLinkedJob.result_json).sync_fallback) && (
                          <p className="notice">远端同步暂时失败，本次运行已按显式配置使用最近一次成功同步的本地缓存完成验收。代码版本：{value(selected, "branch") || "main"}。</p>
                        )}
                        {value(rowData(deepLinkedJob.result_json).publish as Row, "status") === "skipped" && (
                          <p className="notice">测试和评审已通过，未检测到生产代码变更，已跳过发布，不会创建重复 Pull Request。</p>
                        )}
                      </section>
                    )}
                    <RunSummary
                      run={selected}
                      artifacts={(details.artifacts || []) as Row[]}
                    />
                    <h3>工具调用</h3>
                    {((details.calls || []) as Row[]).map((call) => (
                      <details key={call.id}>
                        <summary>
                          <span>{value(call, "tool_name")}</span>
                          <span className="status">{display(call.status)}</span>
                          <small>{value(call, "duration_ms")} ms</small>
                        </summary>
                        <h4>输入</h4>
                        <Raw data={call.input} />
                        <h4>输出</h4>
                        <Raw data={call.output} />
                      </details>
                    ))}
                    <h3>证据与产物</h3>
                    {((details.artifacts || []) as Row[]).map((artifact) => (
                      <details key={artifact.id}>
                        <summary>{value(artifact, "name")}</summary>
                        {typeof artifact.content === "string" && (
                          <a href={`/api/v1/artifacts/${encodeURIComponent(String(artifact.id))}/download`} download>下载产物</a>
                        )}
                        <Raw data={artifact.content} />
                      </details>
                    ))}
                    {details.delegation && (
                      <details open>
                        <summary>最近委派的子任务</summary>
                        <Raw data={details.delegation} />
                      </details>
                    )}
                    <details>
                      <summary>原始运行记录</summary>
                      <Raw data={selected} />
                    </details>
                  </>
                ) : section === "system" && selected.id === "workspace-quality" ? (
                  <WorkspaceQualityPanel data={details} />
                ) : section === "system" && selected.id === "retention-preview" ? (
                  <section className="repository-health"><h3>过期日志保留计划</h3><p>只清理终态运行的过期日志内容，保留补丁、报告、审计记录和保留标记。</p><p>本批 {String(details.selected_count || 0)} 个日志，预计释放 {String(details.estimated_bytes || 0)} 字节。</p>
                    <button disabled={!admin || busy || !Number(details.selected_count)} onClick={() => { if (!window.confirm(`确认清理本批 ${String(details.selected_count)} 个过期日志内容？此操作无法直接撤销。`)) return; void api(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/retention?confirmation_digest=${encodeURIComponent(String(details.confirmation_digest))}`,"POST",{}).then((data) => {setDetails(data);setNotice(`已清理 ${String(data.expired_count)} 个日志内容，元数据已保留。`);}).catch((err) => setError(err.message));}}>确认清理本批过期日志</button><Raw data={details} /></section>
                ) : section === "repositories" ? (
                  <>
                    <RepositoryHealthPanel
                      health={(details.health || {}) as Row}
                    />
                    <ProjectProfilePanel profile={(details.projectProfile || {}) as Row} />
                    <RepositoryIssueAutomationPanel
                      repository={selected}
                      webhook={(details.webhook || {}) as Row}
                    />
                    <RepositoryPublicationPanel
                      publications={(details.publications || []) as Row[]}
                      busy={busy}
                      onRefresh={(jobId) => {
                        void action(`/integrations/repositories/${selected.id}/publications/${jobId}/refresh`)
                          .then(() => inspect(selected))
                          .catch(() => {});
                      }}
                      onRollback={(jobId) => {
                        setPendingAction({
                          title: "关闭未合并 Pull Request",
                          description: "此操作只会关闭远端尚未合并的草稿 Pull Request，不会删除分支、提交或修改默认分支。",
                          path: `/integrations/repositories/${selected.id}/publications/${jobId}/rollback`,
                          body: {},
                          onComplete: async () => inspect(selected),
                        });
                      }}
                    />
                    <RecordDetails row={selected} />
                    {details.publish && (
                      <PublishResult result={details.publish as Row} />
                    )}
                    <details>
                      <summary>原始仓库记录</summary>
                      <Raw
                        data={{ repository: selected, health: details.health }}
                      />
                    </details>
                  </>
                ) : section === "training" ? (
                  <>
                    <RecordDetails row={selected} />
                    <div className="secondary-actions">
                      <button
                        onClick={() => {
                          void api(`/training/jobs/${selected.id}/logs`)
                            .then((logs) => setDetails({ ...details, logs }))
                            .catch((err) => setError(err.message));
                        }}
                      >
                        <Activity size={16} />
                        查看训练日志
                      </button>
                      {value(selected, "backend") === "kubernetes" && (
                        <button
                          onClick={() => {
                            void api(
                              `/training/jobs/${selected.id}/kubernetes-manifest`,
                            )
                              .then((manifest) =>
                                setDetails({
                                  ...details,
                                  kubernetes_manifest: manifest,
                                }),
                              )
                              .catch((err) => setError(err.message));
                          }}
                        >
                          <Settings size={16} />
                          查看 Kubernetes 清单
                        </button>
                      )}
                    </div>
                    {Object.keys(details).length > 0 && <Raw data={details} />}
                  </>
                ) : selected.id === "production-readiness" ? (
                  <ProductionReadiness data={details} />
                ) : (
                  <>
                    <RecordDetails row={selected} />
                    {Object.keys(details).length > 0 &&
                      section !== "registry" && <Raw data={details} />}
                  </>
                )}
              </section>
            )}
          </div>
        </section>
      </main>
      {modal && (
        <Form
          title={modal.title}
          fields={modal.fields}
          loginOptions={modal.loginOptions}
          onSubmit={modal.submit}
          onClose={() => setModal(null)}
        />
      )}
      {publishConfirmation && (
        <PublishConfirmation
          state={publishConfirmation}
          onConfirm={confirmPublish}
          onClose={() => setPublishConfirmation(null)}
        />
      )}
      {pendingAction && (
        <ConfirmationDialog
          title={pendingAction.title}
          description={pendingAction.description}
          confirmLabel="确认执行"
          onConfirm={async () => {
            const result = await action(pendingAction.path, pendingAction.body);
            await pendingAction.onComplete?.(result);
          }}
          onClose={() => setPendingAction(null)}
        />
      )}
    </div>
  );
}

function ModelUsagePanel({
  workspaceId,
  admin,
}: {
  workspaceId: string;
  admin: boolean;
}) {
  const [items, setItems] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Row>({});
  const [reconciliations, setReconciliations] = useState<Row[]>([]);
  const [imports, setImports] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState<{
    title: string;
    fields: Field[];
    submit: (data: Record<string, string>) => Promise<void>;
  } | null>(null);
  const load = useCallback(async () => {
    try {
      const [usage, billing, billingImports] = await Promise.all([
        api<Page & { summary: Row }>(
          `/models/usage?workspace_id=${encodeURIComponent(workspaceId)}&limit=10`,
        ),
        api<Page>(
          `/models/billing/reconciliations?workspace_id=${encodeURIComponent(workspaceId)}&limit=10`,
        ),
        api<Page>(
          `/models/billing/imports?workspace_id=${encodeURIComponent(workspaceId)}&limit=10`,
        ),
      ]);
      setItems(usage.items);
      setSummary(usage.summary || {});
      setReconciliations(billing.items);
      setImports(billingImports.items);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }, [workspaceId]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 700);
    return () => window.clearTimeout(timer);
  }, [load]);
  return (
    <section className="model-usage-panel">
      <header>
        <h2>模型用量账本</h2>
        <div className="secondary-actions">
          {admin && (
            <>
              <button
                onClick={() =>
                  setForm({
                    title: "录入供应商账单",
                    fields: [
                      { name: "provider", label: "模型提供方", required: true },
                      { name: "model_name", label: "模型名称（可选）" },
                      {
                        name: "period_start",
                        label: "账单开始时间",
                        type: "datetime-local",
                        required: true,
                      },
                      {
                        name: "period_end",
                        label: "账单结束时间",
                        type: "datetime-local",
                        required: true,
                      },
                      {
                        name: "actual_cost",
                        label: "实际金额",
                        type: "number",
                        required: true,
                      },
                      { name: "currency", label: "币种", value: "USD" },
                      {
                        name: "fx_rate_to_usd",
                        label: "折算 USD 汇率（非 USD 可选）",
                        type: "number",
                      },
                      {
                        name: "total_tokens",
                        label: "供应商 Token 总数",
                        type: "number",
                      },
                      { name: "invoice_reference", label: "账单或发票标识" },
                      {
                        name: "tolerance",
                        label: "允许偏差（折算 USD）",
                        type: "number",
                        value: "0.01",
                      },
                      { name: "notes", label: "核验备注", type: "textarea" },
                    ],
                    submit: async (data) => {
                      await api("/models/billing/reconciliations", "POST", {
                        workspace_id: workspaceId,
                        provider: data.provider,
                        model_name: data.model_name.trim() || null,
                        period_start: new Date(data.period_start).toISOString(),
                        period_end: new Date(data.period_end).toISOString(),
                        actual_cost: Number(data.actual_cost),
                        currency: data.currency.trim().toUpperCase() || "USD",
                        fx_rate_to_usd: data.fx_rate_to_usd
                          ? Number(data.fx_rate_to_usd)
                          : null,
                        total_tokens: data.total_tokens
                          ? Number(data.total_tokens)
                          : null,
                        invoice_reference:
                          data.invoice_reference.trim() || null,
                        tolerance: Number(data.tolerance),
                        notes: data.notes.trim() || null,
                      });
                      await load();
                    },
                  })
                }
              >
                <Plus size={16} />
                录入账单
              </button>
              <button
                onClick={() =>
                  setForm({
                    title: "批量导入供应商账单",
                    fields: [
                      { name: "provider", label: "模型提供方", required: true },
                      {
                        name: "format",
                        label: "导入格式",
                        options: ["csv", "json"],
                        value: "csv",
                      },
                      {
                        name: "default_currency",
                        label: "默认币种",
                        value: "USD",
                      },
                      {
                        name: "default_fx_rate_to_usd",
                        label: "默认折算 USD 汇率（非 USD 可选）",
                        type: "number",
                      },
                      {
                        name: "content",
                        label: "账单导出内容",
                        type: "textarea",
                        required: true,
                      },
                      {
                        name: "strict",
                        label: "遇到无效行则整体拒绝",
                        type: "checkbox",
                        value: "on",
                      },
                      { name: "notes", label: "导入备注", type: "textarea" },
                    ],
                    submit: async (data) => {
                      await api("/models/billing/imports", "POST", {
                        workspace_id: workspaceId,
                        provider: data.provider,
                        format: data.format,
                        default_currency:
                          data.default_currency.trim().toUpperCase() || "USD",
                        default_fx_rate_to_usd: data.default_fx_rate_to_usd
                          ? Number(data.default_fx_rate_to_usd)
                          : null,
                        content: data.content,
                        strict: data.strict === "on",
                        notes: data.notes.trim() || null,
                      });
                      await load();
                    },
                  })
                }
              >
                <Download size={16} />
                批量导入
              </button>
              <button
                onClick={() =>
                  setForm({
                    title: "同步已配置供应商账单",
                    fields: [
                      { name: "provider", label: "模型提供方", required: true },
                      {
                        name: "model_config_id",
                        label: "模型配置 ID（有多个导出配置时必填）",
                      },
                      {
                        name: "strict",
                        label: "遇到无效行则整体拒绝",
                        type: "checkbox",
                        value: "on",
                      },
                      { name: "notes", label: "同步备注", type: "textarea" },
                    ],
                    submit: async (data) => {
                      await api("/models/billing/imports/pull", "POST", {
                        workspace_id: workspaceId,
                        provider: data.provider,
                        model_config_id: data.model_config_id.trim() || null,
                        strict: data.strict === "on",
                        notes: data.notes.trim() || null,
                      });
                      await load();
                    },
                  })
                }
              >
                <RefreshCw size={16} />
                同步账单
              </button>
            </>
          )}
          <IconButton
            label="刷新模型账本"
            icon={RefreshCw}
            onClick={() => void load()}
          />
        </div>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="secondary-actions">
        <span>Token：{String(summary.total_tokens || 0)}</span>
        <span>预估成本：{String(summary.estimated_cost || 0)}</span>
        <span>可计费成本：{String(summary.billable_cost || 0)}</span>
        <span>回退：{String(summary.fallback_count || 0)} 次</span>
      </div>
      {!items.length && <p>暂无模型调用记录</p>}
      {items.map((item) => (
        <details key={String(item.id)}>
          <summary>
            <span>{String(item.model_name)}</span>
            <span>
              {String(item.total_tokens)} Token · {String(item.billable_cost)}
            </span>
            <small>{item.fallback_used ? "本地回退" : "真实调用"}</small>
          </summary>
          <Raw data={item} />
        </details>
      ))}
      <h3>供应商账单对账</h3>
      {!reconciliations.length && <p>暂无已录入账单</p>}
      {reconciliations.map((item) => (
        <details key={String(item.id)}>
          <summary>
            <span>
              {String(item.provider)}
              {item.model_name ? ` · ${String(item.model_name)}` : ""}
            </span>
            <span>
              {display(item.status)} · 偏差 {String(item.variance_cost)} USD
            </span>
          </summary>
          <Raw data={item} />
        </details>
      ))}
      <h3>账单导入记录</h3>
      {!imports.length && <p>暂无批量导入记录</p>}
      {imports.map((item) => (
        <details key={String(item.id)}>
          <summary>
            <span>
              {String(item.provider)} · {String(item.format).toUpperCase()}
            </span>
            <span>
              导入 {String(item.imported_rows)} / {String(item.total_rows)} 行
            </span>
          </summary>
          <Raw data={item} />
        </details>
      ))}
      {form && (
        <Form
          title={form.title}
          fields={form.fields}
          onSubmit={form.submit}
          onClose={() => setForm(null)}
        />
      )}
    </section>
  );
}

function WorkspaceMembers({
  session,
  onSession,
}: {
  session: Row;
  onSession: (session: Row) => void;
}) {
  const workspace = session.workspace as Row;
  const manage = ((session.permissions || []) as string[]).includes(
    "members:manage",
  );
  const [members, setMembers] = useState<Row[]>([]);
  const [invitations, setInvitations] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [token, setToken] = useState("");
  const [form, setForm] = useState<{
    title: string;
    fields: Field[];
    submit: (data: Record<string, string>) => Promise<void>;
  } | null>(null);
  const load = useCallback(async () => {
    try {
      setMembers(
        (await api<Page>(`/workspaces/${workspace.id}/members`)).items,
      );
      if (manage)
        setInvitations(
          (await api<Page>(`/workspaces/${workspace.id}/invitations`)).items,
        );
    } catch (err) {
      setError((err as Error).message);
    }
  }, [workspace.id, manage]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 700);
    return () => window.clearTimeout(timer);
  }, [load]);
  return (
    <section className="detail">
      <header>
        <h2>工作区成员</h2>
        <IconButton
          label="刷新成员"
          icon={RefreshCw}
          onClick={() => void load()}
        />
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="secondary-actions">
        {manage && (
          <button
            onClick={() =>
              setForm({
                title: "邀请成员",
                fields: [
                  { name: "user_id", label: "目标账户 ID", required: true },
                  {
                    name: "role",
                    label: "工作区角色",
                    options: ["viewer", "operator", "workspace_admin"],
                  },
                ],
                submit: async (data) => {
                  const result = await api(
                    `/workspaces/${workspace.id}/invitations`,
                    "POST",
                    data,
                  );
                  setToken(String(result.token));
                  await load();
                },
              })
            }
          >
            <Plus size={16} />
            邀请成员
          </button>
        )}
        <button
          onClick={() =>
            setForm({
              title: "接受邀请",
              fields: [{ name: "token", label: "邀请令牌", required: true }],
              submit: async (data) => {
                await api("/auth/invitations/accept", "POST", data);
                onSession(await api("/auth/session"));
                await load();
              },
            })
          }
        >
          <Check size={16} />
          接受邀请
        </button>
      </div>
      {token && (
        <div className="notice">
          <label>
            邀请令牌
            <textarea aria-label="邀请令牌" readOnly value={token} />
          </label>
          <IconButton
            label="关闭邀请令牌"
            icon={X}
            onClick={() => setToken("")}
          />
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>成员</th>
              <th>角色</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <tr key={member.id}>
                <td>
                  {String(member.name)}
                  <small>{String(member.user_id)}</small>
                </td>
                <td>{display(member.role)}</td>
                <td>{display(member.status)}</td>
                <td>
                  {manage && (
                    <IconButton
                      label={`编辑成员 ${member.name}`}
                      icon={Settings}
                      onClick={() =>
                        setForm({
                          title: "编辑成员",
                          fields: [
                            {
                              name: "role",
                              label: "工作区角色",
                              options: [
                                "viewer",
                                "operator",
                                "workspace_admin",
                              ],
                              value: String(member.role),
                            },
                            {
                              name: "status",
                              label: "成员状态",
                              options: ["active", "disabled"],
                              value: String(member.status),
                            },
                          ],
                          submit: async (data) => {
                            await api(
                              `/workspaces/${workspace.id}/members/${member.user_id}`,
                              "PATCH",
                              data,
                            );
                            await load();
                          },
                        })
                      }
                    />
                  )}
                </td>
              </tr>
            ))}
            {!members.length && (
              <tr>
                <td colSpan={4}>暂无成员</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {manage && (
        <details>
          <summary>邀请记录</summary>
          {invitations.map((invitation) => (
            <div className="deployment-row" key={invitation.id}>
              <span>
                {String(invitation.user_id)}
                <small>
                  {display(invitation.role)} · {display(invitation.status)}
                </small>
              </span>
              {invitation.status === "pending" && (
                <IconButton
                  label="撤销邀请"
                  icon={X}
                  onClick={() => {
                    void api(
                      `/workspaces/${workspace.id}/invitations/${invitation.id}`,
                      "DELETE",
                    )
                      .then(load)
                      .catch((err) => setError(err.message));
                  }}
                />
              )}
            </div>
          ))}
        </details>
      )}
      {form && (
        <Form
          title={form.title}
          fields={form.fields}
          onSubmit={form.submit}
          onClose={() => setForm(null)}
        />
      )}
    </section>
  );
}

function ResearchRecovery({
  briefId,
  writable,
}: {
  briefId: string;
  writable: boolean;
}) {
  const [jobs, setJobs] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const load = useCallback(async () => {
    try {
      setJobs(
        (
          await api<Page>(
            `/jobs?kind=research_cycle&resource_id=${encodeURIComponent(briefId)}`,
          )
        ).items,
      );
    } catch (err) {
      setError((err as Error).message);
    }
  }, [briefId]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    return () => clearInterval(timer);
  }, [load]);
  return (
    <section>
      <h3>研究循环与恢复审核</h3>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!jobs.length && <p>暂无研究循环</p>}
      {jobs.map((job) => (
        <details key={job.id}>
          <summary>
            <span>{job.id}</span>
            <span>{display(job.status)}</span>
          </summary>
          <Raw data={job} />
          {writable && ["paused", "failed"].includes(String(job.status)) && (
            <button onClick={() => setSelected(job)}>
              <Play size={16} />
              审核并恢复
            </button>
          )}
        </details>
      ))}
      {selected && (
        <Form
          title="审核中断实验"
          fields={[
            {
              name: "decision",
              label: "恢复决定",
              options: ["accept_result", "skip", "retry"],
            },
            { name: "notebook_id", label: "已有实验结果 ID" },
            {
              name: "reason",
              label: "审核理由",
              type: "textarea",
              required: true,
            },
          ]}
          onClose={() => setSelected(null)}
          onSubmit={async (data) => {
            await api(`/research/cycles/${selected.id}/review-resume`, "POST", {
              ...data,
              notebook_id: data.notebook_id || null,
            });
            await load();
          }}
        />
      )}
    </section>
  );
}

function TrainingStatus() {
  const [state, setState] = useState<Row>({});
  useEffect(() => {
    const load = () => {
      if (document.visibilityState !== "visible") return;
      void api("/training/controller")
        .then(setState)
        .catch(() => setState({ last_error: "控制器状态不可用" }));
    };
    const initial = window.setTimeout(load, 4000);
    const timer = setInterval(load, 10000);
    return () => {
      window.clearTimeout(initial);
      clearInterval(timer);
    };
  }, []);
  return (
    <div className="secondary-actions">
      <span>训练调度：{state.running ? "运行中" : "未运行"}</span>
      <span>执行后端：{display(state.backend || "-")}</span>
      <span>并发上限：{String(state.concurrency || "-")}</span>
      {Boolean(state.last_error) && (
        <span className="error">{display(state.last_error)}</span>
      )}
    </div>
  );
}

function OnlineRLPanel({ workspaceId }: { workspaceId: string }) {
  const [sessions, setSessions] = useState<Row[]>([]);
  const [referenceIds, setReferenceIds] = useState("");
  const [modelName, setModelName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      setSessions((await api<Page>("/training/online-rl")).items);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 700);
    return () => window.clearTimeout(timer);
  }, [load]);
  async function create() {
    setBusy(true);
    setError("");
    try {
      await api("/training/online-rl", "POST", {
        workspace_id: workspaceId,
        model_name: modelName || null,
        reference_item_ids: referenceIds.split(/\s+/).filter(Boolean),
        max_rollouts: 10,
        max_tokens_per_rollout: 1024,
        max_cost: 2,
        temperature: 0.7,
      });
      setReferenceIds("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function rollout(session: Row) {
    setBusy(true);
    setError("");
    try {
      await api(`/training/online-rl/${session.id}/rollouts`, "POST", {
        prompt: prompt || undefined,
      });
      setPrompt("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function exportSession(session: Row) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<Row>(
        `/training/online-rl/${session.id}/export`,
        "POST",
        {},
      );
      const blob = new Blob([String(result.jsonl || "")], {
        type: "application/jsonl;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${String(session.id)}.jsonl`;
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice(
        `已导出 ${String(result.row_count)} 条训练样本，SHA-256：${String(result.sha256)}`,
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function stop(session: Row) {
    if (!window.confirm("确认停止此在线强化学习会话吗？")) return;
    setBusy(true);
    setError("");
    try {
      await api(`/training/online-rl/${session.id}/stop`, "POST", {});
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="detail online-rl-panel">
      <header>
        <h2>在线强化学习</h2>
        <span className="status">受控 GRPO Rollout</span>
      </header>
      <div className="form-grid">
        <label>
          已审核 RL 样本 ID
          <textarea
            aria-label="已审核 RL 样本 ID"
            rows={2}
            value={referenceIds}
            onChange={(event) => setReferenceIds(event.target.value)}
            placeholder="每行一个样本 ID"
          />
        </label>
        <label>
          模型名称（可选）
          <input
            aria-label="在线 RL 模型名称"
            value={modelName}
            onChange={(event) => setModelName(event.target.value)}
          />
        </label>
        <label>
          本轮提示（可选）
          <textarea
            aria-label="在线 RL 本轮提示"
            rows={2}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>
      </div>
      <div className="secondary-actions">
        <button
          className="primary"
          disabled={busy || !referenceIds.trim()}
          onClick={() => void create()}
        >
          <Plus size={16} />
          创建会话
        </button>
        {error && <span className="error">{display(error)}</span>}
        {notice && <span className="success">{notice}</span>}
      </div>
      {!sessions.length && <p>暂无在线 RL 会话</p>}
      {sessions.map((session) => (
        <details key={session.id}>
          <summary>
            <span>{String(session.id)}</span>
            <span>
              {display(session.status)} · Rollout{" "}
              {String(session.rollout_count)} · 奖励{" "}
              {String(session.total_reward)}
            </span>
          </summary>
          <Raw
            data={{
              model_name: session.model_name,
              total_cost: session.total_cost,
              reference_item_ids: session.reference_item_ids,
              rollouts: session.rollouts,
              exported_dataset: session.exported_dataset,
              training_ready: session.training_ready,
            }}
          />
          <div className="secondary-actions">
            {session.status === "active" && (
              <>
                <button disabled={busy} onClick={() => void rollout(session)}>
                  <Play size={16} />
                  发起 Rollout
                </button>
                <button disabled={busy} onClick={() => void stop(session)}>
                  <Square size={16} />
                  停止会话
                </button>
              </>
            )}
            {Number(session.rollout_count || 0) > 0 && (
              <button
                disabled={busy}
                onClick={() => void exportSession(session)}
              >
                <Download size={16} />
                导出训练数据
              </button>
            )}
          </div>
        </details>
      ))}
    </section>
  );
}

function CanaryPanel({
  admin,
  workspaceId,
}: {
  admin: boolean;
  workspaceId: string;
}) {
  const [items, setItems] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState<{
    title: string;
    fields: Field[];
    submit: (data: Record<string, string>) => Promise<void>;
  } | null>(null);
  const load = useCallback(async () => {
    try {
      setItems((await api<Page>("/model-registry/canaries")).items);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [workspaceId]);
  useEffect(() => {
    const initial = window.setTimeout(() => {
      if (document.visibilityState === "visible") void load();
    }, 700);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 10000);
    return () => {
      window.clearTimeout(initial);
      clearInterval(timer);
    };
  }, [load]);
  return (
    <section className="detail">
      <header>
        <h2>灰度发布</h2>
        {admin && (
          <button
            onClick={() =>
              setForm({
                title: "创建灰度发布",
                fields: [
                  { name: "version_id", label: "候选版本 ID", required: true },
                  {
                    name: "candidate_model_id",
                    label: "候选模型配置 ID",
                    required: true,
                  },
                  {
                    name: "baseline_model_id",
                    label: "基线模型配置 ID",
                    required: true,
                  },
                  {
                    name: "evaluation_id",
                    label: "真实评测 ID",
                    required: true,
                  },
                  {
                    name: "traffic_percent",
                    label: "候选流量百分比",
                    type: "number",
                    value: "10",
                  },
                  {
                    name: "min_samples",
                    label: "每组最少样本数",
                    type: "number",
                    value: "20",
                  },
                  {
                    name: "max_failure_rate",
                    label: "最大失败率（0 到 1）",
                    value: "0.2",
                  },
                  {
                    name: "max_cost_ratio",
                    label: "最大成本倍率",
                    value: "1.5",
                  },
                ],
                submit: async (data) => {
                  await api("/model-registry/canaries", "POST", {
                    ...data,
                    traffic_percent: Number(data.traffic_percent),
                    min_samples: Number(data.min_samples),
                    max_failure_rate: Number(data.max_failure_rate),
                    max_cost_ratio: Number(data.max_cost_ratio),
                  });
                  await load();
                },
              })
            }
          >
            <Plus size={16} />
            创建灰度
          </button>
        )}
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!items.length && <p>暂无灰度记录</p>}
      {items.map((item) => (
        <details key={item.id}>
          <summary>
            <span>{item.id}</span>
            <span>
              {display(item.status)} · {String(item.traffic_percent)}%
            </span>
          </summary>
          <Raw data={item.metrics} />
          {item.rollback_reason !== undefined && (
            <p>{String(item.rollback_reason)}</p>
          )}
          {admin && ["canary", "promoted"].includes(String(item.status)) && (
            <div className="secondary-actions">
              {(item.status === "canary"
                ? ["promote", "rollback"]
                : ["rollback"]
              ).map((action) => (
                <button
                  key={action}
                  onClick={() =>
                    setForm({
                      title: action === "promote" ? "全量发布" : "回滚灰度",
                      fields: [
                        {
                          name: "confirm",
                          label: "确认执行",
                          type: "checkbox",
                        },
                      ],
                      submit: async (data) => {
                        if (data.confirm !== "on")
                          throw new Error("请确认操作");
                        await api(
                          `/model-registry/canaries/${item.id}/${action}`,
                          "POST",
                          {},
                        );
                        await load();
                      },
                    })
                  }
                >
                  {action === "promote" ? "全量发布" : "回滚"}
                </button>
              ))}
            </div>
          )}
        </details>
      ))}
      {form && (
        <Form
          title={form.title}
          fields={form.fields}
          onSubmit={form.submit}
          onClose={() => setForm(null)}
        />
      )}
    </section>
  );
}


