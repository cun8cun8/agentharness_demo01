const API_BASE = globalThis.RESEARCHFORGE_API_BASE
  || (globalThis.location && !["localhost", "127.0.0.1"].includes(globalThis.location.hostname)
    ? `${globalThis.location.origin}/api/v1`
    : "http://127.0.0.1:8001/api/v1");
let API_KEY = globalThis.RESEARCHFORGE_API_KEY
  || globalThis.localStorage?.getItem("researchforge_api_key")
  || "";
const SAVED_USER_ID = globalThis.localStorage?.getItem("researchforge_user_id") || "";
const MAX_API_CONCURRENCY = 6;
let activeApiRequests = 0;
const pendingApiRequests = [];

const state = {
  tasks: [],
  taskTotal: 0,
  taskFilters: {
    query: "",
    status: "",
    type: "",
    workspace_id: ""
  },
  runs: [],
  selectedTaskId: null,
  selectedTaskDetail: null,
  selectedRun: null,
  selectedFilePath: "",
  selectedFileContent: "",
  selectedFileTruncated: false,
  steps: [],
  toolCalls: [],
  artifacts: [],
  strategies: [],
  policies: [],
  evaluations: [],
  comparison: null,
  releaseGate: null,
  releaseGates: [],
  acceptance: null,
  datasetItems: [],
  preferencePairs: [],
  datasetSnapshots: [],
  datasetQuality: null,
  selectedDatasetItemId: null,
  datasetFormDirty: false,
  auditLogs: [],
  auditFilters: {
    action: "",
    resource_type: "",
    resource_id: "",
    decision: ""
  },
  memoryItems: [],
  memoryRecommendations: [],
  approvals: [],
  storage: null,
  jobs: [],
  jobSummary: null,
  tools: [],
  models: [],
  modelHealth: [],
  extensions: [],
  extensionHealth: {},
  hookDispatches: [],
  researchBriefs: [],
  researchEvidence: [],
  notebookRuns: [],
  researchAcceptance: null,
  telemetry: null,
  sandbox: null,
  sandboxCheck: null,
  modelInvocation: null,
  readiness: null,
  repositoryHealth: {},
  session: null,
  githubOAuth: null,
  currentUserId: SAVED_USER_ID,
  workspaces: [],
  workspaceUsage: null,
  users: [],
  selectedWorkspaceId: null,
  selectedUserId: null,
  workspaceFormDirty: false,
  userFormDirty: false,
  repositories: [],
  selectedRepositoryId: null,
  repositoryFormDirty: false,
  fileTree: [],
  fileTreeTotal: 0,
  fileTreeTruncated: false,
  lastRefreshError: "",
  modelFormInitialized: false,
  selectedModelId: null,
  strategyFormInitialized: false,
  selectedStrategyId: null,
  policyFormInitialized: false,
  selectedPolicyId: null,
  memoryFormInitialized: false,
  selectedMemoryId: null,
  memoryFormDirty: false,
  policyPreview: null,
  online: false,
  pollTimer: null,
  eventSource: null,
  streamRetries: 0,
  taskFormId: null,
  taskFormDirty: false,
  taskFilterTimer: null,
  jobPollTimers: new Map()
};

const elements = {
  apiDot: document.querySelector("#apiDot"),
  apiStatus: document.querySelector("#apiStatus"),
  githubLoginButton: document.querySelector("#githubLoginButton"),
  logoutButton: document.querySelector("#logoutButton"),
  seedButton: document.querySelector("#seedButton"),
  runButton: document.querySelector("#runButton"),
  pauseButton: document.querySelector("#pauseButton"),
  resumeButton: document.querySelector("#resumeButton"),
  cancelButton: document.querySelector("#cancelButton"),
  benchmarkButton: document.querySelector("#benchmarkButton"),
  goldenAcceptanceButton: document.querySelector("#goldenAcceptanceButton"),
  compareButton: document.querySelector("#compareButton"),
  gateButton: document.querySelector("#gateButton"),
  labelButton: document.querySelector("#labelButton"),
  newAnnotationButton: document.querySelector("#newAnnotationButton"),
  preferenceButton: document.querySelector("#preferenceButton"),
  snapshotButton: document.querySelector("#snapshotButton"),
  researchButton: document.querySelector("#researchButton"),
  researchAcceptanceButton: document.querySelector("#researchAcceptanceButton"),
  notebookButton: document.querySelector("#notebookButton"),
  importEvidenceButton: document.querySelector("#importEvidenceButton"),
  attachEvidenceButton: document.querySelector("#attachEvidenceButton"),
  modelInvokeButton: document.querySelector("#modelInvokeButton"),
  saveModelButton: document.querySelector("#saveModelButton"),
  resetModelButton: document.querySelector("#resetModelButton"),
  sandboxCheckButton: document.querySelector("#sandboxCheckButton"),
  exportLink: document.querySelector("#exportLink"),
  preferenceExportLink: document.querySelector("#preferenceExportLink"),
  failureExportLink: document.querySelector("#failureExportLink"),
  trainingExportLink: document.querySelector("#trainingExportLink"),
  runExportLink: document.querySelector("#runExportLink"),
  taskForm: document.querySelector("#taskForm"),
  saveTaskButton: document.querySelector("#saveTaskButton"),
  titleInput: document.querySelector("#titleInput"),
  taskWorkspaceSelect: document.querySelector("#taskWorkspaceSelect"),
  repoInput: document.querySelector("#repoInput"),
  testInput: document.querySelector("#testInput"),
  goalInput: document.querySelector("#goalInput"),
  executionConfigInput: document.querySelector("#executionConfigInput"),
  maxStepsInput: document.querySelector("#maxStepsInput"),
  testTimeoutInput: document.querySelector("#testTimeoutInput"),
  maxToolCallsInput: document.querySelector("#maxToolCallsInput"),
  maxCostInput: document.querySelector("#maxCostInput"),
  qualitySelect: document.querySelector("#qualitySelect"),
  useCaseSelect: document.querySelector("#useCaseSelect"),
  traceTypeSelect: document.querySelector("#traceTypeSelect"),
  failureTypeInput: document.querySelector("#failureTypeInput"),
  traceErrorStepSelect: document.querySelector("#traceErrorStepSelect"),
  usableForSftInput: document.querySelector("#usableForSftInput"),
  usableForPreferenceInput: document.querySelector("#usableForPreferenceInput"),
  rootCauseInput: document.querySelector("#rootCauseInput"),
  preferredActionInput: document.querySelector("#preferredActionInput"),
  qualityFilterSelect: document.querySelector("#qualityFilterSelect"),
  traceFilterSelect: document.querySelector("#traceFilterSelect"),
  useCaseFilterSelect: document.querySelector("#useCaseFilterSelect"),
  statusFilterSelect: document.querySelector("#statusFilterSelect"),
  labelNotesInput: document.querySelector("#labelNotesInput"),
  chosenRunSelect: document.querySelector("#chosenRunSelect"),
  rejectedRunSelect: document.querySelector("#rejectedRunSelect"),
  preferenceRationaleInput: document.querySelector("#preferenceRationaleInput"),
  researchQuestionInput: document.querySelector("#researchQuestionInput"),
  researchDomainInput: document.querySelector("#researchDomainInput"),
  evidenceTitleInput: document.querySelector("#evidenceTitleInput"),
  evidenceSourceTypeSelect: document.querySelector("#evidenceSourceTypeSelect"),
  evidenceFilePathInput: document.querySelector("#evidenceFilePathInput"),
  evidenceContentInput: document.querySelector("#evidenceContentInput"),
  researchEvidenceList: document.querySelector("#researchEvidenceList"),
  researchBriefSelect: document.querySelector("#researchBriefSelect"),
  modelPromptInput: document.querySelector("#modelPromptInput"),
  modelIdInput: document.querySelector("#modelIdInput"),
  modelNameInput: document.querySelector("#modelNameInput"),
  modelProviderSelect: document.querySelector("#modelProviderSelect"),
  modelRoleSelect: document.querySelector("#modelRoleSelect"),
  modelContextWindowInput: document.querySelector("#modelContextWindowInput"),
  modelCostInput: document.querySelector("#modelCostInput"),
  modelStatusSelect: document.querySelector("#modelStatusSelect"),
  modelSourceSelect: document.querySelector("#modelSourceSelect"),
  modelBaseUrlInput: document.querySelector("#modelBaseUrlInput"),
  modelApiKeyEnvInput: document.querySelector("#modelApiKeyEnvInput"),
  modelStrategyIdsInput: document.querySelector("#modelStrategyIdsInput"),
  modelMaxRetriesInput: document.querySelector("#modelMaxRetriesInput"),
  strategyIdInput: document.querySelector("#strategyIdInput"),
  strategyNameInput: document.querySelector("#strategyNameInput"),
  strategyTaskTypeSelect: document.querySelector("#strategyTaskTypeSelect"),
  strategyStatusSelect: document.querySelector("#strategyStatusSelect"),
  strategyMaxStepsInput: document.querySelector("#strategyMaxStepsInput"),
  strategyMemoryEnabledInput: document.querySelector("#strategyMemoryEnabledInput"),
  strategyDescriptionInput: document.querySelector("#strategyDescriptionInput"),
  strategyPlannerPromptInput: document.querySelector("#strategyPlannerPromptInput"),
  strategyRepairPromptInput: document.querySelector("#strategyRepairPromptInput"),
  strategyCriticPromptInput: document.querySelector("#strategyCriticPromptInput"),
  strategyToolSelectionPolicyInput: document.querySelector("#strategyToolSelectionPolicyInput"),
  strategyRuntimeConfigInput: document.querySelector("#strategyRuntimeConfigInput"),
  saveStrategyButton: document.querySelector("#saveStrategyButton"),
  resetStrategyButton: document.querySelector("#resetStrategyButton"),
  policyIdInput: document.querySelector("#policyIdInput"),
  policyNameInput: document.querySelector("#policyNameInput"),
  policyStatusSelect: document.querySelector("#policyStatusSelect"),
  policyMaxStepsInput: document.querySelector("#policyMaxStepsInput"),
  policyMaxRuntimeSecondsInput: document.querySelector("#policyMaxRuntimeSecondsInput"),
  policyMaxPatchFilesInput: document.querySelector("#policyMaxPatchFilesInput"),
  policyMaxChangedLinesInput: document.querySelector("#policyMaxChangedLinesInput"),
  policyNetworkEnabledInput: document.querySelector("#policyNetworkEnabledInput"),
  policyAllowedToolsInput: document.querySelector("#policyAllowedToolsInput"),
  policyAllowedCommandsInput: document.querySelector("#policyAllowedCommandsInput"),
  policyBlockedCommandsInput: document.querySelector("#policyBlockedCommandsInput"),
  policyRequiresApprovalToolsInput: document.querySelector("#policyRequiresApprovalToolsInput"),
  policyProtectedReadPatternsInput: document.querySelector("#policyProtectedReadPatternsInput"),
  policyProtectedWritePatternsInput: document.querySelector("#policyProtectedWritePatternsInput"),
  savePolicyButton: document.querySelector("#savePolicyButton"),
  resetPolicyButton: document.querySelector("#resetPolicyButton"),
  policyPreviewToolSelect: document.querySelector("#policyPreviewToolSelect"),
  policyPreviewPolicySelect: document.querySelector("#policyPreviewPolicySelect"),
  policyPreviewRepoInput: document.querySelector("#policyPreviewRepoInput"),
  policyPreviewInput: document.querySelector("#policyPreviewInput"),
  policyPreviewButton: document.querySelector("#policyPreviewButton"),
  policyPreviewBlock: document.querySelector("#policyPreviewBlock"),
  strategySelect: document.querySelector("#strategySelect"),
  policySelect: document.querySelector("#policySelect"),
  successRate: document.querySelector("#successRate"),
  runCount: document.querySelector("#runCount"),
  avgCost: document.querySelector("#avgCost"),
  toolCallCount: document.querySelector("#toolCallCount"),
  taskCount: document.querySelector("#taskCount"),
  taskList: document.querySelector("#taskList"),
  taskDetail: document.querySelector("#taskDetail"),
  taskSearchInput: document.querySelector("#taskSearchInput"),
  taskStatusFilter: document.querySelector("#taskStatusFilter"),
  taskTypeFilter: document.querySelector("#taskTypeFilter"),
  taskWorkspaceFilter: document.querySelector("#taskWorkspaceFilter"),
  clearTaskFiltersButton: document.querySelector("#clearTaskFiltersButton"),
  runId: document.querySelector("#runId"),
  runHistorySelect: document.querySelector("#runHistorySelect"),
  runStatus: document.querySelector("#runStatus"),
  timeline: document.querySelector("#timeline"),
  toolCallList: document.querySelector("#toolCallList"),
  fileTreeList: document.querySelector("#fileTreeList"),
  diffBlock: document.querySelector("#diffBlock"),
  fileContentBlock: document.querySelector("#fileContentBlock"),
  baselineLogBlock: document.querySelector("#baselineLogBlock"),
  validationLogBlock: document.querySelector("#validationLogBlock"),
  criticReviewBlock: document.querySelector("#criticReviewBlock"),
  modelAssistBlock: document.querySelector("#modelAssistBlock"),
  reportBlock: document.querySelector("#reportBlock"),
  baselineTestMetric: document.querySelector("#baselineTestMetric"),
  testMetric: document.querySelector("#testMetric"),
  testDeltaMetric: document.querySelector("#testDeltaMetric"),
  traceMetric: document.querySelector("#traceMetric"),
  benchmarkRows: document.querySelector("#benchmarkRows"),
  gateStatus: document.querySelector("#gateStatus"),
  acceptanceStatus: document.querySelector("#acceptanceStatus"),
  evaluationHistory: document.querySelector("#evaluationHistory"),
  gateHistory: document.querySelector("#gateHistory"),
  datasetCount: document.querySelector("#datasetCount"),
  datasetList: document.querySelector("#datasetList"),
  snapshotList: document.querySelector("#snapshotList"),
  datasetQualityBlock: document.querySelector("#datasetQualityBlock"),
  auditLogCount: document.querySelector("#auditLogCount"),
  auditLogList: document.querySelector("#auditLogList"),
  auditActionFilterInput: document.querySelector("#auditActionFilterInput"),
  auditResourceTypeFilterInput: document.querySelector("#auditResourceTypeFilterInput"),
  auditResourceIdFilterInput: document.querySelector("#auditResourceIdFilterInput"),
  auditDecisionFilterSelect: document.querySelector("#auditDecisionFilterSelect"),
  auditFilterButton: document.querySelector("#auditFilterButton"),
  auditClearFilterButton: document.querySelector("#auditClearFilterButton"),
  memoryCount: document.querySelector("#memoryCount"),
  memoryIdInput: document.querySelector("#memoryIdInput"),
  memoryTaskIdInput: document.querySelector("#memoryTaskIdInput"),
  memoryRunIdInput: document.querySelector("#memoryRunIdInput"),
  memoryScopeSelect: document.querySelector("#memoryScopeSelect"),
  memoryTypeSelect: document.querySelector("#memoryTypeSelect"),
  memoryKeyInput: document.querySelector("#memoryKeyInput"),
  memoryStatusSelect: document.querySelector("#memoryStatusSelect"),
  memorySummaryInput: document.querySelector("#memorySummaryInput"),
  memoryDetailInput: document.querySelector("#memoryDetailInput"),
  saveMemoryButton: document.querySelector("#saveMemoryButton"),
  resetMemoryButton: document.querySelector("#resetMemoryButton"),
  memoryList: document.querySelector("#memoryList"),
  approvalCount: document.querySelector("#approvalCount"),
  approvalList: document.querySelector("#approvalList"),
  researchCount: document.querySelector("#researchCount"),
  researchAcceptanceStatus: document.querySelector("#researchAcceptanceStatus"),
  researchAcceptanceSummary: document.querySelector("#researchAcceptanceSummary"),
  researchList: document.querySelector("#researchList"),
  platformStatus: document.querySelector("#platformStatus"),
  modelList: document.querySelector("#modelList"),
  extensionList: document.querySelector("#extensionList"),
  toolList: document.querySelector("#toolList"),
  strategyRuntimeList: document.querySelector("#strategyRuntimeList"),
  policyList: document.querySelector("#policyList"),
  jobList: document.querySelector("#jobList"),
  workspaceList: document.querySelector("#workspaceList"),
  workspaceUsageBlock: document.querySelector("#workspaceUsageBlock"),
  userList: document.querySelector("#userList"),
  workspaceNameInput: document.querySelector("#workspaceNameInput"),
  workspaceOwnerInput: document.querySelector("#workspaceOwnerInput"),
  workspaceStatusSelect: document.querySelector("#workspaceStatusSelect"),
  workspaceMaxTasksInput: document.querySelector("#workspaceMaxTasksInput"),
  workspaceMaxActiveRunsInput: document.querySelector("#workspaceMaxActiveRunsInput"),
  workspaceMaxDailyCostInput: document.querySelector("#workspaceMaxDailyCostInput"),
  saveWorkspaceButton: document.querySelector("#saveWorkspaceButton"),
  resetWorkspaceButton: document.querySelector("#resetWorkspaceButton"),
  userWorkspaceSelect: document.querySelector("#userWorkspaceSelect"),
  userEmailInput: document.querySelector("#userEmailInput"),
  userNameInput: document.querySelector("#userNameInput"),
  userRoleSelect: document.querySelector("#userRoleSelect"),
  userStatusSelect: document.querySelector("#userStatusSelect"),
  saveUserButton: document.querySelector("#saveUserButton"),
  resetUserButton: document.querySelector("#resetUserButton"),
  repositoryList: document.querySelector("#repositoryList"),
  repositoryNameInput: document.querySelector("#repositoryNameInput"),
  repositoryProviderSelect: document.querySelector("#repositoryProviderSelect"),
  repositoryUrlInput: document.querySelector("#repositoryUrlInput"),
  repositoryLocalPathInput: document.querySelector("#repositoryLocalPathInput"),
  repositoryDefaultBranchInput: document.querySelector("#repositoryDefaultBranchInput"),
  repositoryCredentialInput: document.querySelector("#repositoryCredentialInput"),
  createRepositoryButton: document.querySelector("#createRepositoryButton"),
  resetRepositoryButton: document.querySelector("#resetRepositoryButton"),
  telemetryBlock: document.querySelector("#telemetryBlock"),
  readinessList: document.querySelector("#readinessList"),
  readinessSummary: document.querySelector("#readinessSummary"),
  modelInvokeBlock: document.querySelector("#modelInvokeBlock"),
  sandboxCheckBlock: document.querySelector("#sandboxCheckBlock"),
  sessionUserSelect: document.querySelector("#sessionUserSelect")
};

function selectedTask() {
  return state.tasks.find((task) => task.id === state.selectedTaskId) || state.tasks[0];
}

function hasPermission(permission) {
  if (!state.online) return true;
  const user = state.session?.user;
  return user?.role === "admin" || (state.session?.permissions || []).includes(permission);
}

function updatePermissionUi() {
  const bindings = [
    [elements.seedButton, "tasks:write"],
    [elements.runButton, "runs:write"],
    [elements.pauseButton, "runs:write"],
    [elements.resumeButton, "runs:write"],
    [elements.cancelButton, "runs:write"],
    [elements.saveTaskButton, "tasks:write"],
    [elements.benchmarkButton, "admin"],
    [elements.goldenAcceptanceButton, "admin"],
    [elements.compareButton, "admin"],
    [elements.gateButton, "admin"],
    [elements.labelButton, "datasets:review"],
    [elements.newAnnotationButton, "datasets:review"],
    [elements.preferenceButton, "datasets:review"],
    [elements.snapshotButton, "datasets:review"],
    [elements.saveMemoryButton, "datasets:review"],
    [elements.researchButton, "research:write"],
    [elements.researchAcceptanceButton, "research:write"],
    [elements.notebookButton, "research:write"],
    [elements.importEvidenceButton, "research:write"],
    [elements.attachEvidenceButton, "research:write"],
    [elements.modelInvokeButton, "admin"],
    [elements.saveModelButton, "admin"],
    [elements.saveStrategyButton, "admin"],
    [elements.savePolicyButton, "admin"],
    [elements.saveWorkspaceButton, "admin"],
    [elements.saveUserButton, "admin"],
    [elements.createRepositoryButton, "integrations:write"]
  ];
  bindings.forEach(([element, permission]) => {
    if (!element) return;
    const disabled = !hasPermission(permission);
    element.disabled = disabled;
    element.title = disabled ? "当前成员没有执行此操作的权限。" : "";
  });
  const taskSubmit = elements.taskForm?.querySelector('button[type="submit"]');
  if (taskSubmit) {
    const disabled = !hasPermission("tasks:write");
    taskSubmit.disabled = disabled;
    taskSubmit.title = disabled ? "当前成员没有创建任务的权限。" : "";
  }
}

function parseExecutionConfig(value) {
  const text = String(value || "").trim();
  if (!text) return {};
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("执行配置必须是合法的 JSON。");
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("执行配置必须是 JSON 对象。");
  }
  return parsed;
}

function syncTaskForm(task) {
  if (!task || !elements.titleInput) return;
  if (state.taskFormDirty && state.taskFormId === task.id) return;
  state.taskFormId = task.id;
  state.taskFormDirty = false;
  elements.titleInput.value = task.title || "";
  if (elements.taskWorkspaceSelect) {
    elements.taskWorkspaceSelect.value = task.workspace_id || state.session?.workspace?.id || "workspace_default";
  }
  elements.repoInput.value = task.repo_path || "";
  elements.testInput.value = task.test_command || "python -m pytest -q";
  elements.goalInput.value = task.goal || "";
  elements.executionConfigInput.value = JSON.stringify(task.execution_config || {}, null, 2);
  elements.maxStepsInput.value = task.budget?.max_steps || 20;
  elements.testTimeoutInput.value = task.test_timeout_seconds || 120;
  elements.maxToolCallsInput.value = task.budget?.max_tool_calls || 40;
  elements.maxCostInput.value = task.budget?.max_model_cost || 2;
}

function setOnline(online) {
  state.online = online;
  elements.apiDot.className = `dot ${online ? "success" : "muted"}`;
  const storageLabel = formatStorageSummary(state.storage);
  elements.apiStatus.textContent = online
    ? `接口在线 / ${storageLabel}`
    : state.lastRefreshError
      ? `接口离线 / ${state.lastRefreshError}`
      : "正在连接";
}

function drainApiQueue() {
  while (activeApiRequests < MAX_API_CONCURRENCY && pendingApiRequests.length) {
    const request = pendingApiRequests.shift();
    activeApiRequests += 1;
    Promise.resolve()
      .then(request.run)
      .then(request.resolve, request.reject)
      .finally(() => {
        activeApiRequests -= 1;
        drainApiQueue();
      });
  }
}

function enqueueApiRequest(run) {
  return new Promise((resolve, reject) => {
    pendingApiRequests.push({ run, resolve, reject });
    drainApiQueue();
  });
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(path, options = {}) {
  return enqueueApiRequest(async () => {
    let response;
    let lastError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        response = await fetch(`${API_BASE}${path}`, {
          cache: "no-store",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            ...(document.cookie.match(/(?:^|; )researchforge_csrf=([^;]+)/)?.[1] ? { "X-CSRF-Token": decodeURIComponent(document.cookie.match(/(?:^|; )researchforge_csrf=([^;]+)/)[1]) } : {}),
            ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
            ...(state.developmentAuth && state.currentUserId ? { "X-User-ID": state.currentUserId } : {}),
            ...(options.headers || {})
          },
          ...options
        });
        break;
      } catch (error) {
        lastError = error;
        if (options.method && !["GET", "HEAD"].includes(options.method.toUpperCase())) break;
        if (attempt < 2) await wait(150 * (attempt + 1));
      }
    }
    if (!response) {
      throw new Error(`${path}: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
    }
    if (!response.ok) {
      const text = await response.text();
      let body = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        body = null;
      }
      const code = body?.error?.code || `HTTP_${response.status}`;
      const message = body?.error?.message || body?.detail || text || `HTTP ${response.status}`;
      throw new Error(`${code}: ${Array.isArray(message) ? "请求参数校验失败" : message}`);
    }
    return response.json();
  });
}

async function refreshAll() {
  try {
    const authConfig = await api("/auth/config");
    state.developmentAuth = authConfig.development_auth;
    const taskFilters = currentTaskFilters();
    const taskQuery = new URLSearchParams({ limit: "200" });
    if (taskFilters.query) taskQuery.set("query", taskFilters.query);
    if (taskFilters.status) taskQuery.set("status", taskFilters.status);
    if (taskFilters.type) taskQuery.set("type", taskFilters.type);
    if (taskFilters.workspace_id) taskQuery.set("workspace_id", taskFilters.workspace_id);
    state.taskFilters = taskFilters;
    const auditQuery = new URLSearchParams({ limit: "50" });
    const auditFilters = currentAuditFilters();
    if (auditFilters.action) auditQuery.set("action", auditFilters.action);
    if (auditFilters.resource_type) auditQuery.set("resource_type", auditFilters.resource_type);
    if (auditFilters.resource_id) auditQuery.set("resource_id", auditFilters.resource_id);
    if (auditFilters.decision) auditQuery.set("decision", auditFilters.decision);
    state.auditFilters = auditFilters;
    const [
      tasks,
      stats,
      runs,
      strategies,
      policies,
      evaluations,
      releaseGates,
      datasetItems,
      preferencePairs,
      datasetSnapshots,
      datasetQuality,
      auditLogs,
      memoryItems,
      memoryRecommendations,
      approvals,
      storage,
      jobs,
      jobSummary,
      tools,
      models,
      modelHealth,
      extensions,
      hookDispatches,
      researchBriefs,
      researchEvidence,
      notebookRuns,
      telemetry,
      sandbox,
      session,
      workspaces,
      users,
      repositories,
      readiness
    ] = await Promise.all([
      api(`/tasks?${taskQuery.toString()}`),
      api(taskFilters.workspace_id
        ? `/tasks/stats?workspace_id=${encodeURIComponent(taskFilters.workspace_id)}`
        : "/tasks/stats"),
      api(taskFilters.workspace_id
        ? `/runs?workspace_id=${encodeURIComponent(taskFilters.workspace_id)}`
        : "/runs"),
      api("/strategies"),
      api("/policies"),
      api("/evaluations/runs"),
      api("/evaluations/release-gates"),
      api("/datasets/trace-items"),
      api("/datasets/preference-pairs"),
      api("/datasets/snapshots"),
      api("/datasets/quality-report"),
      api(`/audit-logs?${auditQuery.toString()}`),
      api("/memory/items"),
      api("/memory/recommendations?limit=6"),
      api("/approvals"),
      api("/system/storage"),
      api(taskFilters.workspace_id
        ? `/jobs?workspace_id=${encodeURIComponent(taskFilters.workspace_id)}`
        : "/jobs"),
      api("/jobs/summary"),
      api("/tools"),
      api("/models"),
      api("/models/health"),
      api("/extensions"),
      api("/extensions/hooks/dispatches"),
      api("/research/briefs"),
      api("/research/evidence"),
      api("/research/notebook-runs"),
      api("/telemetry/summary"),
      api("/sandbox/status"),
      api("/auth/session"),
      api("/workspaces"),
      api("/users"),
      api("/integrations/repositories"),
      api("/system/readiness")
    ]);
    state.tasks = Array.isArray(tasks.items) ? tasks.items : [];
    state.taskTotal = Number.isFinite(tasks.total) ? tasks.total : state.tasks.length;
    state.runs = runs.items || [];
    state.strategies = strategies.items || [];
    state.policies = policies.items || [];
    state.evaluations = evaluations.items || [];
    state.releaseGates = releaseGates.items || [];
    state.datasetItems = datasetItems.items || [];
    state.preferencePairs = preferencePairs.items || [];
    state.datasetSnapshots = datasetSnapshots.items || [];
    state.datasetQuality = datasetQuality.report || null;
    state.auditLogs = auditLogs.items || [];
    state.memoryItems = memoryItems.items || [];
    state.memoryRecommendations = memoryRecommendations.items || [];
    state.approvals = approvals.items || [];
    state.storage = storage;
    state.jobs = jobs.items || [];
    state.jobSummary = jobSummary;
    state.tools = tools.items || [];
    state.models = models.items || [];
    state.modelHealth = modelHealth.items || [];
    state.extensions = extensions.items || [];
    state.extensionHealth = extensions.health || {};
    state.hookDispatches = hookDispatches.items || [];
    state.researchBriefs = researchBriefs.items || [];
    state.researchEvidence = researchEvidence.items || [];
    state.notebookRuns = notebookRuns.items || [];
    state.telemetry = telemetry;
    state.sandbox = sandbox;
    state.session = session;
    syncAuthControls();
    state.workspaces = workspaces.items || [];
    const usageWorkspaceId = state.selectedWorkspaceId || session.workspace?.id || state.workspaces[0]?.id;
    if (usageWorkspaceId) {
      try {
        state.workspaceUsage = await api(`/workspaces/${encodeURIComponent(usageWorkspaceId)}/usage`);
      } catch {
        state.workspaceUsage = null;
      }
    } else {
      state.workspaceUsage = null;
    }
    state.users = users.items || [];
    if (!state.users.some((user) => user.id === state.currentUserId)) {
      state.currentUserId = session.user?.id || "user_admin";
      globalThis.localStorage?.setItem("researchforge_user_id", state.currentUserId);
    }
    state.repositories = repositories.items || [];
    state.readiness = readiness;
    if (state.repositories.length) {
      const healthEntries = await Promise.all(
        state.repositories.map(async (repository) => {
          try {
            return [repository.id, await api(`/integrations/repositories/${repository.id}/health`)];
          } catch {
            return [repository.id, null];
          }
        })
      );
      state.repositoryHealth = Object.fromEntries(healthEntries.filter(([, health]) => health));
    }
    if (!state.tasks.find((task) => task.id === state.selectedTaskId)) {
      state.selectedTaskId = state.tasks[0]?.id;
    }
    applyStats(stats);
    state.lastRefreshError = "";
    setOnline(true);
    try {
      await loadSelectedRun();
    } catch (error) {
      state.lastRefreshError = `详情加载失败：${error instanceof Error ? error.message : String(error)}`;
      state.selectedTaskDetail = selectedTask();
      state.selectedRun = null;
      state.steps = [];
      state.artifacts = [];
      state.toolCalls = [];
      state.fileTree = [];
      state.fileTreeTotal = 0;
      state.fileTreeTruncated = false;
    }
  } catch (error) {
    state.lastRefreshError = `接口加载失败：${error instanceof Error ? error.message : String(error)}`;
    setOnline(false);
    state.tasks = [];
    state.taskTotal = 0;
    state.selectedTaskDetail = null;
    state.selectedFilePath = "";
    state.selectedFileContent = "";
    state.steps = [];
    state.artifacts = [];
    state.toolCalls = [];
    state.fileTree = [];
    state.releaseGates = [];
    state.jobSummary = null;
    state.acceptance = null;
    state.strategies = [];
    state.policies = [];
    state.preferencePairs = [];
    state.datasetSnapshots = [];
    state.datasetQuality = null;
    state.auditLogs = [];
    state.memoryItems = [];
    state.memoryRecommendations = [];
    state.approvals = [];
    state.storage = null;
    state.jobs = [];
    state.tools = [];
    state.models = [];
    state.modelHealth = [];
    state.extensions = [];
    state.extensionHealth = {};
    state.hookDispatches = [];
    state.researchBriefs = [];
    state.researchEvidence = [];
    state.notebookRuns = [];
    state.researchAcceptance = null;
    state.telemetry = null;
    state.sandbox = null;
    state.sandboxCheck = null;
    state.modelInvocation = null;
    state.selectedStrategyId = null;
    state.strategyFormInitialized = false;
    state.selectedPolicyId = null;
    state.policyFormInitialized = false;
    state.selectedMemoryId = null;
    state.memoryFormInitialized = false;
    state.memoryFormDirty = false;
    state.policyPreview = null;
    state.repositoryHealth = {};
    state.session = null;
    state.workspaces = [];
    state.users = [];
    state.selectedWorkspaceId = null;
    state.selectedUserId = null;
    state.workspaceFormDirty = false;
    state.userFormDirty = false;
    state.repositories = [];
    state.repositoryHealth = {};
  }
  render();
}

function applyStats(stats) {
  elements.successRate.textContent = `${Math.round((stats.success_rate || 0) * 100)}%`;
  elements.runCount.textContent = String(stats.run_count || 0);
  elements.avgCost.textContent = `$${Number(stats.avg_cost || 0).toFixed(4)}`;
}

async function loadSelectedRun() {
  const task = selectedTask();
  state.selectedFilePath = "";
  state.selectedFileContent = "";
  state.selectedFileTruncated = false;
  let context = null;
  if (task?.id && state.online) {
    try {
      context = await api(`/tasks/${encodeURIComponent(task.id)}/context`);
      state.selectedTaskDetail = context.task;
      state.runs = mergeRecords(state.runs, context.runs || []);
      state.jobs = mergeRecords(state.jobs, context.jobs || []);
      state.memoryItems = context.memory_items || [];
      state.memoryRecommendations = context.memory_recommendations || [];
      state.datasetItems = mergeRecords(state.datasetItems, context.trace_items || []);
      state.preferencePairs = mergeRecords(state.preferencePairs, context.preference_pairs || []);
      syncTaskForm(state.selectedTaskDetail);
    } catch {
      state.selectedTaskDetail = task;
      syncTaskForm(task);
    }
  } else {
    state.selectedTaskDetail = task || null;
    syncTaskForm(state.selectedTaskDetail);
  }
  const runId = context?.latest_run?.id || state.selectedTaskDetail?.latest_run_id || task?.latest_run_id;
  if (!runId || !state.online) {
    state.selectedRun = null;
    state.steps = [];
    state.artifacts = [];
    state.toolCalls = [];
    state.fileTree = [];
    state.fileTreeTotal = 0;
    state.fileTreeTruncated = false;
    state.selectedFilePath = "";
    state.selectedFileContent = "";
    return;
  }
  await loadRun(runId);
}

function mergeRecords(existing, incoming) {
  const byId = new Map();
  [...(existing || []), ...(incoming || [])].forEach((item) => {
    if (item?.id) byId.set(item.id, item);
  });
  return Array.from(byId.values()).sort(
    (left, right) => new Date(right.created_at || 0) - new Date(left.created_at || 0)
  );
}

async function loadRun(runId) {
  const [run, steps, artifacts, toolCalls] = await Promise.all([
    api(`/runs/${runId}`),
    api(`/runs/${runId}/steps`),
    api(`/runs/${runId}/artifacts`),
    api(`/runs/${runId}/tool-calls`)
  ]);
  state.selectedRun = run;
  state.steps = steps.items || [];
  state.artifacts = artifacts.items || [];
  state.toolCalls = toolCalls.items || [];
  const [fileTree, memoryItems, memoryRecommendations, taskJobs] = await Promise.all([
    api(`/tasks/${run.task_id}/files`),
    api(`/memory/items?task_id=${encodeURIComponent(run.task_id)}`),
    api(`/memory/recommendations?task_id=${encodeURIComponent(run.task_id)}&limit=6`),
    api(`/jobs?task_id=${encodeURIComponent(run.task_id)}`)
  ]);
  state.fileTree = fileTree.items || [];
  state.fileTreeTotal = Number(fileTree.total ?? state.fileTree.length);
  state.fileTreeTruncated = Boolean(fileTree.truncated);
  state.memoryItems = memoryItems.items || [];
  state.memoryRecommendations = memoryRecommendations.items || [];
  state.jobs = mergeRecords(state.jobs, taskJobs.items || []);
  if (["completed", "failed", "blocked", "cancelled", "paused"].includes(run.status)) {
    closeTraceStream();
  }
}

async function createTask(event) {
  event.preventDefault();
  try {
    const executionConfig = parseExecutionConfig(elements.executionConfigInput.value);
    const task = await api("/tasks", {
      method: "POST",
      body: JSON.stringify({
        type: "coding",
        title: elements.titleInput.value.trim() || "未命名代码任务",
        workspace_id: elements.taskWorkspaceSelect?.value || state.session?.workspace?.id || "workspace_default",
        repo_path: elements.repoInput.value.trim(),
        test_command: elements.testInput.value.trim() || "python -m pytest -q",
        test_timeout_seconds: parsePositiveInt(elements.testTimeoutInput.value, 120),
        goal: elements.goalInput.value.trim() || "修复失败的测试。",
        execution_config: executionConfig,
        budget: {
          max_steps: parsePositiveInt(elements.maxStepsInput.value, 20),
          max_runtime_seconds: 600,
          max_tokens: 80000,
          max_model_cost: parsePositiveFloat(elements.maxCostInput.value, 2.0),
          max_tool_calls: parsePositiveInt(elements.maxToolCallsInput.value, 40)
        }
      })
    });
    state.selectedTaskId = task.id;
    state.taskFormDirty = false;
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function saveCurrentTask() {
  const task = state.selectedTaskDetail || selectedTask();
  if (!task?.id || task.id.startsWith("task_demo_")) {
    showError(new Error("当前任务不可保存，请先连接后端或创建真实任务。"));
    return;
  }
  setButtonBusy(elements.saveTaskButton, true, "保存中");
  try {
    const updated = await api(`/tasks/${encodeURIComponent(task.id)}`, {
      method: "PATCH",
      body: JSON.stringify({
        type: "coding",
        title: elements.titleInput.value.trim() || "未命名代码任务",
        workspace_id: elements.taskWorkspaceSelect?.value || task.workspace_id || "workspace_default",
        repo_path: elements.repoInput.value.trim() || null,
        test_command: elements.testInput.value.trim() || "python -m pytest -q",
        test_timeout_seconds: parsePositiveInt(elements.testTimeoutInput.value, 120),
        goal: elements.goalInput.value.trim() || "修复失败的测试。",
        execution_config: parseExecutionConfig(elements.executionConfigInput.value),
        budget: {
          max_steps: parsePositiveInt(elements.maxStepsInput.value, 20),
          max_runtime_seconds: 600,
          max_tokens: 80000,
          max_model_cost: parsePositiveFloat(elements.maxCostInput.value, 2.0),
          max_tool_calls: parsePositiveInt(elements.maxToolCallsInput.value, 40)
        }
      })
    });
    state.selectedTaskDetail = updated;
    state.tasks = state.tasks.map((item) =>
      item.id === updated.id
        ? {
            ...item,
            title: updated.title,
            status: updated.status,
            latest_run_id: updated.latest_run_id
          }
        : item
    );
    state.taskFormDirty = false;
    render();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.saveTaskButton, false, "保存当前任务");
  }
}

async function routeModelName(taskType, estimatedTokens = 8000) {
  try {
    const response = await api("/models/route", {
      method: "POST",
      body: JSON.stringify({
        task_type: taskType || "coding",
        strategy_id: elements.strategySelect.value || null,
        estimated_tokens: estimatedTokens
      })
    });
    if (response.model_name) return response.model_name;
  } catch {
    // fall through to local selection
  }
  const role = taskType || "coding";
  const fallback = state.models.find((model) => model.role === role && model.status === "active")
    || state.models.find((model) => model.status === "active")
    || state.models[0];
  return fallback?.model_name || "model_unavailable";
}

async function runSelectedTask() {
  const task = selectedTask();
  if (!task) return;
  setButtonBusy(elements.runButton, true, "已排队");
  try {
    const modelName = await routeModelName(task.type || "coding", 8000);
    const run = await api(`/tasks/${task.id}/runs`, {
      method: "POST",
      body: JSON.stringify({
        agent_strategy_id: elements.strategySelect.value || "repair_baseline_v1",
        policy_version_id: elements.policySelect.value || "policy_default_v1",
        model_name: modelName
      })
    });
    state.selectedRun = run;
    state.tasks = state.tasks.map((item) =>
      item.id === task.id ? { ...item, latest_run_id: run.id, status: run.status } : item
    );
    render();
    startPolling(run.id);
    startTraceStream(run.id);
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.runButton, false, "运行选中任务");
  }
}

function startPolling(runId) {
  if (state.pollTimer) clearInterval(state.pollTimer);
  let attempts = 0;
  state.pollTimer = setInterval(async () => {
    attempts += 1;
    try {
      await loadRun(runId);
      const status = state.selectedRun?.status;
      if (["completed", "failed", "blocked", "cancelled", "paused"].includes(status) || attempts > 40) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        await refreshAll();
      } else {
        render();
      }
    } catch {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }, 750);
}

function watchJob(jobId, onComplete = null) {
  if (!jobId || !state.online || state.jobPollTimers.has(jobId)) return;
  let attempts = 0;
  const timer = setInterval(async () => {
    attempts += 1;
    try {
      const job = await api(`/jobs/${encodeURIComponent(jobId)}`);
      state.jobs = state.jobs.map((item) => item.id === job.id ? job : item);
      renderPlatform();
      if (["completed", "failed", "cancelled", "paused"].includes(job.status) || attempts > 120) {
        clearInterval(timer);
        state.jobPollTimers.delete(jobId);
        if (job.status === "completed" && typeof onComplete === "function") {
          onComplete(job);
        }
        await refreshAll();
      }
    } catch {
      clearInterval(timer);
      state.jobPollTimers.delete(jobId);
    }
  }, 1000);
  state.jobPollTimers.set(jobId, timer);
}

function closeTraceStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.streamRetries = 0;
}

function startTraceStream(runId, retryCount = 0) {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  if (!state.online || typeof EventSource === "undefined") return;
  state.streamRetries = retryCount;
  const source = new EventSource(`${API_BASE}/runs/${runId}/events`, { withCredentials: true });
  state.eventSource = source;
  const refreshTrace = async (event) => {
    try {
      await loadRun(runId);
      render();
      if (["run.completed", "run.failed", "run.cancelled", "run.paused"].includes(event.type)) {
        closeTraceStream();
      }
    } catch (error) {
      console.error(error);
    }
  };
  [
    "run.created",
    "run.phase_changed",
    "run.cancel_requested",
    "run.pause_requested",
    "run.paused",
    "step.started",
    "step.completed",
    "tool_call.completed",
    "artifact.created",
    "run.completed",
    "run.failed",
    "run.cancelled"
  ].forEach((eventType) => source.addEventListener(eventType, refreshTrace));
  source.onerror = () => {
    source.close();
    if (state.eventSource === source) state.eventSource = null;
    const status = state.selectedRun?.status;
    if (!["completed", "failed", "blocked", "cancelled", "paused"].includes(status) && retryCount < 5) {
      window.setTimeout(() => {
        if (state.selectedRun?.id === runId) startTraceStream(runId, retryCount + 1);
      }, Math.min(1000 * (retryCount + 1), 5000));
    }
  };
}

async function cancelSelectedRun() {
  const runId = state.selectedRun?.id || selectedTask()?.latest_run_id;
  if (!runId) return;
  setButtonBusy(elements.cancelButton, true, "取消中");
  try {
    const run = await api(`/runs/${runId}/cancel`, {
      method: "POST",
      body: "{}"
    });
    state.selectedRun = run;
    await loadSelectedRun();
    render();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.cancelButton, false, "取消运行");
    render();
  }
}

async function pauseSelectedRun() {
  const runId = state.selectedRun?.id || selectedTask()?.latest_run_id;
  if (!runId) return;
  setButtonBusy(elements.pauseButton, true, "暂停中");
  try {
    state.selectedRun = await api(`/runs/${encodeURIComponent(runId)}/pause`, {
      method: "POST",
      body: "{}"
    });
    await loadSelectedRun();
    render();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.pauseButton, false, "暂停运行");
    render();
  }
}

async function resumePausedRun() {
  const runId = state.selectedRun?.id || selectedTask()?.latest_run_id;
  if (!runId) return;
  try {
    const resumed = await api(`/runs/${encodeURIComponent(runId)}/resume`, {
      method: "POST"
    });
    state.selectedRun = resumed;
    await refreshAll();
    startPolling(resumed.id);
    startTraceStream(resumed.id);
  } catch (error) {
    showError(error);
  }
}

async function cancelJob(jobId) {
  if (!jobId) return;
  try {
    const job = await api(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason: "operator_request" })
    });
    if (job.resource_id) {
      const run = await api(`/runs/${encodeURIComponent(job.resource_id)}`).catch(() => null);
      if (run) state.selectedRun = run;
    }
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function retryJob(jobId) {
  if (!jobId) return;
  try {
    const payload = await api(`/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
      body: "{}"
    });
    if (payload.run?.id) {
      state.selectedRun = payload.run;
      state.selectedTaskId = payload.run.task_id || state.selectedTaskId;
    }
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function resumeJob(jobId) {
  if (!jobId) return;
  try {
    const payload = await api(`/jobs/${encodeURIComponent(jobId)}/resume`, {
      method: "POST"
    });
    if (payload.run?.id) {
      state.selectedRun = payload.run;
      state.selectedTaskId = payload.run.task_id || state.selectedTaskId;
      startPolling(payload.run.id);
      startTraceStream(payload.run.id);
    }
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function seedGoldenTasks() {
  setButtonBusy(elements.seedButton, true, "导入中");
  try {
    const payload = await api("/benchmarks/golden-tasks/seed", { method: "POST", body: "{}" });
    if (payload.items?.length) state.selectedTaskId = payload.items[0].id;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.seedButton, false, "导入黄金任务");
  }
}

async function runBenchmark() {
  setButtonBusy(elements.benchmarkButton, true, "运行中");
  try {
    const modelName = await routeModelName("coding", 12000);
    let taskIds = state.tasks
      .filter((task) => !task.id.startsWith("task_demo_"))
      .slice(0, 10)
      .map((task) => task.id);
    if (!taskIds.length) {
      const seeded = await api("/benchmarks/golden-tasks/seed", {
        method: "POST",
        body: "{}"
      });
      taskIds = (seeded.items || []).map((task) => task.id);
    }
    const result = await api("/evaluations/runs", {
      method: "POST",
      body: JSON.stringify({
        benchmark_name: "coding_golden_v1",
        task_ids: taskIds,
        agent_strategy_id: elements.strategySelect.value || "repair_baseline_v1",
        policy_version_id: elements.policySelect.value || "policy_default_v1",
        model_name: modelName
      })
    });
    if (result.status === "queued") {
      watchJob(result.job_id, () => {});
    }
    state.comparison = null;
    state.releaseGate = null;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.benchmarkButton, false, "运行评测");
  }
}

async function runGoldenAcceptance() {
  setButtonBusy(elements.goldenAcceptanceButton, true, "验收中");
  try {
    const modelName = await routeModelName("coding", 12000);
    const result = await api("/benchmarks/golden-tasks/acceptance", {
      method: "POST",
      body: JSON.stringify({
        agent_strategy_id: elements.strategySelect.value || "repair_with_critic_v3",
        policy_version_id: elements.policySelect.value || "policy_default_v1",
        model_name: modelName,
        limit: 10
      })
    });
    if (result.status === "queued") {
      state.acceptance = {
        status: "queued",
        job_id: result.job_id || result.acceptance?.job_id,
        task_count: 0,
        passed_count: 0,
        report_path: result.acceptance?.report_path || ""
      };
      render();
      watchJob(state.acceptance.job_id, (job) => {
        state.acceptance = {
          ...state.acceptance,
          ...(job.result_json || {}),
          status: job.status,
          job_id: job.id
        };
      });
      return;
    }
    if (result.status !== "completed") {
      throw new Error(result.error || "Golden Task 批量验收失败。");
    }
    state.comparison = null;
    state.releaseGate = null;
    state.acceptance = result.acceptance;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.goldenAcceptanceButton, false, "批量验收 10 题");
  }
}

async function compareStrategies() {
  setButtonBusy(elements.compareButton, true, "比较中");
  try {
    const modelName = await routeModelName("coding", 12000);
    state.comparison = await api("/evaluations/compare", {
      method: "POST",
      body: JSON.stringify({
        benchmark_name: "coding_golden_v1",
        task_ids: [],
        agent_strategy_ids: [
          "repair_baseline_v1",
          "repair_with_trace_v2",
          "repair_with_critic_v3"
        ],
        baseline_strategy_id: "repair_baseline_v1",
        policy_version_id: elements.policySelect.value || "policy_default_v1",
        model_name: modelName
      })
    });
    if (state.comparison.status === "queued") {
      watchJob(state.comparison.job_id, (job) => {
        state.comparison = job.result_json?.comparison || {
          status: job.status,
          job_id: job.id
        };
      });
    }
    state.releaseGate = null;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.compareButton, false, "比较策略");
  }
}

async function runReleaseGate() {
  const evaluationId =
    state.comparison?.winner?.evaluation_run_id ||
    state.evaluations[0]?.id;
  if (!evaluationId) {
    showError(new Error("请先运行一次评测，再检查发布门禁。"));
    return;
  }
  setButtonBusy(elements.gateButton, true, "检查中");
  try {
    state.releaseGate = await api("/evaluations/release-gate", {
      method: "POST",
      body: JSON.stringify({ evaluation_run_id: evaluationId })
    });
    render();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.gateButton, false, "发布门禁");
  }
}

async function labelSelectedRun() {
  const run = state.selectedRun;
  if (!run) return;
  setButtonBusy(elements.labelButton, true, "标注中");
  try {
    const payload = readAnnotationForm(run);
    const selectedItem = state.selectedDatasetItemId
      ? state.datasetItems.find((item) => item.id === state.selectedDatasetItemId)
      : null;
    const targetId = selectedItem?.agent_run_id === run.id ? selectedItem.id : "";
    const saved = await api(targetId ? `/datasets/trace-items/${encodeURIComponent(targetId)}` : "/datasets/trace-items", {
      method: targetId ? "PATCH" : "POST",
      body: JSON.stringify(targetId ? withoutKey(payload, "agent_run_id") : payload)
    });
    state.selectedDatasetItemId = saved.id;
    state.datasetFormDirty = false;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.labelButton, false, "保存运行标注");
  }
}

function readAnnotationForm(run) {
  const useCase = elements.useCaseSelect.value || (run.status === "completed" ? "sft_candidate" : "failure_case");
  const traceType = elements.traceTypeSelect?.value || (run.status === "completed" ? "SUCCESS_TRACE" : "FAILURE_TRACE");
  return {
    agent_run_id: run.id,
    quality_label: elements.qualitySelect.value || (run.status === "completed" ? "good" : "average"),
    trace_type: traceType,
    use_case: useCase,
    failure_type: elements.failureTypeInput?.value.trim() || run.error_summary || null,
    root_cause: elements.rootCauseInput?.value.trim() || null,
    agent_error_step_id: elements.traceErrorStepSelect?.value || null,
    human_preferred_action: elements.preferredActionInput?.value.trim() || null,
    usable_for_sft: Boolean(elements.usableForSftInput?.checked),
    usable_for_preference: Boolean(elements.usableForPreferenceInput?.checked),
    notes: elements.labelNotesInput.value.trim() || "来自控制台的人工标注。"
  };
}

function withoutKey(value, key) {
  const copy = { ...(value || {}) };
  delete copy[key];
  return copy;
}

function resetAnnotationForm({ force = false } = {}) {
  const run = state.selectedRun;
  if (!run || state.datasetFormDirty && !force) return;
  const selectedItem = state.selectedDatasetItemId
    ? state.datasetItems.find((item) => item.id === state.selectedDatasetItemId)
    : null;
  if (selectedItem && selectedItem.agent_run_id === run.id) {
    syncAnnotationForm(selectedItem, { force: true });
    return;
  }
  const useCase = run.status === "completed" ? "sft_candidate" : "failure_case";
  const traceType = run.status === "completed" ? "SUCCESS_TRACE" : "FAILURE_TRACE";
  if (elements.qualitySelect) elements.qualitySelect.value = run.status === "completed" ? "good" : "average";
  if (elements.useCaseSelect) elements.useCaseSelect.value = useCase;
  if (elements.traceTypeSelect) elements.traceTypeSelect.value = traceType;
  if (elements.failureTypeInput) elements.failureTypeInput.value = run.error_summary || "";
  if (elements.traceErrorStepSelect) elements.traceErrorStepSelect.value = "";
  if (elements.usableForSftInput) elements.usableForSftInput.checked = run.status === "completed";
  if (elements.usableForPreferenceInput) elements.usableForPreferenceInput.checked = useCase === "preference_candidate";
  if (elements.rootCauseInput) elements.rootCauseInput.value = "";
  if (elements.preferredActionInput) elements.preferredActionInput.value = "";
  if (elements.labelNotesInput) elements.labelNotesInput.value = "来自控制台的人工标注。";
  state.selectedDatasetItemId = null;
  state.datasetFormDirty = false;
}

function syncAnnotationForm(item, { force = false } = {}) {
  if (!item || state.datasetFormDirty && !force) return;
  if (elements.qualitySelect) elements.qualitySelect.value = item.quality_label || "average";
  if (elements.useCaseSelect) elements.useCaseSelect.value = item.use_case || "sft_candidate";
  if (elements.traceTypeSelect) elements.traceTypeSelect.value = item.trace_type || "SUCCESS_TRACE";
  if (elements.failureTypeInput) elements.failureTypeInput.value = item.failure_type || "";
  if (elements.traceErrorStepSelect) elements.traceErrorStepSelect.value = item.agent_error_step_id || "";
  if (elements.usableForSftInput) elements.usableForSftInput.checked = Boolean(item.usable_for_sft);
  if (elements.usableForPreferenceInput) elements.usableForPreferenceInput.checked = Boolean(item.usable_for_preference);
  if (elements.rootCauseInput) elements.rootCauseInput.value = item.root_cause || "";
  if (elements.preferredActionInput) elements.preferredActionInput.value = item.human_preferred_action || "";
  if (elements.labelNotesInput) elements.labelNotesInput.value = item.notes || "";
  state.selectedDatasetItemId = item.id;
  state.datasetFormDirty = false;
}

async function selectDatasetItem(itemId) {
  const item = state.datasetItems.find((candidate) => candidate.id === itemId);
  if (!item) return;
  try {
    const run = state.runs.find((candidate) => candidate.id === item.agent_run_id);
    if (run?.task_id) state.selectedTaskId = run.task_id;
    if (state.online && state.selectedRun?.id !== item.agent_run_id) {
      await loadRun(item.agent_run_id);
    } else {
      state.selectedRun = run || state.selectedRun;
    }
    syncAnnotationForm(item, { force: true });
    render();
    elements.labelNotesInput?.focus();
  } catch (error) {
    showError(error);
  }
}

function newAnnotationForCurrentRun() {
  state.selectedDatasetItemId = null;
  state.datasetFormDirty = false;
  resetAnnotationForm({ force: true });
}

async function reviewTraceItem(itemId, decision) {
  const item = state.datasetItems.find((candidate) => candidate.id === itemId);
  if (!item) return;
  const changes = decision === "approved"
    ? {
        status: "approved",
        notes: item.notes || "人工审核通过，可进入数据集。"
      }
    : decision === "rejected"
      ? {
          status: "rejected",
          notes: item.notes || "人工审核拒绝，暂不进入数据集。"
        }
      : {
          status: "reviewed",
          quality_label: "bad",
          trace_type: "FAILURE_TRACE",
          use_case: "failure_case",
          usable_for_sft: false,
          usable_for_preference: false,
          notes: item.notes || "已转为失败样本，等待进一步分析。"
        };
  try {
    await api(`/datasets/trace-items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: JSON.stringify(changes)
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function createPreferencePair() {
  const chosenRunId = elements.chosenRunSelect.value;
  const rejectedRunId = elements.rejectedRunSelect.value;
  if (!chosenRunId || !rejectedRunId || chosenRunId === rejectedRunId) {
    showError(new Error("请选择两个不同的运行。"));
    return;
  }
  setButtonBusy(elements.preferenceButton, true, "创建中");
  try {
    await api("/datasets/preference-pairs", {
      method: "POST",
      body: JSON.stringify({
        chosen_run_id: chosenRunId,
        rejected_run_id: rejectedRunId,
        preference_label: "chosen_better",
        rationale: elements.preferenceRationaleInput.value.trim() || "优选运行具备更完整的修复证据。"
      })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.preferenceButton, false, "创建偏好对");
  }
}

async function createDatasetSnapshot() {
  setButtonBusy(elements.snapshotButton, true, "生成中");
  try {
    const filters = currentDatasetFilters();
    await api("/datasets/snapshots", {
      method: "POST",
      body: JSON.stringify({
        version_name: `trace_dataset_${new Date().toISOString().slice(0, 10)}`,
        quality_label: filters.quality_label || null,
        trace_type: filters.trace_type || null,
        use_case: filters.use_case || null,
        item_status: filters.status || null,
        include_preferences: true,
        notes: "由中文控制台批量生成的数据集版本。"
      })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.snapshotButton, false, "生成数据集版本");
  }
}

async function decideApproval(approvalId, decision) {
  if (!approvalId || !decision) return;
  try {
    await api(`/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        decided_by: "console",
        reason: decision === "approved" ? "控制台审批通过" : "控制台审批拒绝"
      })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function resumeApprovedRun(runId) {
  if (!runId) return;
  try {
    await api(`/runs/${encodeURIComponent(runId)}/resume`, { method: "POST" });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function invokeModel() {
  setButtonBusy(elements.modelInvokeButton, true, "调用中");
  try {
    state.modelInvocation = await api("/models/invoke", {
      method: "POST",
      body: JSON.stringify({
        task_type: "coding",
        prompt: elements.modelPromptInput.value.trim() || "请用中文总结当前任务。",
        max_tokens: 256,
        temperature: 0.2
      })
    });
    renderPlatform();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.modelInvokeButton, false, "试调模型");
  }
}

async function saveModelConfig() {
  setButtonBusy(elements.saveModelButton, true, "保存中");
  try {
    const payload = readModelConfigForm();
    const saved = await api("/models", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    state.modelFormInitialized = true;
    syncModelFormToSelection(saved);
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.saveModelButton, false, "保存模型配置");
  }
}

function resetModelConfigForm() {
  syncModelFormToSelection(findDefaultModelConfig());
}

function syncModelFormToSelection(model = null, options = {}) {
  const selected = model || findDefaultModelConfig();
  if (!elements.modelIdInput) return;
  elements.modelIdInput.value = selected.id || "model_coding_custom_v1";
  elements.modelNameInput.value = selected.model_name || "mock-coding-agent";
  elements.modelProviderSelect.value = selected.provider || "mock";
  elements.modelRoleSelect.value = selected.role || "coding";
  elements.modelContextWindowInput.value = String(selected.context_window ?? 128000);
  elements.modelCostInput.value = String(selected.cost_per_1k_tokens ?? 0.002);
  elements.modelStatusSelect.value = selected.status || "active";
  elements.modelSourceSelect.value = String(selected.config?.source || (selected.provider === "mock" ? "mock" : "environment"));
  elements.modelBaseUrlInput.value = String(selected.config?.base_url || "");
  elements.modelApiKeyEnvInput.value = String(selected.config?.api_key_env || "");
  elements.modelStrategyIdsInput.value = Array.isArray(selected.config?.strategy_ids)
    ? selected.config.strategy_ids.join(", ")
    : "";
  elements.modelMaxRetriesInput.value = String(selected.config?.max_retries ?? "");
  state.modelFormInitialized = true;
  state.selectedModelId = selected.id || null;
}

function readModelConfigForm() {
  const id = String(elements.modelIdInput?.value || "").trim();
  const model_name = String(elements.modelNameInput?.value || "").trim();
  if (!id) {
    throw new Error("配置 ID 不能为空。");
  }
  if (!model_name) {
    throw new Error("模型名称不能为空。");
  }
  const strategyIds = String(elements.modelStrategyIdsInput?.value || "")
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean);
  const config = {};
  const source = String(elements.modelSourceSelect?.value || "").trim();
  if (source) config.source = source;
  if (String(elements.modelBaseUrlInput?.value || "").trim()) {
    config.base_url = String(elements.modelBaseUrlInput.value || "").trim();
  }
  if (String(elements.modelApiKeyEnvInput?.value || "").trim()) {
    config.api_key_env = String(elements.modelApiKeyEnvInput.value || "").trim();
  }
  if (strategyIds.length) {
    config.strategy_ids = strategyIds;
  }
  const maxRetries = Number.parseInt(String(elements.modelMaxRetriesInput?.value || ""), 10);
  if (Number.isFinite(maxRetries) && maxRetries >= 0) {
    config.max_retries = maxRetries;
  }
  return {
    id,
    provider: String(elements.modelProviderSelect?.value || "mock"),
    model_name,
    role: String(elements.modelRoleSelect?.value || "coding"),
    context_window: parsePositiveInt(String(elements.modelContextWindowInput?.value || ""), 128000),
    cost_per_1k_tokens: parsePositiveFloat(String(elements.modelCostInput?.value || ""), 0.002),
    config,
    status: String(elements.modelStatusSelect?.value || "active")
  };
}

function findDefaultModelConfig() {
  return (
    state.models.find((item) => item.status === "active" && item.role === "coding")
    || state.models.find((item) => item.status === "active")
    || state.models[0]
    || {
      id: "model_coding_custom_v1",
      provider: "mock",
      model_name: "mock-coding-agent",
      role: "coding",
      context_window: 128000,
      cost_per_1k_tokens: 0.002,
      config: { source: "mock" },
      status: "active"
    }
  );
}

async function saveStrategyConfig() {
  setButtonBusy(elements.saveStrategyButton, true, "保存中");
  try {
    const payload = readStrategyConfigForm();
    const saved = await api("/strategies", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    state.strategyFormInitialized = true;
    syncStrategyFormToSelection(saved);
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.saveStrategyButton, false, "保存策略配置");
  }
}

function resetStrategyConfigForm() {
  syncStrategyFormToSelection(findDefaultStrategyConfig());
}

function syncStrategyFormToSelection(strategy = null) {
  const selected = strategy || findDefaultStrategyConfig();
  if (!elements.strategyIdInput) return;
  elements.strategyIdInput.value = selected.id || "repair_custom_v1";
  elements.strategyNameInput.value = selected.name || "自定义策略 V1";
  elements.strategyTaskTypeSelect.value = selected.task_type || "coding";
  elements.strategyStatusSelect.value = selected.status || "draft";
  elements.strategyMaxStepsInput.value = String(selected.max_steps ?? 20);
  elements.strategyMemoryEnabledInput.checked = Boolean(selected.memory_enabled);
  elements.strategyDescriptionInput.value = selected.description || "";
  elements.strategyPlannerPromptInput.value = selected.planner_prompt || "";
  elements.strategyRepairPromptInput.value = selected.repair_prompt || "";
  elements.strategyCriticPromptInput.value = selected.critic_prompt || "";
  elements.strategyToolSelectionPolicyInput.value = JSON.stringify(selected.tool_selection_policy || {}, null, 2);
  elements.strategyRuntimeConfigInput.value = JSON.stringify(selected.runtime_config || {}, null, 2);
  state.strategyFormInitialized = true;
  state.selectedStrategyId = selected.id || null;
}

function readStrategyConfigForm() {
  const id = String(elements.strategyIdInput?.value || "").trim();
  const name = String(elements.strategyNameInput?.value || "").trim();
  if (!id) {
    throw new Error("策略 ID 不能为空。");
  }
  if (!name) {
    throw new Error("策略名称不能为空。");
  }
  const parseJsonObject = (value, errorMessage) => {
    const text = String(value || "").trim();
    if (!text) return {};
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(errorMessage);
    }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error(errorMessage);
    }
    return parsed;
  };
  return {
    id,
    name,
    task_type: String(elements.strategyTaskTypeSelect?.value || "coding"),
    description: String(elements.strategyDescriptionInput?.value || "").trim(),
    planner_prompt: String(elements.strategyPlannerPromptInput?.value || "").trim(),
    repair_prompt: String(elements.strategyRepairPromptInput?.value || "").trim(),
    critic_prompt: String(elements.strategyCriticPromptInput?.value || "").trim(),
    tool_selection_policy: parseJsonObject(
      elements.strategyToolSelectionPolicyInput?.value,
      "工具选择策略必须是合法的 JSON 对象。"
    ),
    runtime_config: parseJsonObject(
      elements.strategyRuntimeConfigInput?.value,
      "运行时配置必须是合法的 JSON 对象。"
    ),
    max_steps: parsePositiveInt(String(elements.strategyMaxStepsInput?.value || ""), 20),
    memory_enabled: Boolean(elements.strategyMemoryEnabledInput?.checked),
    status: String(elements.strategyStatusSelect?.value || "draft")
  };
}

function findDefaultStrategyConfig() {
  return (
    state.strategies.find((item) => item.status === "active" && item.task_type === "coding")
    || state.strategies.find((item) => item.status === "active")
    || state.strategies[0]
    || {
      id: "repair_custom_v1",
      name: "自定义策略 V1",
      task_type: "coding",
      description: "用于配置策略提示词、运行时参数和生命周期。",
      planner_prompt: "创建清晰、可执行的修复计划。",
      repair_prompt: "生成最小、可验证的补丁。",
      critic_prompt: "评审补丁风险、测试证据和回归可能性。",
      tool_selection_policy: {},
      runtime_config: {
        precheck: true,
        retry: true,
        critic: false,
        model_gateway: true,
        max_validation_retries: 1,
        max_patch_files: 3,
        max_changed_lines: 80,
        allow_test_edits: false,
        require_diff: true,
        require_all_tests: true
      },
      max_steps: 20,
      memory_enabled: false,
      status: "draft"
    }
  );
}

async function savePolicyConfig() {
  setButtonBusy(elements.savePolicyButton, true, "保存中");
  try {
    const payload = readPolicyConfigForm();
    const saved = await api("/policies", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    state.policyFormInitialized = true;
    syncPolicyFormToSelection(saved);
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.savePolicyButton, false, "保存安全策略");
  }
}

async function previewToolPolicy() {
  if (!elements.policyPreviewToolSelect || !elements.policyPreviewPolicySelect) return;
  setButtonBusy(elements.policyPreviewButton, true, "预检中");
  try {
    const inputText = String(elements.policyPreviewInput?.value || "").trim();
    let input = {};
    if (inputText) {
      try {
        input = JSON.parse(inputText);
      } catch {
        throw new Error("工具输入必须是合法的 JSON。");
      }
      if (!input || Array.isArray(input) || typeof input !== "object") {
        throw new Error("工具输入必须是 JSON 对象。");
      }
    }
    const result = await api(`/tools/${encodeURIComponent(elements.policyPreviewToolSelect.value)}/policy-preview`, {
      method: "POST",
      body: JSON.stringify({
        repo_path: elements.policyPreviewRepoInput?.value.trim() || null,
        policy_version_id: elements.policyPreviewPolicySelect.value || "policy_default_v1",
        input
      })
    });
    state.policyPreview = result;
    renderPlatform();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.policyPreviewButton, false, "执行策略预检");
  }
}

function resetPolicyConfigForm() {
  syncPolicyFormToSelection(findDefaultPolicyConfig());
}

function syncPolicyFormToSelection(policy = null) {
  const selected = policy || findDefaultPolicyConfig();
  if (!elements.policyIdInput) return;
  elements.policyIdInput.value = selected.id || "policy_custom_v1";
  elements.policyNameInput.value = selected.name || "自定义安全策略 V1";
  elements.policyStatusSelect.value = selected.status || "active";
  elements.policyMaxStepsInput.value = String(selected.max_steps ?? 20);
  elements.policyMaxRuntimeSecondsInput.value = String(selected.max_runtime_seconds ?? 600);
  elements.policyMaxPatchFilesInput.value = String(selected.max_patch_files ?? 3);
  elements.policyMaxChangedLinesInput.value = String(selected.max_changed_lines ?? 80);
  elements.policyNetworkEnabledInput.checked = Boolean(selected.network_enabled);
  elements.policyAllowedToolsInput.value = Array.isArray(selected.allowed_tools) ? selected.allowed_tools.join(", ") : "";
  elements.policyAllowedCommandsInput.value = Array.isArray(selected.allowed_commands) ? selected.allowed_commands.join(", ") : "";
  elements.policyBlockedCommandsInput.value = Array.isArray(selected.blocked_commands) ? selected.blocked_commands.join(", ") : "";
  elements.policyRequiresApprovalToolsInput.value = Array.isArray(selected.requires_approval_tools) ? selected.requires_approval_tools.join(", ") : "";
  elements.policyProtectedReadPatternsInput.value = Array.isArray(selected.protected_read_patterns) ? selected.protected_read_patterns.join(", ") : "";
  elements.policyProtectedWritePatternsInput.value = Array.isArray(selected.protected_write_patterns) ? selected.protected_write_patterns.join(", ") : "";
  state.policyFormInitialized = true;
  state.selectedPolicyId = selected.id || null;
}

function syncPolicyPreviewForm() {
  if (!elements.policyPreviewToolSelect || !elements.policyPreviewPolicySelect) return;
  const selectedTool = elements.policyPreviewToolSelect.value;
  const selectedPolicy = elements.policyPreviewPolicySelect.value;
  const toolOptions = state.tools.length
    ? state.tools
    : [{ name: "file.read", description: "读取文件" }, { name: "shell.run", description: "执行命令" }];
  const policyOptions = state.policies.length
    ? state.policies
    : [{ id: "policy_default_v1", name: "默认安全策略 V1" }];
  elements.policyPreviewToolSelect.innerHTML = toolOptions
    .map((tool) => `<option value="${escapeHtml(tool.name)}">${escapeHtml(formatAction(tool.name))}</option>`)
    .join("");
  elements.policyPreviewPolicySelect.innerHTML = policyOptions
    .map((policy) => `<option value="${escapeHtml(policy.id)}">${escapeHtml(formatPolicyName(policy.name || policy.id))}</option>`)
    .join("");
  if (selectedTool && toolOptions.some((tool) => tool.name === selectedTool)) {
    elements.policyPreviewToolSelect.value = selectedTool;
  } else if (toolOptions[0]) {
    elements.policyPreviewToolSelect.value = toolOptions[0].name;
  }
  if (selectedPolicy && policyOptions.some((policy) => policy.id === selectedPolicy)) {
    elements.policyPreviewPolicySelect.value = selectedPolicy;
  } else if (policyOptions[0]) {
    elements.policyPreviewPolicySelect.value = policyOptions[0].id;
  }
}

function readPolicyConfigForm() {
  const id = String(elements.policyIdInput?.value || "").trim();
  const name = String(elements.policyNameInput?.value || "").trim();
  if (!id) {
    throw new Error("安全策略 ID 不能为空。");
  }
  if (!name) {
    throw new Error("安全策略名称不能为空。");
  }
  const parseList = (value) =>
    String(value || "")
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
  return {
    id,
    name,
    allowed_tools: parseList(elements.policyAllowedToolsInput?.value),
    blocked_commands: parseList(elements.policyBlockedCommandsInput?.value),
    allowed_commands: parseList(elements.policyAllowedCommandsInput?.value),
    requires_approval_tools: parseList(elements.policyRequiresApprovalToolsInput?.value),
    protected_read_patterns: parseList(elements.policyProtectedReadPatternsInput?.value),
    protected_write_patterns: parseList(elements.policyProtectedWritePatternsInput?.value),
    max_steps: parsePositiveInt(String(elements.policyMaxStepsInput?.value || ""), 20),
    max_runtime_seconds: parsePositiveInt(String(elements.policyMaxRuntimeSecondsInput?.value || ""), 600),
    max_patch_files: parsePositiveInt(String(elements.policyMaxPatchFilesInput?.value || ""), 3),
    max_changed_lines: parsePositiveInt(String(elements.policyMaxChangedLinesInput?.value || ""), 80),
    network_enabled: Boolean(elements.policyNetworkEnabledInput?.checked),
    status: String(elements.policyStatusSelect?.value || "active")
  };
}

function findDefaultPolicyConfig() {
  return (
    state.policies.find((item) => item.id === "policy_default_v1")
    || state.policies.find((item) => item.status === "active")
    || state.policies[0]
    || {
      id: "policy_custom_v1",
      name: "自定义安全策略 V1",
      allowed_tools: ["file.read", "file.write_patch", "shell.run", "git.diff", "test.run", "report.write"],
      blocked_commands: [
        "rm -rf",
        "del /s",
        "rmdir /s",
        "format",
        "chmod",
        "chown",
        "Invoke-WebRequest",
        "curl | sh",
        "wget | bash",
        "ssh",
        "scp",
        "sudo",
        "powershell -EncodedCommand",
        "Invoke-Expression"
      ],
      allowed_commands: ["pytest", "python", "python3", "git"],
      requires_approval_tools: [],
      protected_read_patterns: [".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519", "secrets.*"],
      protected_write_patterns: [".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519", "secrets.*", "tests/*", "test_*"],
      max_steps: 20,
      max_runtime_seconds: 600,
      max_patch_files: 3,
      max_changed_lines: 80,
      network_enabled: false,
      status: "active"
    }
  );
}

function currentAuditFilters() {
  return {
    action: String(elements.auditActionFilterInput?.value || "").trim(),
    resource_type: String(elements.auditResourceTypeFilterInput?.value || "").trim(),
    resource_id: String(elements.auditResourceIdFilterInput?.value || "").trim(),
    decision: String(elements.auditDecisionFilterSelect?.value || "").trim()
  };
}

function syncAuditFiltersToForm() {
  if (elements.auditActionFilterInput) elements.auditActionFilterInput.value = state.auditFilters.action || "";
  if (elements.auditResourceTypeFilterInput) elements.auditResourceTypeFilterInput.value = state.auditFilters.resource_type || "";
  if (elements.auditResourceIdFilterInput) elements.auditResourceIdFilterInput.value = state.auditFilters.resource_id || "";
  if (elements.auditDecisionFilterSelect) elements.auditDecisionFilterSelect.value = state.auditFilters.decision || "";
}

function clearAuditFilters() {
  if (elements.auditActionFilterInput) elements.auditActionFilterInput.value = "";
  if (elements.auditResourceTypeFilterInput) elements.auditResourceTypeFilterInput.value = "";
  if (elements.auditResourceIdFilterInput) elements.auditResourceIdFilterInput.value = "";
  if (elements.auditDecisionFilterSelect) elements.auditDecisionFilterSelect.value = "";
  state.auditFilters = {
    action: "",
    resource_type: "",
    resource_id: "",
    decision: ""
  };
  refreshAll();
}

function findDefaultMemoryItem() {
  return {
    id: "",
    task_id: "",
    agent_run_id: "",
    scope: "project",
    memory_type: "failure",
    key: "",
    summary: "",
    detail_json: {},
    status: "active"
  };
}

function syncMemoryFormToSelection(memory = null, options = {}) {
  if (state.memoryFormDirty && !options.force) return;
  const selected = memory || findDefaultMemoryItem();
  if (!elements.memoryIdInput) return;
  elements.memoryIdInput.value = selected.id || "";
  elements.memoryIdInput.readOnly = true;
  elements.memoryTaskIdInput.value = selected.task_id || "";
  elements.memoryRunIdInput.value = selected.agent_run_id || "";
  elements.memoryScopeSelect.value = selected.scope || "project";
  elements.memoryTypeSelect.value = selected.memory_type || "failure";
  elements.memoryKeyInput.value = selected.key || "";
  elements.memoryStatusSelect.value = selected.status || "active";
  elements.memorySummaryInput.value = selected.summary || "";
  elements.memoryDetailInput.value = JSON.stringify(selected.detail_json || {}, null, 2);
  state.memoryFormInitialized = true;
  state.memoryFormDirty = false;
  state.selectedMemoryId = selected.id || null;
}

function resetMemoryForm() {
  state.selectedMemoryId = null;
  state.memoryFormDirty = false;
  syncMemoryFormToSelection(null, { force: true });
}

function readMemoryForm() {
  const key = String(elements.memoryKeyInput?.value || "").trim();
  const summary = String(elements.memorySummaryInput?.value || "").trim();
  if (!key) {
    throw new Error("记忆键不能为空。");
  }
  if (!summary) {
    throw new Error("记忆摘要不能为空。");
  }
  const parseJsonObject = (value, errorMessage) => {
    const text = String(value || "").trim();
    if (!text) return {};
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(errorMessage);
    }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error(errorMessage);
    }
    return parsed;
  };
  return {
    task_id: String(elements.memoryTaskIdInput?.value || "").trim() || null,
    agent_run_id: String(elements.memoryRunIdInput?.value || "").trim() || null,
    scope: String(elements.memoryScopeSelect?.value || "project"),
    memory_type: String(elements.memoryTypeSelect?.value || "failure"),
    key,
    summary,
    detail_json: parseJsonObject(
      elements.memoryDetailInput?.value,
      "记忆详情必须是合法的 JSON 对象。"
    ),
    status: String(elements.memoryStatusSelect?.value || "active")
  };
}

async function saveMemoryItem() {
  if (!elements.saveMemoryButton) return;
  try {
    const payload = readMemoryForm();
    const targetId = String(elements.memoryIdInput?.value || "").trim() || state.selectedMemoryId || "";
    setButtonBusy(elements.saveMemoryButton, true, targetId ? "保存中" : "创建中");
    const saved = await api(targetId ? `/memory/items/${encodeURIComponent(targetId)}` : "/memory/items", {
      method: targetId ? "PATCH" : "POST",
      body: JSON.stringify(payload)
    });
    syncMemoryFormToSelection(saved, { force: true });
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.saveMemoryButton, false, "保存记忆");
  }
}

function selectMemoryItem(itemId) {
  const item = state.memoryItems.find((entry) => entry.id === itemId) || null;
  syncMemoryFormToSelection(item, { force: true });
  elements.memorySummaryInput?.focus();
}

function syncWorkspaceOptions() {
  if (!elements.userWorkspaceSelect) return;
  const selectedValue = elements.userWorkspaceSelect.value || state.selectedWorkspaceId || state.session?.workspace?.id || "";
  const options = state.workspaces
    .map((workspace) => `<option value="${escapeHtml(workspace.id)}">${escapeHtml(workspace.name)} / ${escapeHtml(formatWorkspaceStatus(workspace.status))}</option>`)
    .join("");
  elements.userWorkspaceSelect.innerHTML = options || `<option value="workspace_default">默认工作区</option>`;
  if (selectedValue && state.workspaces.some((workspace) => workspace.id === selectedValue)) {
    elements.userWorkspaceSelect.value = selectedValue;
  }
}

function syncWorkspaceForm(workspace = null, options = {}) {
  if (state.workspaceFormDirty && !options.force) return;
  const selected = workspace || null;
  if (elements.workspaceNameInput) elements.workspaceNameInput.value = selected?.name || "研发工作区";
  if (elements.workspaceOwnerInput) elements.workspaceOwnerInput.value = selected?.owner_id || state.session?.user?.id || "user_admin";
  if (elements.workspaceStatusSelect) elements.workspaceStatusSelect.value = selected?.status || "active";
  if (elements.workspaceMaxTasksInput) elements.workspaceMaxTasksInput.value = selected?.max_tasks ?? 1000;
  if (elements.workspaceMaxActiveRunsInput) elements.workspaceMaxActiveRunsInput.value = selected?.max_active_runs ?? 8;
  if (elements.workspaceMaxDailyCostInput) elements.workspaceMaxDailyCostInput.value = selected?.max_daily_cost ?? 50;
  state.selectedWorkspaceId = selected?.id || null;
  state.workspaceFormDirty = false;
}

function resetWorkspaceForm() {
  state.selectedWorkspaceId = null;
  state.workspaceFormDirty = false;
  syncWorkspaceForm(null, { force: true });
}

function readWorkspaceForm() {
  const name = String(elements.workspaceNameInput?.value || "").trim();
  if (!name) throw new Error("工作区名称不能为空。");
  const maxTasks = Number(elements.workspaceMaxTasksInput?.value || 0);
  const maxActiveRuns = Number(elements.workspaceMaxActiveRunsInput?.value || 0);
  const maxDailyCost = Number(elements.workspaceMaxDailyCostInput?.value || 0);
  if (!Number.isInteger(maxTasks) || maxTasks < 1) throw new Error("任务上限必须是正整数。");
  if (!Number.isInteger(maxActiveRuns) || maxActiveRuns < 1) throw new Error("并发运行上限必须是正整数。");
  if (!Number.isFinite(maxDailyCost) || maxDailyCost < 0) throw new Error("每日成本上限必须是非负数字。");
  return {
    name,
    owner_id: String(elements.workspaceOwnerInput?.value || "").trim() || "user_admin",
    status: String(elements.workspaceStatusSelect?.value || "active"),
    max_tasks: maxTasks,
    max_active_runs: maxActiveRuns,
    max_daily_cost: maxDailyCost
  };
}

async function saveWorkspace() {
  if (!elements.saveWorkspaceButton) return;
  try {
    const payload = readWorkspaceForm();
    const targetId = state.selectedWorkspaceId || "";
    setButtonBusy(elements.saveWorkspaceButton, true, targetId ? "保存中" : "创建中");
    const saved = await api(targetId ? `/workspaces/${encodeURIComponent(targetId)}` : "/workspaces", {
      method: targetId ? "PATCH" : "POST",
      body: JSON.stringify(payload)
    });
    state.selectedWorkspaceId = saved.id;
    state.workspaceFormDirty = false;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.saveWorkspaceButton, false, "保存工作区");
  }
}

function selectWorkspace(workspaceId) {
  const workspace = state.workspaces.find((item) => item.id === workspaceId) || null;
  syncWorkspaceForm(workspace, { force: true });
  if (workspace?.id) {
    loadWorkspaceUsage(workspace.id);
  }
  if (workspace?.id && elements.userWorkspaceSelect) elements.userWorkspaceSelect.value = workspace.id;
  renderPlatform();
  elements.workspaceNameInput?.focus();
}

async function loadWorkspaceUsage(workspaceId) {
  if (!workspaceId) return;
  try {
    state.workspaceUsage = await api(`/workspaces/${encodeURIComponent(workspaceId)}/usage`);
  } catch (error) {
    state.workspaceUsage = null;
    if (state.online) showError(error);
  }
  renderPlatform();
}

async function updateWorkspaceStatus(workspaceId, status) {
  if (!workspaceId || !status) return;
  try {
    await api(`/workspaces/${encodeURIComponent(workspaceId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

function syncUserForm(user = null, options = {}) {
  if (state.userFormDirty && !options.force) return;
  const selected = user || null;
  syncWorkspaceOptions();
  if (elements.userWorkspaceSelect) {
    const fallbackWorkspace = state.selectedWorkspaceId || state.session?.workspace?.id || state.workspaces[0]?.id || "workspace_default";
    elements.userWorkspaceSelect.value = selected?.workspace_id || fallbackWorkspace;
  }
  if (elements.userEmailInput) elements.userEmailInput.value = selected?.email || "operator@researchforge.local";
  if (elements.userNameInput) elements.userNameInput.value = selected?.name || "研发操作员";
  if (elements.userRoleSelect) elements.userRoleSelect.value = selected?.role || "operator";
  if (elements.userStatusSelect) elements.userStatusSelect.value = selected?.status || "active";
  state.selectedUserId = selected?.id || null;
  state.userFormDirty = false;
}

function resetUserForm() {
  state.selectedUserId = null;
  state.userFormDirty = false;
  syncUserForm(null, { force: true });
}

function readUserForm() {
  const email = String(elements.userEmailInput?.value || "").trim();
  const name = String(elements.userNameInput?.value || "").trim();
  if (!email) throw new Error("成员邮箱不能为空。");
  if (!name) throw new Error("成员姓名不能为空。");
  return {
    workspace_id: String(elements.userWorkspaceSelect?.value || state.selectedWorkspaceId || "workspace_default"),
    email,
    name,
    role: String(elements.userRoleSelect?.value || "operator"),
    status: String(elements.userStatusSelect?.value || "active")
  };
}

async function saveUser() {
  if (!elements.saveUserButton) return;
  try {
    const payload = readUserForm();
    const targetId = state.selectedUserId || "";
    setButtonBusy(elements.saveUserButton, true, targetId ? "保存中" : "创建中");
    const saved = await api(targetId ? `/users/${encodeURIComponent(targetId)}` : "/users", {
      method: targetId ? "PATCH" : "POST",
      body: JSON.stringify(payload)
    });
    state.selectedUserId = saved.id;
    state.userFormDirty = false;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.saveUserButton, false, "保存成员");
  }
}

function selectUser(userId) {
  const user = state.users.find((item) => item.id === userId) || null;
  syncUserForm(user, { force: true });
  renderPlatform();
  elements.userNameInput?.focus();
}

async function updateUserStatus(userId, status) {
  if (!userId || !status) return;
  try {
    await api(`/users/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function transitionStrategy(strategyId, action) {
  if (!strategyId || !action) return;
  const endpointMap = {
    candidate: "candidate",
    promote: "promote",
    rollback: "rollback"
  };
  const endpoint = endpointMap[action];
  if (!endpoint) return;
  try {
    await api(`/strategies/${encodeURIComponent(strategyId)}/${endpoint}`, {
      method: "POST",
      body: JSON.stringify({ reason: "控制台操作" })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function checkSandbox() {
  setButtonBusy(elements.sandboxCheckButton, true, "检查中");
  try {
    state.sandboxCheck = await api("/sandbox/check");
    renderPlatform();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.sandboxCheckButton, false, "检查沙箱");
  }
}

async function checkExtensionHealth(extensionId) {
  if (!extensionId) return;
  try {
    await api(`/extensions/${encodeURIComponent(extensionId)}/health`);
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function invokeExtension(extensionId, action, extensionType) {
  if (!extensionId) return;
  const task = selectedTask();
  const input = {};
  if (extensionType === "mcp_tool") {
    input.repo_path = task?.repo_path || elements.repoInput.value.trim() || ".";
  }
  if (extensionType === "skill") {
    input.task_type = task?.type || "coding";
  }
  try {
    await api(`/extensions/${encodeURIComponent(extensionId)}/invoke`, {
      method: "POST",
      body: JSON.stringify({
        action: action || "inspect",
        input
      })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function updateExtensionStatus(extensionId, status) {
  if (!extensionId || !status) return;
  try {
    await api(`/extensions/${encodeURIComponent(extensionId)}/status`, {
      method: "PATCH",
      body: JSON.stringify({
        status,
        reason: status === "enabled" ? "控制台启用扩展。" : "控制台暂停扩展。"
      })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function dispatchTestHook(eventType) {
  try {
    await api("/extensions/hooks/dispatch", {
      method: "POST",
      body: JSON.stringify({
        event_type: eventType || "run.completed",
        payload: {
          source: "console",
          timestamp: new Date().toISOString()
        }
      })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function checkRepository(repositoryId) {
  if (!repositoryId) return;
  try {
    state.repositoryHealth[repositoryId] = await api(`/integrations/repositories/${repositoryId}/health`);
    renderPlatform();
  } catch (error) {
    showError(error);
  }
}

async function syncRepository(repositoryId) {
  if (!repositoryId) return;
  try {
    const result = await api(`/integrations/repositories/${repositoryId}/sync`, {
      method: "POST",
      body: "{}"
    });
    state.repositoryHealth[repositoryId] = result.health;
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function createResearchBrief() {
  setButtonBusy(elements.researchButton, true, "生成中");
  try {
    const brief = await api("/research/briefs", {
      method: "POST",
      body: JSON.stringify({
        question: elements.researchQuestionInput.value.trim() || "如何评估智能体可靠性？",
        domain: elements.researchDomainInput.value.trim() || "general",
        max_papers: 5
      })
    });
    state.researchBriefs = [brief, ...state.researchBriefs.filter((item) => item.id !== brief.id)];
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.researchButton, false, "生成简报");
  }
}

async function createRepositoryConnection() {
  if (!elements.createRepositoryButton) return;
  const targetId = state.selectedRepositoryId || "";
  setButtonBusy(elements.createRepositoryButton, true, targetId ? "保存中" : "创建中");
  try {
    const payload = {
      workspace_id: state.selectedWorkspaceId || state.session?.workspace?.id || "workspace_default",
      name: elements.repositoryNameInput?.value.trim() || "GitHub 仓库",
      provider: elements.repositoryProviderSelect?.value || "local",
      url: elements.repositoryUrlInput?.value.trim() || null,
      local_path: elements.repositoryLocalPathInput?.value.trim() || null,
      default_branch: elements.repositoryDefaultBranchInput?.value.trim() || "main",
      credential_ref: elements.repositoryCredentialInput?.value.trim() || null
    };
    const saved = await api(targetId
      ? `/integrations/repositories/${encodeURIComponent(targetId)}`
      : "/integrations/repositories", {
      method: targetId ? "PATCH" : "POST",
      body: JSON.stringify(targetId ? withoutKey(payload, "workspace_id") : payload)
    });
    state.selectedRepositoryId = saved.id;
    state.repositoryFormDirty = false;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.createRepositoryButton, false, "创建连接");
  }
}

function syncRepositoryForm(repository = null, options = {}) {
  if (state.repositoryFormDirty && !options.force) return;
  if (elements.repositoryNameInput) elements.repositoryNameInput.value = repository?.name || "GitHub 仓库";
  if (elements.repositoryProviderSelect) elements.repositoryProviderSelect.value = repository?.provider || "local";
  if (elements.repositoryUrlInput) elements.repositoryUrlInput.value = repository?.url || "";
  if (elements.repositoryLocalPathInput) elements.repositoryLocalPathInput.value = repository?.local_path || "";
  if (elements.repositoryDefaultBranchInput) elements.repositoryDefaultBranchInput.value = repository?.default_branch || "main";
  if (elements.repositoryCredentialInput) elements.repositoryCredentialInput.value = repository?.credential_ref || "";
  if (elements.createRepositoryButton) elements.createRepositoryButton.textContent = repository ? "保存连接" : "创建连接";
  state.selectedRepositoryId = repository?.id || null;
  state.repositoryFormDirty = false;
}

function resetRepositoryForm() {
  state.selectedRepositoryId = null;
  state.repositoryFormDirty = false;
  syncRepositoryForm(null, { force: true });
}

function selectRepository(repositoryId) {
  const repository = state.repositories.find((item) => item.id === repositoryId) || null;
  syncRepositoryForm(repository, { force: true });
  elements.repositoryNameInput?.focus();
}

async function updateRepositoryStatus(repositoryId, status) {
  if (!repositoryId || !status) return;
  try {
    await api(`/integrations/repositories/${encodeURIComponent(repositoryId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function runResearchBenchmarkAcceptance() {
  setButtonBusy(elements.researchAcceptanceButton, true, "验收中");
  try {
    const result = await api("/research/benchmarks/acceptance", {
      method: "POST",
      body: JSON.stringify({ benchmark_name: "research_brief_v1", max_papers: 5, limit: 10 })
    });
    if (result.status === "queued") {
      state.researchAcceptance = {
        status: "queued",
        job_id: result.acceptance?.job_id || result.job_id,
        task_count: 0,
        passed_count: 0,
        report_path: result.acceptance?.report_path || ""
      };
      render();
      watchJob(state.researchAcceptance.job_id, (job) => {
        state.researchAcceptance = {
          ...state.researchAcceptance,
          ...(job.result_json || {}),
          status: job.status,
          job_id: job.id
        };
      });
      return;
    }
    if (result.status !== "completed") {
      throw new Error("Research Benchmark 批量验收失败。");
    }
    state.researchAcceptance = result.acceptance;
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.researchAcceptanceButton, false, "研究验收 10 题");
  }
}

async function reviewPreferencePair(pairId, status) {
  if (!pairId || !status) return;
  try {
    await api(`/datasets/preference-pairs/${encodeURIComponent(pairId)}`, {
      method: "PATCH",
      body: JSON.stringify({
        status,
        rationale: status === "approved" ? "人工审核通过，可用于偏好数据。" : "人工审核拒绝，暂不用于偏好数据。"
      })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function archiveMemoryItem(itemId) {
  if (!itemId) return;
  try {
    await api(`/memory/items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "archived" })
    });
    await refreshAll();
  } catch (error) {
    showError(error);
  }
}

async function importResearchEvidence() {
  setButtonBusy(elements.importEvidenceButton, true, "导入中");
  try {
    const evidence = await api("/research/evidence", {
      method: "POST",
      body: JSON.stringify({
        title: elements.evidenceTitleInput.value.trim() || null,
        domain: elements.researchDomainInput.value.trim() || "general",
        source_type: elements.evidenceSourceTypeSelect.value || "manual",
        content: elements.evidenceContentInput.value.trim(),
        file_path: elements.evidenceFilePathInput.value.trim() || null
      })
    });
    state.researchEvidence = [evidence, ...state.researchEvidence.filter((item) => item.id !== evidence.id)];
    elements.evidenceTitleInput.value = "";
    elements.evidenceFilePathInput.value = "";
    elements.evidenceContentInput.value = "";
    renderResearch();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.importEvidenceButton, false, "导入证据");
  }
}

async function attachSelectedResearchEvidence() {
  const briefId = elements.researchBriefSelect.value || state.researchBriefs[0]?.id;
  const paperIds = [...document.querySelectorAll("[data-evidence-check]:checked")]
    .map((input) => input.dataset.evidenceCheck)
    .filter(Boolean);
  if (!briefId) {
    showError(new Error("请先生成一个研究简报。"));
    return;
  }
  if (!paperIds.length) {
    showError(new Error("请先选择要挂载的证据。"));
    return;
  }
  setButtonBusy(elements.attachEvidenceButton, true, "挂载中");
  try {
    const brief = await api(`/research/briefs/${encodeURIComponent(briefId)}/evidence`, {
      method: "POST",
      body: JSON.stringify({ paper_ids: paperIds })
    });
    state.researchBriefs = [brief, ...state.researchBriefs.filter((item) => item.id !== brief.id)];
    renderResearch();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.attachEvidenceButton, false, "挂载选中证据");
  }
}

async function reviewResearchCitation(briefId, paperId, status) {
  try {
    const brief = await api(`/research/briefs/${encodeURIComponent(briefId)}/citations/review`, {
      method: "POST",
      body: JSON.stringify({
        paper_id: paperId,
        status,
        reviewer_id: "operator",
        note: status === "approved" ? "前端审核通过。" : "前端审核退回。"
      })
    });
    state.researchBriefs = [brief, ...state.researchBriefs.filter((item) => item.id !== brief.id)];
    renderResearch();
  } catch (error) {
    showError(error);
  }
}

function currentTaskFilters() {
  return {
    query: elements.taskSearchInput?.value.trim() || "",
    status: elements.taskStatusFilter?.value || "",
    type: elements.taskTypeFilter?.value || "",
    workspace_id: elements.taskWorkspaceFilter?.value || ""
  };
}

function scheduleTaskFilterRefresh() {
  if (state.taskFilterTimer) clearTimeout(state.taskFilterTimer);
  state.taskFilterTimer = window.setTimeout(() => {
    state.taskFilterTimer = null;
    refreshAll();
  }, 260);
}

function clearTaskFilters() {
  elements.taskSearchInput.value = "";
  elements.taskStatusFilter.value = "";
  elements.taskTypeFilter.value = "";
  if (elements.taskWorkspaceFilter) elements.taskWorkspaceFilter.value = "";
  refreshAll();
}

async function loadTaskFile(filePath) {
  const task = selectedTask();
  if (!task?.id || !filePath || !state.online) return;
  try {
    const result = await api(`/tasks/${encodeURIComponent(task.id)}/files/${filePath.split("/").map(encodeURIComponent).join("/")}`);
    state.selectedFilePath = result.path || filePath;
    state.selectedFileContent = result.content || "";
    state.selectedFileTruncated = Boolean(result.truncated);
    renderEvidence();
  } catch (error) {
    showError(error);
  }
}

async function runNotebookForLatestBrief() {
  const briefId = state.researchBriefs[0]?.id;
  if (!briefId) {
    showError(new Error("请先生成一个研究简报。"));
    return;
  }
  setButtonBusy(elements.notebookButton, true, "运行中");
  try {
    const result = await api(`/research/briefs/${briefId}/notebook-runs`, {
      method: "POST",
      body: "{}"
    });
    if (result.status === "queued") {
      watchJob(result.job_id, () => {});
    }
    await refreshAll();
  } catch (error) {
    showError(error);
  } finally {
    setButtonBusy(elements.notebookButton, false, "运行笔记本");
  }
}

function render() {
  syncAuthControls();
  document.dispatchEvent(new CustomEvent("rf:updated"));
  updatePermissionUi();
  renderSelectors();
  renderRunSelectors();
  renderAnnotationStepOptions();
  renderTasks();
  renderTaskDetail();
  renderTrace();
  renderEvidence();
  renderBenchmark();
  renderDataset();
  renderAuditLogs();
  renderMemory();
  renderApprovals();
  renderResearch();
  renderPlatform();
}

function renderRunSelectors() {
  const taskId = selectedTask()?.id;
  const taskRuns = state.runs
    .filter((run) => !taskId || run.task_id === taskId)
    .slice(0, 30);
  const options = taskRuns
    .map((run) => `<option value="${escapeHtml(run.id)}">${escapeHtml(run.id)} / ${escapeHtml(formatRunStatus(run.status))}</option>`)
    .join("");
  if (elements.runHistorySelect) {
    elements.runHistorySelect.innerHTML = options || `<option value="">暂无历史运行</option>`;
    if (state.selectedRun?.id && taskRuns.some((run) => run.id === state.selectedRun.id)) {
      elements.runHistorySelect.value = state.selectedRun.id;
    }
  }
  elements.chosenRunSelect.innerHTML = options || `<option value="">暂无运行</option>`;
  elements.rejectedRunSelect.innerHTML = options || `<option value="">暂无运行</option>`;
}

function renderAnnotationStepOptions() {
  if (!elements.traceErrorStepSelect) return;
  const selected = elements.traceErrorStepSelect.value || "";
  const options = state.steps
    .map((step) => {
      const label = `步骤 ${step.step_index} / ${formatPhase(step.phase)} / ${formatRunStatus(step.status)}`;
      return `<option value="${escapeHtml(step.id)}">${escapeHtml(label)}</option>`;
    })
    .join("");
  elements.traceErrorStepSelect.innerHTML = `<option value="">未指定</option>${options}`;
  if (selected && state.steps.some((step) => step.id === selected)) {
    elements.traceErrorStepSelect.value = selected;
  }
  resetAnnotationForm();
}

function renderSelectors() {
  const strategyOptions = state.strategies.length
    ? state.strategies
    : [{ id: "repair_baseline_v1", name: "基础修复策略 V1" }];
  const policyOptions = state.policies.length
    ? state.policies
    : [{ id: "policy_default_v1", name: "默认安全策略 V1" }];
  elements.strategySelect.innerHTML = strategyOptions
    .map((strategy) => `<option value="${escapeHtml(strategy.id)}">${escapeHtml(formatStrategyName(strategy.name || strategy.id))}</option>`)
    .join("");
  elements.policySelect.innerHTML = policyOptions
    .map((policy) => `<option value="${escapeHtml(policy.id)}">${escapeHtml(formatPolicyName(policy.name || policy.id))}</option>`)
    .join("");
  if (elements.taskWorkspaceSelect) {
    const selectedWorkspace = elements.taskWorkspaceSelect.value || state.session?.workspace?.id || "workspace_default";
    elements.taskWorkspaceSelect.innerHTML = state.workspaces
      .map((workspace) => `<option value="${escapeHtml(workspace.id)}">${escapeHtml(workspace.name)} / ${escapeHtml(formatWorkspaceStatus(workspace.status))}</option>`)
      .join("") || `<option value="workspace_default">默认工作区</option>`;
    if (selectedWorkspace && state.workspaces.some((workspace) => workspace.id === selectedWorkspace)) {
      elements.taskWorkspaceSelect.value = selectedWorkspace;
    }
  }
  if (elements.taskWorkspaceFilter) {
    const selectedFilter = state.taskFilters.workspace_id || "";
    const workspaceOptions = state.workspaces
      .map((workspace) => (
        `<option value="${escapeHtml(workspace.id)}">${escapeHtml(workspace.name)} / ${escapeHtml(formatWorkspaceStatus(workspace.status))}</option>`
      ))
      .join("");
    elements.taskWorkspaceFilter.innerHTML = `<option value="">全部工作区</option>${workspaceOptions}`;
    if (selectedFilter && state.workspaces.some((workspace) => workspace.id === selectedFilter)) {
      elements.taskWorkspaceFilter.value = selectedFilter;
    } else {
      elements.taskWorkspaceFilter.value = "";
    }
  }
}

function renderTasks() {
  elements.taskCount.textContent = state.taskTotal !== state.tasks.length
    ? `${state.tasks.length}/${state.taskTotal}`
    : String(state.tasks.length);
  elements.taskList.innerHTML = state.tasks
    .map(
      (task) => `
        <button class="task-row ${task.id === state.selectedTaskId ? "selected" : ""}" type="button" data-task-id="${escapeHtml(task.id)}">
          <span>
            <strong>${escapeHtml(formatTaskTitle(task.title))}</strong>
            <small>${escapeHtml(task.id)} · ${escapeHtml(
              state.workspaces.find((workspace) => workspace.id === task.workspace_id)?.name || task.workspace_id || "默认工作区"
            )} ${task.latest_run_id ? `-> ${escapeHtml(task.latest_run_id)}` : ""}</small>
          </span>
          ${statusBadge(task.status)}
        </button>
      `
    )
    .join("");
  document.querySelectorAll(".task-row").forEach((button) => {
    button.addEventListener("click", async () => {
      state.selectedTaskId = button.dataset.taskId;
      await loadSelectedRun();
      render();
    });
  });
}

function renderTaskDetail() {
  const task = state.selectedTaskDetail || selectedTask();
  if (!task) {
    elements.taskDetail.innerHTML = `<div class="empty">暂无选中任务</div>`;
    return;
  }
  const budget = task.budget || {};
  const config = task.execution_config || {};
  const relatedJobs = state.jobs.filter(
    (job) => job.task_id === task.id || job.resource_id === task.latest_run_id
  );
  const taskRecommendations = state.memoryRecommendations.filter(
    (entry) => !entry.item?.task_id || entry.item.task_id === task.id
  );
  elements.taskDetail.innerHTML = `
    <div class="task-detail-header">
      <div>
        <strong>${escapeHtml(formatTaskTitle(task.title))}</strong>
        <small>${escapeHtml(task.id)} / ${escapeHtml(formatTaskType(task.type || "coding"))}</small>
      </div>
      ${statusBadge(task.status || "created")}
    </div>
    <div class="task-detail-grid">
      <div><span>仓库</span><code>${escapeHtml(task.repo_path || "-")}</code></div>
      <div><span>测试命令</span><code>${escapeHtml(task.test_command || "-")}</code></div>
      <div><span>最大步骤</span><strong>${escapeHtml(budget.max_steps || "-")}</strong></div>
      <div><span>工具上限</span><strong>${escapeHtml(budget.max_tool_calls || "-")}</strong></div>
      <div><span>模型成本上限</span><strong>$${Number(budget.max_model_cost || 0).toFixed(3)}</strong></div>
      <div><span>测试超时</span><strong>${escapeHtml(task.test_timeout_seconds || "-")} 秒</strong></div>
    </div>
    <div class="task-detail-goal">
      <span>目标</span>
      <p>${escapeHtml(task.goal || "-")}</p>
    </div>
    <details class="task-config">
      <summary>查看执行配置</summary>
      <pre>${escapeHtml(formatJson(config))}</pre>
    </details>
    <div class="task-linked-grid">
      <div>
        <strong>关联后台任务</strong>
        ${renderMiniJobList(relatedJobs)}
      </div>
      <div>
        <strong>推荐记忆</strong>
        ${renderMiniMemoryRecommendations(taskRecommendations)}
      </div>
    </div>
  `;
}

function renderMiniJobList(items) {
  return items.length
    ? `<div class="mini-list">${items
        .slice(0, 4)
        .map((item) => {
          const summary = item.error_summary || formatJobResult(item.result_json) || `尝试 ${item.attempts || 0} 次`;
          return `
            <div class="mini-list-item">
              <span>${escapeHtml(formatJobKind(item.kind))} / ${escapeHtml(formatJobStatus(item.status))}</span>
              <small>${escapeHtml(summary)}</small>
            </div>
          `;
        })
        .join("")}</div>`
    : `<small class="muted-text">暂无关联后台任务</small>`;
}

function renderMiniMemoryRecommendations(items) {
  return items.length
    ? `<div class="mini-list">${items
        .slice(0, 4)
        .map((entry) => {
          const item = entry.item || {};
          return `
            <div class="mini-list-item">
              <span>${escapeHtml(formatMemoryType(item.memory_type))} / ${escapeHtml(formatFailureReason(item.key))}</span>
              <small>${escapeHtml(entry.reason || item.summary || "-")}</small>
            </div>
          `;
        })
        .join("")}</div>`
    : `<small class="muted-text">暂无推荐记忆</small>`;
}

function renderTrace() {
  elements.runId.textContent = state.selectedRun?.id || selectedTask()?.latest_run_id || "暂无运行";
  elements.runStatus.textContent = formatRunStatus(state.selectedRun?.status || selectedTask()?.status || "idle");
  const runStatus = state.selectedRun?.status;
  if (elements.pauseButton) {
    elements.pauseButton.disabled = !hasPermission("runs:write")
      || !["queued", "running"].includes(runStatus);
  }
  if (elements.resumeButton) {
    elements.resumeButton.disabled = !hasPermission("runs:write") || runStatus !== "paused";
  }
  elements.cancelButton.disabled = !hasPermission("runs:write")
    || !["queued", "running"].includes(state.selectedRun?.status);
  elements.timeline.innerHTML = state.steps
    .map(
      (step) => `
        <article class="step">
          <div class="step-index">${step.step_index}</div>
          <div class="step-body">
            <details class="step-details">
              <summary class="step-title">
                <span>${escapeHtml(formatPhase(step.phase))}</span>
                ${statusBadge(step.status)}
              </summary>
              <div class="step-detail-content">
                <small>步骤 ${escapeHtml(step.step_index)}${step.started_at ? ` · 开始 ${escapeHtml(formatTimestamp(step.started_at))}` : ""}${step.finished_at ? ` · 结束 ${escapeHtml(formatTimestamp(step.finished_at))}` : ""}${step.started_at || step.finished_at ? ` · 耗时 ${escapeHtml(formatDurationRange(step.started_at, step.finished_at))}` : ""} · 工具 ${escapeHtml(String(toolCallsForStep(step.id).length))} 次</small>
                ${step.thought_summary ? `<p>思考摘要：${escapeHtml(formatObservation(step.thought_summary))}</p>` : ""}
                ${step.metadata && Object.keys(step.metadata).length ? `<pre>${escapeHtml(formatJson(step.metadata))}</pre>` : ""}
                ${renderStepToolCalls(step.id)}
              </div>
            </details>
            <p>${escapeHtml(formatStepGoal(step.goal))}</p>
            ${step.action ? `<code>${escapeHtml(formatAction(step.action))}</code>` : ""}
            ${step.observation ? `<small>${escapeHtml(formatObservation(step.observation))}</small>` : ""}
          </div>
        </article>
      `
    )
    .join("") || `<div class="empty">暂无执行轨迹</div>`;
}

function renderEvidence() {
  const diff = state.artifacts.find((artifact) => artifact.type === "diff");
  const report = state.artifacts.find((artifact) => artifact.type === "report");
  const baselineLog = state.artifacts.find((artifact) => artifact.name === "baseline-test.log");
  const criticReview = state.artifacts.find((artifact) => artifact.name === "critic-review.json");
  const modelAssistArtifacts = state.artifacts.filter((artifact) => artifact.name?.startsWith("model-assist-"));
  const validationLog =
    state.artifacts.find((artifact) => artifact.name === "retry-validation-test.log") ||
    state.artifacts.find((artifact) => artifact.name === "validation-test.log");
  const metrics = state.selectedRun?.metrics || {};
  const diffSummary = formatDiffSummary(diff, criticReview);
  const sortedToolCalls = [...state.toolCalls].sort(
    (left, right) => new Date(left.created_at || 0) - new Date(right.created_at || 0)
  );
  elements.toolCallCount.textContent = String(state.toolCalls.length || state.selectedRun?.tool_call_count || 0);
  const baselineTestsPassed = Number(metrics.baseline_tests_passed || 0);
  const baselineTestsTotal = Number(metrics.baseline_tests_total || 0);
  const testPassedDelta = Number(metrics.test_passed_delta || 0);
  elements.baselineTestMetric.textContent = `${baselineTestsPassed}/${baselineTestsTotal}`;
  elements.testMetric.textContent = `${metrics.tests_passed || 0}/${metrics.tests_total || 0}`;
  elements.testDeltaMetric.textContent = `${testPassedDelta > 0 ? "+" : ""}${testPassedDelta}`;
  elements.testDeltaMetric.className = testPassedDelta > 0
    ? "metric-positive"
    : testPassedDelta < 0
      ? "metric-negative"
      : "";
  elements.traceMetric.textContent = `${Math.round((metrics.trace_completeness || 0) * 100)}%`;
  elements.diffBlock.textContent = diff
    ? `${diffSummary ? `${diffSummary}\n\n` : ""}${diff.content || "暂无代码差异产物"}`
    : "暂无代码差异产物";
  elements.reportBlock.textContent = report?.content ? formatReport(report.content) : "暂无报告产物";
  elements.baselineLogBlock.textContent = baselineLog ? formatLogArtifact(baselineLog.content) : "暂无基线日志";
  elements.validationLogBlock.textContent = validationLog ? formatLogArtifact(validationLog.content) : "暂无验证日志";
  elements.criticReviewBlock.textContent = criticReview ? formatCriticReview(criticReview) : "暂无评审结果";
  elements.modelAssistBlock.textContent = modelAssistArtifacts.length
    ? modelAssistArtifacts.map(formatModelAssistArtifact).join("\n\n")
    : "暂无模型辅助记录";
  elements.fileContentBlock.textContent = state.selectedFilePath
    ? `${state.selectedFilePath}${state.selectedFileTruncated ? "（内容已截断）" : ""}\n\n${state.selectedFileContent || "文件为空"}`
    : "请选择文件";
  if (state.selectedRun?.id && state.online) {
    elements.runExportLink.href = `${API_BASE}/runs/${state.selectedRun.id}/export`;
    elements.runExportLink.setAttribute("aria-disabled", "false");
  } else {
    elements.runExportLink.href = "#";
    elements.runExportLink.setAttribute("aria-disabled", "true");
  }
  renderFileTree();
  elements.toolCallList.innerHTML = sortedToolCalls.length
    ? sortedToolCalls.map((call) => renderToolCallCard(call)).join("")
    : `<div class="empty">暂无工具调用</div>`;
}

function renderStepToolCalls(stepId) {
  const calls = toolCallsForStep(stepId);
  if (!calls.length) {
    return `<small class="muted-text">此步骤暂无工具调用</small>`;
  }
  return `
    <div class="tool-call-list embedded">
      ${calls.map((call) => renderToolCallCard(call, { embedded: true })).join("")}
    </div>
  `;
}

function renderToolCallCard(call, { embedded = false } = {}) {
  const outputSummary = formatToolCallSummary(call.output);
  return `
    <details class="tool-call${embedded ? " embedded" : ""}">
      <summary>
        <strong>${escapeHtml(formatAction(call.tool_name))}</strong>
        ${statusBadge(call.status)}
        <small>${escapeHtml(formatDurationMs(call.duration_ms))}${call.error_message ? ` · ${escapeHtml(formatFailureReason(call.error_message))}` : ""}${outputSummary ? ` · ${escapeHtml(outputSummary)}` : ""}</small>
      </summary>
      <div class="tool-call-meta">
        <small>步骤 ${escapeHtml(call.step_id || "-")} · 创建 ${escapeHtml(formatTimestamp(call.created_at))}${call.finished_at ? ` · 完成 ${escapeHtml(formatTimestamp(call.finished_at))}` : ""}</small>
        ${call.error_message ? `<p class="tool-error">错误：${escapeHtml(formatFailureReason(call.error_message))}</p>` : ""}
      </div>
      <div class="tool-detail-grid">
        <pre>${escapeHtml(formatJson(call.input))}</pre>
        <pre>${escapeHtml(formatToolOutput(call.output))}</pre>
      </div>
    </details>
  `;
}

function formatToolOutput(output) {
  if (!output || typeof output !== "object") {
    return formatJson(output);
  }
  const sections = [];
  if (typeof output.status_summary === "string" && output.status_summary.trim()) {
    sections.push(`状态摘要：${output.status_summary}`);
  }
  if (typeof output.exit_code !== "undefined" && output.exit_code !== null) {
    sections.push(`退出码：${output.exit_code}`);
  }
  if (typeof output.stdout === "string" && output.stdout.trim()) {
    sections.push(`stdout\n${output.stdout}`);
  }
  if (typeof output.stderr === "string" && output.stderr.trim()) {
    sections.push(`stderr\n${output.stderr}`);
  }
  if (typeof output.diff === "string" && output.diff.trim()) {
    sections.push(`diff\n${output.diff}`);
  }
  if (typeof output.changed_files !== "undefined") {
    sections.push(`变更文件：${output.changed_files}`);
  }
  if (typeof output.changed_lines !== "undefined") {
    sections.push(`变更行数：${output.changed_lines}`);
  }
  sections.push(formatJson(output));
  return sections.join("\n\n");
}

function formatToolCallSummary(output) {
  if (!output || typeof output !== "object") return "";
  const parts = [];
  if (output.status_summary) parts.push(String(output.status_summary));
  if (output.changed_files !== undefined) parts.push(`${output.changed_files} 个文件`);
  if (output.changed_lines !== undefined) parts.push(`${output.changed_lines} 行`);
  if (output.tests_passed !== undefined && output.tests_total !== undefined) {
    parts.push(`测试 ${output.tests_passed}/${output.tests_total}`);
  }
  if (output.exit_code !== undefined && output.exit_code !== null) parts.push(`退出码 ${output.exit_code}`);
  return parts.join(" / ");
}

function formatDurationMs(value) {
  const duration = Number(value || 0);
  if (!Number.isFinite(duration) || duration <= 0) return "0毫秒";
  if (duration < 1000) return `${Math.round(duration)}毫秒`;
  const seconds = duration / 1000;
  return seconds >= 10 ? `${seconds.toFixed(0)}秒` : `${seconds.toFixed(1)}秒`;
}

function formatDurationRange(startAt, endAt) {
  if (!startAt || !endAt) return "进行中";
  const start = new Date(startAt);
  const end = new Date(endAt);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "未知";
  return formatDurationMs(end.getTime() - start.getTime());
}

function toolCallsForStep(stepId) {
  return state.toolCalls.filter((call) => call.step_id === stepId);
}

function formatDiffSummary(diffArtifact, criticReviewArtifact) {
  if (!diffArtifact) return "";
  const parts = [];
  const metadata = diffArtifact.metadata || {};
  if (metadata.changed_files !== undefined) parts.push(`变更文件 ${metadata.changed_files}`);
  if (metadata.changed_lines !== undefined) parts.push(`变更行数 ${metadata.changed_lines}`);
  if (metadata.retry_attempt !== undefined) parts.push(`重试轮次 ${metadata.retry_attempt}`);
  if (metadata.patch_source) parts.push(`补丁来源 ${metadata.patch_source === "model" ? "模型" : "任务回退"}`);
  const critic = parseJsonContent(criticReviewArtifact?.content);
  if (critic) {
    parts.push(`评审 ${critic.accepted ? "通过" : "拒绝"}`);
    if (critic.score !== undefined) {
      parts.push(`分数 ${Number(critic.score).toFixed(2)}`);
    }
    if (Array.isArray(critic.failed_checks) && critic.failed_checks.length) {
      parts.push(`未通过 ${critic.failed_checks.map((item) => formatCriticCheckName(item)).join("、")}`);
    }
  }
  return parts.join(" · ");
}

function parseJsonContent(value) {
  try {
    return JSON.parse(String(value || ""));
  } catch {
    return null;
  }
}

function renderFileTree() {
  const visibleLimit = 80;
  const rows = state.fileTree
    .slice(0, visibleLimit)
    .map(
      (item) => `
        <button class="file-row ${escapeHtml(item.kind)}" type="button" data-file-path="${escapeHtml(item.path)}" ${item.kind === "directory" ? "disabled" : ""}>
          <span>${escapeHtml(item.kind === "directory" ? "目录" : "文件")}</span>
          <code>${escapeHtml(item.path)}</code>
        </button>
      `
    )
    .join("");
  const isTruncated = state.fileTreeTruncated || state.fileTreeTotal > visibleLimit;
  const notice = isTruncated
    ? `<div class="muted-text file-tree-notice">文件较多，仅显示前 ${Math.min(state.fileTreeTotal || state.fileTree.length, visibleLimit)} 项。</div>`
    : "";
  elements.fileTreeList.innerHTML = `${notice}${rows || `<div class="empty">暂无文件树</div>`}`;
  document.querySelectorAll("[data-file-path]").forEach((button) => {
    button.addEventListener("click", () => loadTaskFile(button.dataset.filePath));
  });
}

function renderBenchmark() {
  const gate = state.releaseGate || state.releaseGates[0];
  elements.gateStatus.textContent = gate
    ? formatReleaseGateStatus(gate.status)
    : "未检查";
  elements.gateStatus.className = gate?.passed ? "gate-passed" : gate ? "gate-blocked" : "";
  elements.acceptanceStatus.textContent = state.acceptance?.status === "queued"
    ? "已提交，等待 Worker"
    : state.acceptance
      ? `${state.acceptance.passed_count || 0}/${state.acceptance.task_count || 0} 通过`
      : "未执行";
  elements.acceptanceStatus.className = state.acceptance?.status === "queued"
    ? ""
    : state.acceptance?.all_passed
      ? "gate-passed"
      : state.acceptance
        ? "gate-blocked"
        : "";
  if (state.comparison?.status === "queued") {
    elements.benchmarkRows.innerHTML = `<tr><td colspan="6">策略对比已提交，等待 Worker 处理（${escapeHtml(state.comparison.job_id || "-")}）</td></tr>`;
    renderBenchmarkHistory();
    return;
  }
  if (state.comparison?.items?.length) {
    elements.benchmarkRows.innerHTML = state.comparison.items
      .map(
        (item) => `
          <tr>
            <td>${escapeHtml(formatStrategyName(item.agent_strategy_id))}</td>
            <td>${Number(item.success_rate || 0).toFixed(2)}</td>
            <td>${Number(item.avg_score || 0).toFixed(1)}</td>
            <td>${Math.round(Number(item.avg_trace_completeness || 0) * 100)}%</td>
            <td>${item.regression_count || 0}</td>
            <td>${escapeHtml(formatComparisonStatus(item.status || "-"))}</td>
          </tr>
        `
      )
      .join("");
    renderBenchmarkHistory();
    return;
  }
  const latest = state.evaluations[0];
  const items = latest?.items || [];
  elements.benchmarkRows.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.task_id)}</td>
          <td>${item.success ? "是" : "否"}</td>
          <td>${Number(item.score || 0).toFixed(1)}</td>
          <td>${Math.round(Number(item.metrics?.trace_completeness || 0) * 100)}%</td>
          <td>${latest?.summary?.regression_count || 0}</td>
          <td>${escapeHtml(formatFailureReason(item.failure_reason || "-"))}${formatCriteriaSummary(item.metrics) ? `<br><small>${escapeHtml(formatCriteriaSummary(item.metrics))}</small>` : ""}</td>
        </tr>
      `
    )
    .join("") || `<tr><td colspan="6">暂无评测记录</td></tr>`;
  renderBenchmarkHistory();
}


function renderBenchmarkHistory() {
  elements.evaluationHistory.innerHTML = state.evaluations
    .slice(0, 5)
    .map(
      (item) => `
        <div class="dataset-item">
          <strong>${escapeHtml(formatStrategyName(item.agent_strategy_id))}</strong>
          <span>${Math.round(Number(item.summary?.success_rate || 0) * 100)}% / ${Number(item.summary?.avg_score || 0).toFixed(1)} / ${escapeHtml(formatRunStatus(item.status))}</span>
          <small>${escapeHtml(item.id)} · ${escapeHtml(formatEvaluationBaseline(item.summary))} · ${escapeHtml(formatFailureDistribution(item.summary?.failure_type_distribution))} · <a href="${API_BASE}/evaluations/runs/${escapeHtml(item.id)}/report">导出报告</a></small>
        </div>
      `
    )
    .join("") || `<div class="empty">暂无评测历史</div>`;
  elements.gateHistory.innerHTML = state.releaseGates
    .slice(0, 5)
    .map(
      (item) => `
        <div class="dataset-item">
          <strong>${escapeHtml(formatStrategyName(item.agent_strategy_id))}</strong>
          <span>${escapeHtml(formatReleaseGateStatus(item.status))} / ${escapeHtml(formatReleaseStage(item.release_stage))}${item.canary_percentage ? ` ${item.canary_percentage}%` : ""}</span>
          <small>${escapeHtml(item.evaluation_run_id)} · ${escapeHtml(formatGateChecks(item.checks))}</small>
        </div>
      `
    )
    .join("") || `<div class="empty">暂无发布门禁记录</div>`;
}

function currentDatasetFilters() {
  return {
    quality_label: elements.qualityFilterSelect?.value || "",
    trace_type: elements.traceFilterSelect?.value || "",
    use_case: elements.useCaseFilterSelect?.value || "",
    status: elements.statusFilterSelect?.value || ""
  };
}

function matchesDatasetFilters(item, filters) {
  return (
    (!filters.quality_label || item.quality_label === filters.quality_label) &&
    (!filters.trace_type || item.trace_type === filters.trace_type) &&
    (!filters.use_case || item.use_case === filters.use_case) &&
    (!filters.status || item.status === filters.status)
  );
}

function renderDataset() {
  const filters = currentDatasetFilters();
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value)
  );
  const queryString = query.toString();
  const datasetQuerySuffix = queryString ? `?${queryString}` : "";
  if (elements.exportLink) {
    elements.exportLink.href = `${API_BASE}/datasets/export${datasetQuerySuffix}`;
  }
  if (elements.failureExportLink) {
    const failureQuery = new URLSearchParams(query);
    failureQuery.set("include_unlabeled", "true");
    elements.failureExportLink.href = `${API_BASE}/datasets/failure-cases/export?${failureQuery.toString()}`;
  }
  if (elements.trainingExportLink) {
    elements.trainingExportLink.href = `${API_BASE}/datasets/training-bundle/export?include_pending=true`;
  }
  if (elements.preferenceExportLink) {
    const preferenceQuery = new URLSearchParams();
    if (filters.use_case) preferenceQuery.set("use_case", filters.use_case);
    elements.preferenceExportLink.href = `${API_BASE}/datasets/preference-pairs/export${preferenceQuery.toString() ? `?${preferenceQuery.toString()}` : ""}`;
  }
  const filteredTraceItems = state.datasetItems.filter((item) => matchesDatasetFilters(item, filters));
  const filteredPreferencePairs = state.preferencePairs.filter(
    (item) => !filters.use_case || item.use_case === filters.use_case
  );
  elements.datasetCount.textContent = `${filteredTraceItems.length}/${state.datasetItems.length} 条轨迹 / ${filteredPreferencePairs.length}/${state.preferencePairs.length} 个偏好对 / ${state.datasetSnapshots.length} 个版本`;
  elements.datasetQualityBlock.textContent = state.datasetQuality
    ? [
        `轨迹候选：${state.datasetQuality.trace_item_count || 0}`,
        `已入库轨迹：${state.datasetQuality.approved_trace_item_count || 0}`,
        `偏好对：${state.datasetQuality.preference_pair_count || 0}`,
        `已入库偏好：${state.datasetQuality.approved_preference_pair_count || 0}`,
        `失败样本：${state.datasetQuality.failure_case_count || 0}`,
        `SFT 可用：${state.datasetQuality.usable_for_sft_count || 0}`,
        `偏好可用：${state.datasetQuality.usable_for_preference_count || 0}`,
        `审核率：${formatPercent(state.datasetQuality.approval_rate || 0)}`,
        `失败分布：${formatFailureDistribution(state.datasetQuality.failure_type_distribution || {})}`
      ].join("\n")
    : "暂无质量报告";
  const traceItems = filteredTraceItems
    .map(
      (item) => `
        <div class="dataset-item trace-dataset-item">
          <strong>${escapeHtml(formatTraceType(item.trace_type))}</strong>
          <span>${escapeHtml(formatQualityLabel(item.quality_label))} / ${escapeHtml(formatUseCase(item.use_case))} / ${escapeHtml(formatFailureReason(item.failure_type || "-"))} / ${escapeHtml(formatDatasetStatus(item.status))}</span>
          <small>${escapeHtml(item.agent_run_id)}${item.root_cause ? ` · 根因：${escapeHtml(item.root_cause)}` : ""}${item.human_preferred_action ? ` · 建议：${escapeHtml(item.human_preferred_action)}` : ""}</small>
          ${hasPermission("datasets:review") ? `
            <div class="approval-actions">
              <button class="mini-button" type="button" data-trace-edit-id="${escapeHtml(item.id)}">编辑标注</button>
              ${item.status !== "approved" ? `<button class="mini-button" type="button" data-trace-review-id="${escapeHtml(item.id)}" data-trace-decision="approved">审核通过</button>` : ""}
              ${item.status !== "rejected" ? `<button class="mini-button danger" type="button" data-trace-review-id="${escapeHtml(item.id)}" data-trace-decision="rejected">标记拒绝</button>` : ""}
              ${item.use_case !== "failure_case" ? `<button class="mini-button" type="button" data-trace-review-id="${escapeHtml(item.id)}" data-trace-decision="failure_case">转失败样本</button>` : ""}
            </div>
          ` : ""}
        </div>
      `
    )
    .join("");
  const preferenceItems = filteredPreferencePairs
    .slice(0, 6)
    .map(
      (item) => `
        <div class="dataset-item">
          <strong>偏好对 / ${escapeHtml(formatDatasetStatus(item.status))}</strong>
          <span>${escapeHtml(item.chosen_run_id)} 优于 ${escapeHtml(item.rejected_run_id)}</span>
          <small>${escapeHtml(item.rationale || item.use_case || "-")}</small>
          ${hasPermission("datasets:review") && (item.status === "candidate" || item.status === "reviewed") ? `
            <div class="approval-actions">
              <button class="mini-button" type="button" data-preference-review-id="${escapeHtml(item.id)}" data-preference-review-status="approved">审核通过</button>
              <button class="mini-button danger" type="button" data-preference-review-id="${escapeHtml(item.id)}" data-preference-review-status="rejected">标记拒绝</button>
            </div>
          ` : ""}
        </div>
      `
    )
    .join("");
  elements.datasetList.innerHTML = traceItems || preferenceItems
    ? traceItems + preferenceItems
    : `<div class="empty">暂无符合筛选条件的数据候选</div>`;
  document.querySelectorAll("[data-trace-review-id]").forEach((button) => {
    button.addEventListener("click", () => reviewTraceItem(
      button.dataset.traceReviewId,
      button.dataset.traceDecision
    ));
  });
  document.querySelectorAll("[data-trace-edit-id]").forEach((button) => {
    button.addEventListener("click", () => selectDatasetItem(button.dataset.traceEditId));
  });
  document.querySelectorAll("[data-preference-review-id]").forEach((button) => {
    button.addEventListener("click", () => reviewPreferencePair(
      button.dataset.preferenceReviewId,
      button.dataset.preferenceReviewStatus
    ));
  });
  elements.snapshotList.innerHTML = state.datasetSnapshots
    .slice(0, 6)
    .map(
      (item) => `
        <div class="dataset-item">
          <strong>${escapeHtml(item.version_name)}</strong>
          <span>轨迹 ${item.item_count || 0} / 偏好 ${item.preference_pair_count || 0} / ${escapeHtml(formatDatasetStatus(item.status))}</span>
          <small><a href="${API_BASE}/datasets/snapshots/${escapeHtml(item.id)}/export">导出 ${escapeHtml(item.id)}</a></small>
        </div>
      `
    )
    .join("") || `<div class="empty">暂无数据集版本</div>`;
}

function renderAuditLogs() {
  const activeFilters = Object.entries(state.auditFilters)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}=${value}`)
    .join(" / ");
  elements.auditLogCount.textContent = `${state.auditLogs.length} 条记录${activeFilters ? ` · ${activeFilters}` : ""}`;
  elements.auditLogList.innerHTML = state.auditLogs
    .slice(0, 8)
    .map(
      (item) => `
        <div class="dataset-item">
          <strong>${escapeHtml(formatAuditAction(item.action))}</strong>
          <span>${escapeHtml(formatAuditDecision(item.decision || "-"))} / ${escapeHtml(formatAuditResource(item.resource_type))}</span>
          <small>${escapeHtml(item.resource_id)}${item.detail_json ? ` · ${escapeHtml(formatAuditDetail(item.detail_json))}` : ""}</small>
        </div>
      `
    )
    .join("") || `<div class="empty">暂无审计记录</div>`;
}

function renderMemory() {
  const selectedMemory = state.selectedMemoryId
    ? state.memoryItems.find((item) => item.id === state.selectedMemoryId) || null
    : null;
  if (!state.memoryFormDirty) {
    syncMemoryFormToSelection(selectedMemory, { force: !state.memoryFormInitialized });
  }
  elements.memoryCount.textContent = `${state.memoryItems.length} 条记忆`;
  const recommendationsHtml = state.memoryRecommendations.length
    ? `
      <div class="artifact-block">
        <h3>推荐经验</h3>
        <div class="dataset-list">
          ${state.memoryRecommendations
            .slice(0, 5)
            .map((entry) => {
              const item = entry.item || {};
              return `
                <div class="dataset-item">
                  <strong>${escapeHtml(formatMemoryType(item.memory_type))} / ${escapeHtml(formatFailureReason(item.key))}</strong>
                  <span>${escapeHtml(item.summary || "-")}</span>
                  <small>${escapeHtml(entry.reason || "相关记忆")} · ${escapeHtml(formatMemoryScope(item.scope))} · 分数 ${escapeHtml(String(entry.score ?? 0))}</small>
                  ${hasPermission("datasets:review") ? `
                    <div class="approval-actions">
                      <button class="mini-button" type="button" data-memory-edit-id="${escapeHtml(item.id)}">编辑</button>
                    </div>
                  ` : ""}
                </div>
              `;
            })
            .join("")}
        </div>
      </div>
    `
    : "";
  const memoryItemsHtml = state.memoryItems
    .slice(0, 8)
    .map(
      (item) => `
        <div class="dataset-item ${state.selectedMemoryId === item.id ? "selected" : ""}">
          <strong>${escapeHtml(formatMemoryType(item.memory_type))} / ${escapeHtml(formatFailureReason(item.key))}</strong>
          <span>${escapeHtml(item.summary || "-")} / ${escapeHtml(formatDatasetStatus(item.status))}</span>
          <small>${escapeHtml(formatMemoryScope(item.scope))} · ${escapeHtml(item.task_id || item.agent_run_id || "全局记忆")}</small>
          ${hasPermission("datasets:review") ? `
            <div class="approval-actions">
              <button class="mini-button" type="button" data-memory-edit-id="${escapeHtml(item.id)}">编辑</button>
              ${item.status === "active" ? `<button class="mini-button" type="button" data-memory-archive-id="${escapeHtml(item.id)}">归档</button>` : ""}
            </div>
          ` : ""}
        </div>
      `
    )
    .join("") || `<div class="empty">暂无项目记忆</div>`;
  elements.memoryList.innerHTML = `${recommendationsHtml}${memoryItemsHtml}`;
  document.querySelectorAll("[data-memory-edit-id]").forEach((button) => {
    button.addEventListener("click", () => selectMemoryItem(button.dataset.memoryEditId));
  });
  document.querySelectorAll("[data-memory-archive-id]").forEach((button) => {
    button.addEventListener("click", () => archiveMemoryItem(button.dataset.memoryArchiveId));
  });
}

function renderApprovals() {
  elements.approvalCount.textContent = `${state.approvals.length} 条请求`;
  elements.approvalList.innerHTML = state.approvals
    .slice(0, 8)
    .map(
      (item) => {
        const consumed = item.consumed_by_run_ids?.length
          ? ` · 已恢复 ${item.consumed_by_run_ids.join("、")}`
          : "";
        const policy = item.policy_version_id ? ` · 策略 ${item.policy_version_id}` : "";
        return `
        <div class="dataset-item approval-item">
          <strong>${escapeHtml(formatAction(item.tool_name))} / ${escapeHtml(formatApprovalStatus(item.status))}</strong>
          <span>${escapeHtml(formatFailureReason(item.reason || "-"))}</span>
          <small>${escapeHtml(item.run_id)}${escapeHtml(policy)}${escapeHtml(consumed)}</small>
          ${
            item.status === "pending" && hasPermission("approvals:decide")
              ? `<div class="approval-actions">
                  <button class="mini-button" type="button" data-approval-id="${escapeHtml(item.id)}" data-decision="approved">通过</button>
                  <button class="mini-button danger" type="button" data-approval-id="${escapeHtml(item.id)}" data-decision="denied">拒绝</button>
                </div>`
              : item.status === "approved" && !item.consumed_by_run_ids?.length && hasPermission("runs:write")
                ? `<div class="approval-actions">
                    <button class="mini-button" type="button" data-resume-run-id="${escapeHtml(item.run_id)}">恢复运行</button>
                  </div>`
              : ""
          }
        </div>
      `
      }
    )
    .join("") || `<div class="empty">暂无审批请求</div>`;
  document.querySelectorAll("[data-approval-id]").forEach((button) => {
    button.addEventListener("click", () => decideApproval(button.dataset.approvalId, button.dataset.decision));
  });
  document.querySelectorAll("[data-resume-run-id]").forEach((button) => {
    button.addEventListener("click", () => resumeApprovedRun(button.dataset.resumeRunId));
  });
}

function renderResearch() {
  elements.researchCount.textContent = `${state.researchBriefs.length} 个研究简报`;
  elements.researchAcceptanceStatus.textContent = state.researchAcceptance?.status === "queued"
    ? "已提交，等待 Worker"
    : state.researchAcceptance
      ? `${state.researchAcceptance.passed_count || 0}/${state.researchAcceptance.task_count || 0} 通过`
      : "未执行";
  elements.researchAcceptanceStatus.className = state.researchAcceptance?.status === "queued"
    ? ""
    : state.researchAcceptance?.all_passed
      ? "gate-passed"
      : state.researchAcceptance
        ? "gate-blocked"
        : "";
  if (elements.researchAcceptanceSummary) {
    if (state.researchAcceptance) {
      const reportUrl = state.researchAcceptance.report_path
        ? apiUrl(state.researchAcceptance.report_path)
        : "";
      elements.researchAcceptanceSummary.innerHTML = `
        <div class="dataset-item">
          <strong>最近研究验收 / ${escapeHtml(state.researchAcceptance.status === "queued" ? "等待 Worker" : (state.researchAcceptance.all_passed ? "全部通过" : "存在失败"))}</strong>
          <span>通过 ${escapeHtml(state.researchAcceptance.passed_count || 0)}/${escapeHtml(state.researchAcceptance.task_count || 0)} · 平均分 ${escapeHtml(state.researchAcceptance.avg_score ?? "-")} · 目录 ${escapeHtml(state.researchAcceptance.catalog_count || 0)} 题</span>
          <small>${escapeHtml(state.researchAcceptance.job_id || "-")}${reportUrl ? ` · <a href="${escapeHtml(reportUrl)}">导出验收报告</a>` : ""}</small>
        </div>
      `;
    } else {
      elements.researchAcceptanceSummary.innerHTML = `<div class="empty">暂无研究验收报告</div>`;
    }
  }
  elements.researchBriefSelect.innerHTML = state.researchBriefs
    .map(
      (brief) =>
        `<option value="${escapeHtml(brief.id)}">${escapeHtml(brief.question)} / ${escapeHtml(brief.id)}</option>`
    )
    .join("") || `<option value="">暂无研究简报</option>`;
  const selectedBriefId = elements.researchBriefSelect.value || state.researchBriefs[0]?.id || "";
  if (selectedBriefId) elements.researchBriefSelect.value = selectedBriefId;
  elements.researchEvidenceList.innerHTML = state.researchEvidence
    .slice(0, 30)
    .map(
      (paper) => {
        const metadata = formatEvidenceMetadata(paper.source_metadata || {});
        return `
        <label class="dataset-item selectable-item">
          <span class="selectable-main">
            <input type="checkbox" data-evidence-check="${escapeHtml(paper.id)}" />
            <strong>${escapeHtml(paper.title)}</strong>
          </span>
          <span>${escapeHtml(paper.domain)} / ${escapeHtml(formatEvidenceSource(paper.source))} / 证据 ${paper.evidence_snippets?.length || 0} 条</span>
          <small>${escapeHtml(paper.id)}${metadata ? ` · ${escapeHtml(metadata)}` : ""}${paper.url ? ` · ${escapeHtml(paper.url)}` : ""}</small>
        </label>
      `;
      }
    )
    .join("") || `<div class="empty">暂无证据，请先导入文本或 PDF。</div>`;
  elements.researchList.innerHTML = state.researchBriefs
    .slice(0, 6)
    .map((brief) => {
      const notebook = state.notebookRuns.find((item) => item.brief_id === brief.id);
      const source = formatEvidenceSource(brief.source_summary?.provider || "local");
      const fallback = brief.source_summary?.fallback ? " / 回退" : "";
      const modelAssist = brief.source_summary?.model_assist;
      const modelLabel = modelAssist
        ? `模型 ${escapeHtml(modelAssist.model_name)} / ${modelAssist.fallback_used ? "本地回退" : "在线"}`
        : "暂无模型辅助";
      const citations = (brief.citations || [])
        .map(
          (citation) => `
            <div class="citation-row">
              <span>${escapeHtml(citation.title || citation.paper_id)} / ${escapeHtml(formatCitationStatus(citation.status))}</span>
              ${
                citation.status !== "approved" && hasPermission("research:write")
                  ? `<button class="mini-button" type="button" data-review-brief="${escapeHtml(brief.id)}" data-review-paper="${escapeHtml(citation.paper_id)}" data-review-status="approved">通过</button>`
                  : ""
              }
              ${
                citation.status !== "rejected" && hasPermission("research:write")
                  ? `<button class="mini-button danger" type="button" data-review-brief="${escapeHtml(brief.id)}" data-review-paper="${escapeHtml(citation.paper_id)}" data-review-status="rejected">退回</button>`
                  : ""
              }
            </div>
          `
        )
        .join("") || `<span class="muted-text">暂无引用</span>`;
      return `
        <div class="dataset-item">
          <strong>${escapeHtml(brief.question)}</strong>
          <span>${escapeHtml(brief.domain)} / ${escapeHtml(source)}${escapeHtml(fallback)} / 论文 ${brief.papers?.length || 0} / 假设 ${brief.hypotheses?.length || 0} / 实验 ${brief.experiments?.length || 0}</span>
          <span>${modelLabel}</span>
          <div class="citation-list">${citations}</div>
          <small>${escapeHtml(brief.id)}${notebook ? ` · 笔记本 ${escapeHtml(formatJobStatus(notebook.status))}` : ""} · <a href="${API_BASE}/research/briefs/${escapeHtml(brief.id)}/report">导出报告</a></small>
        </div>
      `;
    })
    .join("") || `<div class="empty">暂无研究简报</div>`;
  document.querySelectorAll("[data-review-brief]").forEach((button) => {
    button.addEventListener("click", () =>
      reviewResearchCitation(
        button.dataset.reviewBrief,
        button.dataset.reviewPaper,
        button.dataset.reviewStatus
      )
    );
  });
}

function renderPlatform() {
  syncSessionUserSelect();
  const selectedWorkspace = state.selectedWorkspaceId
    ? state.workspaces.find((item) => item.id === state.selectedWorkspaceId) || null
    : null;
  const selectedUser = state.selectedUserId
    ? state.users.find((item) => item.id === state.selectedUserId) || null
    : null;
  syncWorkspaceOptions();
  syncWorkspaceForm(selectedWorkspace);
  syncUserForm(selectedUser);
  const backend = state.storage?.backend || "unknown";
  const sandbox = state.sandbox?.isolation || "unknown";
  const artifactStore = state.storage?.artifact_store_backend ? formatArtifactStoreBackend(state.storage.artifact_store_backend) : "内置产物";
  const storageReady = state.storage?.artifact_store_ready === false ? "未就绪" : "就绪";
  const migrationSummary = state.storage?.postgres_migrations?.available
    ? ` / ${state.storage.postgres_migrations.recorded ?? state.storage.postgres_migrations.applied ?? 0}/${state.storage.postgres_migrations.available} 迁移`
    : "";
  elements.platformStatus.textContent = `${backend} / ${sandbox} / ${artifactStore} / ${storageReady}${migrationSummary}`;
  elements.modelList.innerHTML = state.models
    .map(
      (item) => {
        const health = state.modelHealth.find((entry) => entry.model_id === item.id);
        const healthLabel = health
          ? health.healthy
            ? "健康"
            : `需检查：${formatModelHealthReason(health.reason)}`
          : "未检查";
        return `
        <div class="dataset-item ${state.selectedModelId === item.id ? "selected" : ""}">
          <strong>${escapeHtml(item.model_name)}</strong>
          <span>${escapeHtml(formatModelProvider(item.provider))} / ${escapeHtml(formatModelRole(item.role))} / ${Number(item.cost_per_1k_tokens || 0).toFixed(4)} 每千 Token / ${escapeHtml(formatModelSource(item.config))}</span>
          <small>${escapeHtml(item.id)} · ${escapeHtml(formatModelConfigSummary(item.config))}</small>
          <small>${escapeHtml(formatModelStatus(item.status))} / ${escapeHtml(healthLabel)}</small>
          <div class="approval-actions">
            <button class="mini-button" type="button" data-model-edit-id="${escapeHtml(item.id)}">编辑</button>
          </div>
        </div>
      `;
      }
    )
    .join("") || `<div class="empty">暂无模型配置</div>`;
  elements.modelInvokeBlock.textContent = state.modelInvocation
    ? [
        `模型：${state.modelInvocation.model_name}`,
        `提供方：${formatModelProvider(state.modelInvocation.provider)}`,
        `成本估算：$${Number(state.modelInvocation.estimated_cost || 0).toFixed(6)}`,
        `尝试次数：${state.modelInvocation.attempts || 1}`,
        `回退原因：${state.modelInvocation.fallback_reason ? formatFailureReason(state.modelInvocation.fallback_reason) : "-"}`,
        `回退：${state.modelInvocation.fallback_used ? "是" : "否"}`,
        "",
        state.modelInvocation.output_text || ""
      ].join("\n")
    : "暂无试调结果";
  elements.extensionList.innerHTML = state.extensions
    .map(
      (item) => {
        const health = state.extensionHealth[item.id];
        const healthLabel = health
          ? health.healthy
            ? "健康"
            : `需检查：${formatFailureReason(health.reason || "-")}`
          : "未检查";
        return `
        <div class="dataset-item">
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(formatExtensionType(item.type))} / ${escapeHtml(formatExtensionStatus(item.status))} / ${escapeHtml(healthLabel)}</span>
          <small>${escapeHtml(item.description || item.id)}</small>
          <div class="approval-actions">
            <button class="mini-button" type="button" data-extension-health-id="${escapeHtml(item.id)}">检查</button>
            ${hasPermission("admin") ? `
              ${item.type === "mcp_tool" || item.type === "skill" ? `<button class="mini-button" type="button" data-extension-invoke-id="${escapeHtml(item.id)}" data-extension-invoke-type="${escapeHtml(item.type)}" data-extension-action="inspect">试调</button>` : ""}
              ${
                item.status === "enabled"
                  ? `<button class="mini-button danger" type="button" data-extension-status-id="${escapeHtml(item.id)}" data-extension-status="disabled">停用</button>`
                  : `<button class="mini-button" type="button" data-extension-status-id="${escapeHtml(item.id)}" data-extension-status="enabled">启用</button>`
              }
              ${item.type === "hook" ? `<button class="mini-button" type="button" data-hook-event="${escapeHtml(item.config?.event_types?.[0] || "run.completed")}">测试 Hook</button>` : ""}
            ` : ""}
          </div>
        </div>
      `
      }
    )
    .join("") || `<div class="empty">暂无扩展</div>`;
  if (state.hookDispatches.length) {
    elements.extensionList.innerHTML += state.hookDispatches
      .slice(0, 3)
      .map(
        (item) => `
          <div class="dataset-item">
            <strong>钩子派发记录 / ${escapeHtml(formatHookEvent(item.event_type))}</strong>
            <span>送达 ${item.delivered || 0} / 跳过 ${item.skipped || 0}</span>
            <small>${escapeHtml(item.id)}${item.job_id ? ` · 任务 ${escapeHtml(item.job_id)}` : ""}</small>
          </div>
        `
      )
      .join("");
  }
  document.querySelectorAll("[data-extension-health-id]").forEach((button) => {
    button.addEventListener("click", () => checkExtensionHealth(button.dataset.extensionHealthId));
  });
  document.querySelectorAll("[data-extension-invoke-id]").forEach((button) => {
    button.addEventListener("click", () =>
      invokeExtension(
        button.dataset.extensionInvokeId,
        button.dataset.extensionAction,
        button.dataset.extensionInvokeType
      )
    );
  });
  document.querySelectorAll("[data-extension-status-id]").forEach((button) => {
    button.addEventListener("click", () =>
      updateExtensionStatus(button.dataset.extensionStatusId, button.dataset.extensionStatus)
    );
  });
  document.querySelectorAll("[data-hook-event]").forEach((button) => {
    button.addEventListener("click", () => dispatchTestHook(button.dataset.hookEvent));
  });
  document.querySelectorAll("[data-model-edit-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const model = state.models.find((item) => item.id === button.dataset.modelEditId);
      if (model) {
        syncModelFormToSelection(model);
      }
    });
  });
  elements.toolList.innerHTML = state.tools
    .map(
      (item) => `
        <div class="dataset-item">
          <strong>${escapeHtml(formatAction(item.name))}</strong>
          <span>风险 ${escapeHtml(item.risk_level)} / 沙箱 ${item.sandbox_required ? "需要" : "可选"}</span>
          <small>${escapeHtml(formatToolDescription(item.name, item.description))} · 超时 ${escapeHtml(item.timeout_seconds || "-")} 秒</small>
        </div>
      `
    )
    .join("") || `<div class="empty">暂无内置工具</div>`;
  elements.strategyRuntimeList.innerHTML = state.strategies
    .map((item) => {
      const config = item.runtime_config || {};
      const taskType = formatStrategyTaskType(item.task_type);
      const runtimeSummary = formatStrategyRuntimeSummary(config);
      return `
        <div class="dataset-item ${state.selectedStrategyId === item.id ? "selected" : ""}">
          <strong>${escapeHtml(formatStrategyName(item.name || item.id))}</strong>
          <span>${escapeHtml(taskType)} / ${escapeHtml(formatStrategyStatus(item.status))} / ${escapeHtml(`最大步骤 ${item.max_steps || 20}`)}</span>
          <small>${escapeHtml(item.id)} · ${escapeHtml(runtimeSummary)} · ${item.memory_enabled ? "启用记忆" : "未启用记忆"}</small>
          ${hasPermission("admin") ? `
            <div class="approval-actions">
              <button class="mini-button" type="button" data-strategy-edit-id="${escapeHtml(item.id)}">编辑</button>
              <button class="mini-button" type="button" data-strategy-transition-id="${escapeHtml(item.id)}" data-strategy-transition-action="candidate">候选</button>
              <button class="mini-button" type="button" data-strategy-transition-id="${escapeHtml(item.id)}" data-strategy-transition-action="promote">启用</button>
              <button class="mini-button danger" type="button" data-strategy-transition-id="${escapeHtml(item.id)}" data-strategy-transition-action="rollback">回滚</button>
            </div>
          ` : ""}
        </div>
      `;
    })
    .join("") || `<div class="empty">暂无策略配置</div>`;
  elements.policyList.innerHTML = state.policies
    .map((item) => {
      return `
        <div class="dataset-item ${state.selectedPolicyId === item.id ? "selected" : ""}">
          <strong>${escapeHtml(formatPolicyName(item.name || item.id))}</strong>
          <span>${escapeHtml(formatPolicyStatus(item.status))} / ${escapeHtml(formatPolicySummary(item))}</span>
          <small>${escapeHtml(item.id)} · ${escapeHtml((item.allowed_tools || []).slice(0, 4).join("、") || "暂无工具")} ${Array.isArray(item.allowed_tools) && item.allowed_tools.length > 4 ? "…" : ""}</small>
          ${hasPermission("admin") ? `
            <div class="approval-actions">
              <button class="mini-button" type="button" data-policy-edit-id="${escapeHtml(item.id)}">编辑</button>
            </div>
          ` : ""}
        </div>
      `;
    })
    .join("") || `<div class="empty">暂无安全策略</div>`;
  document.querySelectorAll("[data-strategy-edit-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const strategy = state.strategies.find((item) => item.id === button.dataset.strategyEditId);
      if (strategy) {
        syncStrategyFormToSelection(strategy);
      }
    });
  });
  document.querySelectorAll("[data-strategy-transition-id]").forEach((button) => {
    button.addEventListener("click", () =>
      transitionStrategy(button.dataset.strategyTransitionId, button.dataset.strategyTransitionAction)
    );
  });
  document.querySelectorAll("[data-policy-edit-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const policy = state.policies.find((item) => item.id === button.dataset.policyEditId);
      if (policy) {
        syncPolicyFormToSelection(policy);
      }
    });
  });
  syncPolicyPreviewForm();
  if (elements.policyPreviewBlock) {
    elements.policyPreviewBlock.textContent = state.policyPreview
      ? [
          `工具：${state.policyPreview.tool_name}`,
          `策略：${state.policyPreview.policy_version_id}`,
          `仓库：${state.policyPreview.repo_path || "-"}`,
          `允许：${state.policyPreview.allowed ? "是" : "否"}`,
          `需审批：${state.policyPreview.requires_approval ? "是" : "否"}`,
          state.policyPreview.reason ? `原因：${state.policyPreview.reason}` : ""
        ].filter(Boolean).join("\n")
      : "暂无策略预检结果";
  }
  const queue = state.jobSummary?.queue || {};
  const queueBackend = formatQueueBackend(queue.backend);
  const queueName = queue.queue_name ? ` / 队列 ${escapeHtml(queue.queue_name)}` : "";
  const queueConnection = queue.backend === "redis"
    ? ` / 连接 ${queue.connected ? "已连接" : "未连接"}`
    : " / 本地执行";
  const queueWorker = queue.backend === "redis"
    ? ` / Worker ${queue.worker_enabled === false ? "外置" : (queue.worker_running ? "运行中" : "已停止")}`
    : "";
  const jobSummary = state.jobSummary
    ? `
      <div class="dataset-item">
        <strong>任务队列 / ${escapeHtml(queueBackend)}</strong>
        <span>总数 ${state.jobSummary.total || 0} / 排队 ${state.jobSummary.by_status?.queued || 0} / 运行 ${state.jobSummary.by_status?.running || 0}</span>
        <small>失败 ${state.jobSummary.by_status?.failed || 0} / 可重试 ${state.jobSummary.retryable_failed || 0} / 请求取消 ${state.jobSummary.cancel_requested || 0}${queueName}${queueConnection}${queueWorker}</small>
      </div>
    `
    : "";
  const jobRows = state.jobs
    .slice(0, 8)
    .map(
      (item) => {
        const summary = item.error_summary || formatJobResult(item.result_json) || `尝试 ${item.attempts || 0} 次`;
        const relation = formatJobRelation(item);
        const cancelButton = canCancelJob(item)
          ? `<button class="mini-button danger" type="button" data-job-cancel-id="${escapeHtml(item.id)}">取消</button>`
          : "";
        const retryButton = canRetryJob(item)
          ? `<button class="mini-button" type="button" data-job-retry-id="${escapeHtml(item.id)}">重试</button>`
          : "";
        const resumeButton = canResumeJob(item)
          ? `<button class="mini-button" type="button" data-job-resume-id="${escapeHtml(item.id)}">恢复</button>`
          : "";
        return `
        <div class="dataset-item">
          <strong>${escapeHtml(formatJobKind(item.kind))} / ${escapeHtml(formatJobStatus(item.status))}</strong>
          <span>${escapeHtml(item.resource_id)}${item.cancel_requested ? " / 已请求取消" : ""}</span>
          <small>${escapeHtml([summary, relation].filter(Boolean).join(" · "))}</small>
          ${cancelButton || retryButton || resumeButton ? `<div class="approval-actions">${cancelButton}${retryButton}${resumeButton}</div>` : ""}
        </div>
      `;
      }
    )
    .join("");
  elements.jobList.innerHTML = jobSummary + (jobRows || `<div class="empty">暂无队列任务</div>`);
  document.querySelectorAll("[data-job-cancel-id]").forEach((button) => {
    button.addEventListener("click", () => cancelJob(button.dataset.jobCancelId));
  });
  document.querySelectorAll("[data-job-retry-id]").forEach((button) => {
    button.addEventListener("click", () => retryJob(button.dataset.jobRetryId));
  });
  document.querySelectorAll("[data-job-resume-id]").forEach((button) => {
    button.addEventListener("click", () => resumeJob(button.dataset.jobResumeId));
  });
  const sessionHtml = state.session
    ? `
      <div class="dataset-item">
        <strong>当前会话 / ${escapeHtml(state.session.workspace?.name || "-")}</strong>
        <span>${escapeHtml(state.session.user?.name || "-")} / ${escapeHtml(formatRole(state.session.user?.role || "-"))}</span>
        <small>${escapeHtml((state.session.permissions || []).join("、"))}</small>
      </div>
    `
    : `<div class="empty">暂无会话</div>`;
  const workspaceRows = state.workspaces
    .map((item) => {
      const nextStatus = item.status === "active" ? "paused" : "active";
      const nextLabel = item.status === "active" ? "暂停" : "启用";
      return `
        <div class="dataset-item ${state.selectedWorkspaceId === item.id ? "selected" : ""}">
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.id)} / ${escapeHtml(formatWorkspaceStatus(item.status))}</span>
          <small>负责人：${escapeHtml(item.owner_id || "-")} · 任务上限 ${Number(item.max_tasks || 0)} · 并发运行 ${Number(item.max_active_runs || 0)} · 每日成本 $${Number(item.max_daily_cost || 0).toFixed(2)}</small>
          ${hasPermission("admin") ? `
            <div class="approval-actions">
              <button class="mini-button" type="button" data-workspace-edit-id="${escapeHtml(item.id)}">编辑</button>
              <button class="mini-button" type="button" data-workspace-status-id="${escapeHtml(item.id)}" data-workspace-next-status="${escapeHtml(nextStatus)}">${escapeHtml(nextLabel)}</button>
            </div>
          ` : ""}
        </div>
      `;
    })
    .join("");
  elements.workspaceList.innerHTML = `${sessionHtml}${workspaceRows || `<div class="empty">暂无工作区</div>`}`;
  const usage = state.workspaceUsage?.usage || {};
  const quota = state.workspaceUsage?.quota || {};
  const remaining = state.workspaceUsage?.remaining || {};
  elements.workspaceUsageBlock.textContent = state.workspaceUsage
    ? [
        `工作区：${state.workspaceUsage.workspace_id || "-"}`,
        `状态：${formatWorkspaceStatus(state.workspaceUsage.status)}`,
        "",
        `任务：${usage.task_count || 0} / ${quota.max_tasks || 0}（剩余 ${remaining.tasks ?? 0}）`,
        `运行：${usage.active_run_count || 0} / ${quota.max_active_runs || 0}（剩余 ${remaining.active_runs ?? 0}）`,
        `近 24 小时运行：${usage.daily_run_count || 0}`,
        `近 24 小时成本：$${Number(usage.daily_cost || 0).toFixed(6)} / $${Number(quota.max_daily_cost || 0).toFixed(6)}（剩余 $${Number(remaining.daily_cost || 0).toFixed(6)}）`
      ].join("\n")
    : "暂无工作区配额使用量";
  elements.userList.innerHTML = state.users
    .map((item) => {
      const workspace = state.workspaces.find((candidate) => candidate.id === item.workspace_id);
      const nextStatus = item.status === "active" ? "disabled" : "active";
      const nextLabel = item.status === "active" ? "停用" : "启用";
      return `
        <div class="dataset-item ${state.selectedUserId === item.id ? "selected" : ""}">
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.email)} / ${escapeHtml(formatRole(item.role))} / ${escapeHtml(formatUserStatus(item.status))}</span>
          <small>${escapeHtml(workspace?.name || item.workspace_id)} · ${escapeHtml(item.id)}</small>
          ${hasPermission("admin") ? `
            <div class="approval-actions">
              <button class="mini-button" type="button" data-user-edit-id="${escapeHtml(item.id)}">编辑</button>
              <button class="mini-button" type="button" data-user-status-id="${escapeHtml(item.id)}" data-user-next-status="${escapeHtml(nextStatus)}">${escapeHtml(nextLabel)}</button>
            </div>
          ` : ""}
        </div>
      `;
    })
    .join("") || `<div class="empty">暂无成员</div>`;
  document.querySelectorAll("[data-workspace-edit-id]").forEach((button) => {
    button.addEventListener("click", () => selectWorkspace(button.dataset.workspaceEditId));
  });
  document.querySelectorAll("[data-workspace-status-id]").forEach((button) => {
    button.addEventListener("click", () =>
      updateWorkspaceStatus(button.dataset.workspaceStatusId, button.dataset.workspaceNextStatus)
    );
  });
  document.querySelectorAll("[data-user-edit-id]").forEach((button) => {
    button.addEventListener("click", () => selectUser(button.dataset.userEditId));
  });
  document.querySelectorAll("[data-user-status-id]").forEach((button) => {
    button.addEventListener("click", () =>
      updateUserStatus(button.dataset.userStatusId, button.dataset.userNextStatus)
    );
  });
  elements.repositoryList.innerHTML = state.repositories
    .map(
      (item) => {
      const health = state.repositoryHealth[item.id];
      const cachePath = health?.cache_path ? ` / 缓存 ${escapeHtml(health.cache_path)}` : "";
      const remoteReachable = health ? ` / 远端 ${health.remote_reachable ? "可达" : "不可达"}` : "";
      const nextStatus = item.status === "active" ? "inactive" : "active";
      const nextLabel = item.status === "active" ? "停用" : "启用";
      return `
        <div class="dataset-item ${state.selectedRepositoryId === item.id ? "selected" : ""}">
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.provider)} / ${escapeHtml(item.default_branch)} / ${escapeHtml(formatRepositoryStatus(item.status))}</span>
          <small>${escapeHtml(item.local_path || item.url || "-")}</small>
          ${health ? `<small>健康：${escapeHtml(formatHealthStatus(health.status))}${health.current_branch ? ` / ${escapeHtml(health.current_branch)}` : ""}${cachePath}${remoteReachable}</small>` : ""}
          <div class="approval-actions">
            <button class="mini-button" type="button" data-repo-edit-id="${escapeHtml(item.id)}">编辑</button>
            <button class="mini-button" type="button" data-repo-health-id="${escapeHtml(item.id)}">检查</button>
            ${hasPermission("integrations:write") ? `
              <button class="mini-button" type="button" data-repo-status-id="${escapeHtml(item.id)}" data-repo-next-status="${escapeHtml(nextStatus)}">${escapeHtml(nextLabel)}</button>
              <button class="mini-button" type="button" data-repo-sync-id="${escapeHtml(item.id)}">同步</button>
            ` : ""}
          </div>
        </div>
      `;
      }
    )
    .join("") || `<div class="empty">暂无仓库连接</div>`;
  document.querySelectorAll("[data-repo-edit-id]").forEach((button) => {
    button.addEventListener("click", () => selectRepository(button.dataset.repoEditId));
  });
  document.querySelectorAll("[data-repo-status-id]").forEach((button) => {
    button.addEventListener("click", () =>
      updateRepositoryStatus(button.dataset.repoStatusId, button.dataset.repoNextStatus)
    );
  });
  document.querySelectorAll("[data-repo-health-id]").forEach((button) => {
    button.addEventListener("click", () => checkRepository(button.dataset.repoHealthId));
  });
  document.querySelectorAll("[data-repo-sync-id]").forEach((button) => {
    button.addEventListener("click", () => syncRepository(button.dataset.repoSyncId));
  });
  elements.sandboxCheckBlock.textContent = state.sandboxCheck
    ? [
        `后端：${formatSandboxBackend(state.sandboxCheck.backend)}`,
        `就绪：${state.sandboxCheck.ready ? "是" : "否"}`,
        state.sandboxCheck.image ? `镜像：${state.sandboxCheck.image}` : "",
        state.sandboxCheck.namespace ? `Kubernetes 命名空间：${state.sandboxCheck.namespace}` : "",
        typeof state.sandboxCheck.image_present === "boolean"
          ? `镜像存在：${state.sandboxCheck.image_present ? "是" : "否"}`
          : "",
        state.sandboxCheck.network ? `网络：${formatSandboxNetwork(state.sandboxCheck.network)}` : "",
        state.sandboxCheck.limits
          ? `限制：内存 ${state.sandboxCheck.limits.memory} / CPU ${state.sandboxCheck.limits.cpus} / PIDs ${state.sandboxCheck.limits.pids}`
          : "",
        state.sandboxCheck.version ? `版本：${state.sandboxCheck.version}` : "",
        state.sandboxCheck.message || ""
      ].filter(Boolean).join("\n")
    : "暂无检查结果";
  elements.telemetryBlock.textContent = state.telemetry
    ? [
        `运行数：${state.telemetry.run_count}`,
        `工具调用：${state.telemetry.tool_call_count}`,
        `审计日志：${state.telemetry.audit_log_count}`,
        `队列任务：${state.telemetry.job_count}`,
        `排队任务：${state.telemetry.queued_job_count || 0}`,
        `可重试：${state.telemetry.retryable_failed_job_count || 0}`,
        `请求取消：${state.telemetry.cancel_requested_job_count || 0}`,
        `队列后端：${state.telemetry.job_queue_backend || "-"}`,
        `已完成任务：${state.telemetry.completed_job_count || 0}`,
        `失败任务：${state.telemetry.failed_job_count || 0}`,
        `研究简报：${state.telemetry.research_brief_count}`,
        `研究证据：${state.telemetry.research_evidence_count || 0}`,
        `笔记本运行：${state.telemetry.notebook_run_count || 0}`,
        `笔记本执行失败：${state.telemetry.notebook_execution_failure_count || 0}`,
        `数据集版本：${state.telemetry.dataset_snapshot_count || 0}`,
        `已入库轨迹：${state.telemetry.approved_trace_item_count || 0}`,
        `SFT 训练候选：${state.telemetry.training_bundle_sft_candidate_count || 0}`,
        `偏好训练候选：${state.telemetry.training_bundle_preference_candidate_count || 0}`,
        `模型配置：${state.telemetry.model_config_count || 0}`,
        `模型调用：${state.telemetry.model_invocation_count || 0}`,
        `模型回退：${state.telemetry.model_fallback_count || 0}`,
        `仓库连接：${state.telemetry.repository_connection_count || 0}`,
        `记忆项：${state.telemetry.memory_item_count}`
      ].join("\n")
    : "暂无指标";
  elements.readinessSummary.textContent = state.readiness
    ? `完成 ${state.readiness.passed}/${state.readiness.total} · ${Math.round((state.readiness.completion_rate || 0) * 100)}% · ${state.readiness.next_recommended_action || "已就绪"}`
    : "暂无验收状态";
  elements.readinessList.innerHTML = state.readiness?.checks
    ?.map(
      (item) => `
        <div class="dataset-item">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${item.passed ? "已完成" : "未完成"}</span>
          <small>${escapeHtml(item.evidence || item.id)}</small>
        </div>
      `
    )
    .join("") || `<div class="empty">暂无验收项</div>`;
}

function syncAuthControls() {
  const user = state.session?.user;
  if (elements.githubLoginButton) {
    elements.githubLoginButton.hidden = Boolean(user && user.id !== "user_admin");
  }
  if (elements.logoutButton) {
    elements.logoutButton.hidden = !user;
  }
}

async function startGitHubLogin() {
  try {
    const status = await api("/auth/github/status");
    if (!status.enabled) {
      throw new Error("GitHub OAuth 尚未配置，请先设置客户端 ID 和客户端密钥。");
    }
    const result = await api("/auth/github/login");
    window.location.assign(result.authorization_url);
  } catch (error) {
    showError(error);
  }
}

async function logoutCurrentUser() {
  try {
    await api("/auth/logout", { method: "POST", body: JSON.stringify({}) });
  } catch (error) {
    showError(error);
  }
  state.currentUserId = "";
  API_KEY = "";
  globalThis.localStorage?.removeItem("researchforge_api_key");
  globalThis.localStorage?.removeItem("researchforge_user_id");
  await refreshAll();
}

async function handleGitHubCallback() {
  const params = new URLSearchParams(window.location.search);
  if (!params.has("code") && !params.has("error")) return;
  if (params.get("error")) {
    state.lastRefreshError = `GitHub 登录失败：${params.get("error_description") || params.get("error")}`;
    window.history.replaceState({}, document.title, window.location.pathname);
    return;
  }
  const result = await api(`/auth/github/callback?code=${encodeURIComponent(params.get("code") || "")}&state=${encodeURIComponent(params.get("state") || "")}`);
  if (result.user?.id) {
    state.currentUserId = result.user.id;
    globalThis.localStorage?.setItem("researchforge_user_id", state.currentUserId);
  }
  window.history.replaceState({}, document.title, window.location.pathname);
}
function syncSessionUserSelect() {
  if (!elements.sessionUserSelect) return;
  elements.sessionUserSelect.disabled = !state.developmentAuth;
  const options = state.users
    .map((user) => (
      `<option value="${escapeHtml(user.id)}">${escapeHtml(user.name)} / ${escapeHtml(formatRole(user.role))} / ${escapeHtml(formatUserStatus(user.status))}</option>`
    ))
    .join("");
  elements.sessionUserSelect.innerHTML = options || `<option value="user_admin">管理员</option>`;
  if (state.currentUserId && state.users.some((user) => user.id === state.currentUserId)) {
    elements.sessionUserSelect.value = state.currentUserId;
  }
}

function statusBadge(status) {
  return `<span class="status ${escapeHtml(status)}">${escapeHtml(formatRunStatus(status))}</span>`;
}

function setButtonBusy(button, busy, label) {
  button.disabled = busy;
  button.textContent = label;
}

function showError(error) {
  console.error(error);
  elements.apiStatus.textContent = "接口错误";
  elements.apiDot.className = "dot error";
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parsePositiveFloat(value, fallback) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function formatJson(value) {
  return JSON.stringify(value || {}, null, 2);
}

function formatTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "");
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
}

function formatLogArtifact(content) {
  try {
    const parsed = JSON.parse(content);
    const parts = [];
    if (typeof parsed.exit_code !== "undefined") parts.push(`退出码：${parsed.exit_code}`);
    if (typeof parsed.tests_passed !== "undefined" || typeof parsed.tests_total !== "undefined") {
      parts.push(`测试：${parsed.tests_passed || 0}/${parsed.tests_total || 0}`);
    }
    if (parsed.stdout) parts.push(`输出：\n${parsed.stdout}`);
    if (parsed.stderr) parts.push(`错误：\n${parsed.stderr}`);
    return parts.join("\n\n") || formatJson(parsed);
  } catch {
    return content || "";
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatSandboxBackend(value) {
  return {
    local: "本地临时目录",
    docker: "Docker 容器",
    kubernetes: "Kubernetes Pod",
    k8s: "Kubernetes Pod"
  }[value] || value || "未配置";
}

function formatSandboxNetwork(value) {
  return {
    none: "已禁用",
    bridge: "Docker 桥接网络",
    "kubernetes-network-policy-deny-egress": "Kubernetes 策略：拒绝出站",
    "kubernetes-network-policy-allow-all": "Kubernetes 策略：允许出站"
  }[value] || value;
}

function formatRunStatus(value) {
  const map = {
    idle: "空闲",
    created: "已创建",
    queued: "已排队",
    running: "运行中",
    paused: "已暂停",
    completed: "已完成",
    success: "成功",
    failed: "失败",
    blocked: "已阻止",
    cancelled: "已取消",
    policy_blocked: "策略阻止",
    timeout: "超时",
    skipped: "已跳过"
  };
  return map[value] || value;
}

function formatTaskType(value) {
  const map = {
    coding: "代码任务",
    research: "研究任务"
  };
  return map[value] || value;
}

function formatStrategyTaskType(value) {
  const map = {
    coding: "代码",
    research: "研究"
  };
  return map[value] || formatTaskType(value);
}

function formatPhase(value) {
  const map = {
    created: "已创建",
    planning: "规划",
    precheck: "预检查",
    run_tests: "运行测试",
    analyze_failure: "分析失败",
    edit_code: "编辑代码",
    rerun_tests: "重新测试",
    evaluate: "评审",
    report: "报告"
  };
  return map[value] || value;
}

function formatAction(value) {
  const map = {
    "test.run": "测试执行",
    "file.write_patch": "应用补丁",
    "file.read": "读取文件",
    "git.diff": "查看差异",
    "report.write": "写入报告",
    "shell.run": "执行命令"
  };
  return map[value] || value;
}

function formatToolDescription(name, fallback) {
  const map = {
    "test.run": "运行任务测试命令并记录结构化结果。",
    "file.write_patch": "在任务仓库内应用统一 diff 补丁。",
    "file.read": "读取任务仓库内的文本文件。",
    "git.diff": "查看任务仓库当前工作区差异。",
    "report.write": "写入最终运行报告产物。",
    "shell.run": "在沙箱工作区执行白名单命令。"
  };
  return map[name] || fallback || name;
}

function formatStrategyName(value) {
  const map = {
    repair_baseline_v1: "基础修复策略 V1",
    repair_with_trace_v2: "强化轨迹策略 V2",
    repair_with_critic_v3: "评审增强策略 V3",
    "Repair Baseline V1": "基础修复策略 V1",
    "Repair With Trace V2": "强化轨迹策略 V2",
    "Repair With Critic V3": "评审增强策略 V3"
  };
  return map[value] || value;
}

function formatPolicyName(value) {
  const map = {
    policy_default_v1: "默认安全策略 V1",
    policy_no_patch: "禁止补丁策略",
    "Default Policy V1": "默认安全策略 V1",
    "No Patch Policy": "禁止补丁策略"
  };
  return map[value] || value;
}

function formatPolicyStatus(value) {
  const map = {
    active: "已启用",
    inactive: "已停用",
    draft: "草稿"
  };
  return map[value] || value;
}

function formatPolicySummary(policy) {
  if (!policy || typeof policy !== "object") return "暂无策略摘要";
  const parts = [];
  if (Array.isArray(policy.allowed_tools)) parts.push(`工具 ${policy.allowed_tools.length}`);
  if (Array.isArray(policy.allowed_commands)) parts.push(`命令 ${policy.allowed_commands.length}`);
  if (Array.isArray(policy.blocked_commands)) parts.push(`阻止 ${policy.blocked_commands.length}`);
  if (typeof policy.max_steps === "number") parts.push(`步骤 ${policy.max_steps}`);
  if (typeof policy.max_runtime_seconds === "number") parts.push(`时长 ${policy.max_runtime_seconds}`);
  if (typeof policy.max_patch_files === "number") parts.push(`文件 ${policy.max_patch_files}`);
  if (typeof policy.max_changed_lines === "number") parts.push(`行数 ${policy.max_changed_lines}`);
  if (typeof policy.network_enabled === "boolean") parts.push(policy.network_enabled ? "允许网络" : "禁用网络");
  return parts.length ? parts.join(" / ") : "暂无策略摘要";
}

function formatTaskTitle(value) {
  const map = {
    "Fix date parser edge case": "修复日期解析边界问题",
    "Fix date parser": "修复日期解析",
    "Fix price calculator rounding": "修复价格计算四舍五入",
    "Repair pagination off-by-one": "修复分页边界错误",
    "Force failure recovery sample": "强制失败恢复样例",
    "Normalize user email addresses": "规范化用户邮箱地址",
    "Repair slug generator punctuation": "修复短链接标识的标点处理",
    "Fix inventory reorder threshold": "修复库存补货阈值",
    "Repair CSV row counter": "修复 CSV 行计数",
    "Fix timezone offset formatter": "修复时区偏移格式化",
    "Repair retry backoff calculation": "修复重试退避计算"
  };
  return map[value] || value;
}

function formatStepGoal(value) {
  const map = {
    "Create repair plan": "创建修复计划",
    "Run baseline tests": "运行基线测试",
    "Analyze failing tests": "分析失败测试",
    "Read relevant implementation file": "读取相关实现文件",
    "Apply minimal patch": "应用最小补丁",
    "Inspect diff": "检查代码差异",
    "Run validation tests": "运行验证测试",
    "Critic review": "评审检查",
    "Write repair report": "生成修复报告"
  };
  return map[value] || value;
}

function formatObservation(value) {
  const map = {
    "Plan created with baseline test, analysis, patch, validation, and report steps.": "已完成计划，包含基线测试、失败分析、补丁、验证和报告步骤。",
    "Baseline tests reproduced expected failures.": "基线测试已复现预期失败。",
    "Relevant implementation file captured as trace evidence.": "已读取相关实现文件并作为轨迹证据保存。",
    "Patch applied within policy limits.": "补丁已在策略限制内应用。",
    "Diff is small and implementation-only.": "代码差异较小，且只修改实现。",
    "Validation tests passed after patch.": "补丁后验证测试通过。",
    "Critic accepted the repair because tests pass and diff is constrained.": "评审通过：测试通过，代码差异受控。",
    "Repair report generated.": "修复报告已生成。",
    "Failures point to an off-by-one page boundary in pagination logic.": "失败指向分页逻辑中的边界计算错误。",
    "Failures point to rounding behavior in price calculation.": "失败指向价格计算中的四舍五入行为。",
    "Failures point to date parser edge cases: empty strings and invalid formats.": "失败指向日期解析边界：空字符串和非法格式。"
  };
  return map[value] || formatFailureReason(value);
}

function formatFailureReason(value) {
  const map = {
    "-": "-",
    POLICY_BLOCKED: "策略阻止",
    COMMAND_BLOCKED: "命令被阻止",
    COMMAND_NOT_ALLOWED: "命令不在白名单",
    TOOL_NOT_ALLOWED: "工具不被允许",
    TESTS_FAILED: "测试失败",
    TESTS_STILL_FAILING: "修复后测试仍失败",
    PATCH_APPLY_FAILED: "补丁应用失败",
    PATCH_FILE_LIMIT: "补丁文件数超限",
    PATCH_LINE_LIMIT: "补丁行数超限",
    PATH_OUTSIDE_REPO: "路径超出仓库",
    REPO_NOT_FOUND: "未找到仓库",
    UNKNOWN_ERROR: "未知错误",
    TASK_NOT_FOUND: "未找到任务",
    RUN_FAILED: "运行失败",
    CANCELLED_BY_USER: "用户取消",
    BUDGET_EXCEEDED: "预算超限",
    PRECHECK_FAILED: "预检查失败",
    VALIDATION_FAILED: "验证失败",
    CRITIC_REJECTED: "评审拒绝",
    EXTENSION_DISABLED: "扩展已停用",
    HOOK_EVENTS_INVALID: "Hook 事件配置无效",
    HOOK_TARGET_MISSING: "Hook 目标缺失",
    MCP_ENDPOINT_MISSING: "MCP 端点缺失",
    SKILL_BINDING_MISSING: "Skill 绑定缺失",
    API_KEY_MISSING: "缺少模型密钥",
    MOCK_PROVIDER: "本地 Mock 提供方",
    MODEL_RESPONSE_INVALID: "模型响应格式无效",
    MODEL_RESPONSE_MISSING_CHOICE: "模型响应缺少候选内容",
    URLError: "模型网络调用失败",
    HTTPError: "模型 HTTP 调用失败",
    TESTS_TIMEOUT: "测试超时",
    COMMAND_TIMEOUT: "命令超时"
  };
  return map[value] || value;
}

function formatComparisonStatus(value) {
  const map = {
    baseline: "基线",
    compared: "对比项"
  };
  return map[value] || value;
}

function formatStrategyRuntimeSummary(config) {
  if (!config || typeof config !== "object") return "暂无运行时配置";
  const parts = [];
  if (typeof config.precheck === "boolean") parts.push(`预检查 ${config.precheck ? "开" : "关"}`);
  if (typeof config.retry === "boolean") parts.push(`重试 ${config.retry ? "开" : "关"}`);
  if (typeof config.critic === "boolean") parts.push(`评审 ${config.critic ? "开" : "关"}`);
  if (typeof config.model_gateway === "boolean") parts.push(`模型网关 ${config.model_gateway ? "开" : "关"}`);
  if (typeof config.max_validation_retries === "number") parts.push(`验证重试 ${config.max_validation_retries}`);
  if (typeof config.max_patch_files === "number") parts.push(`文件 ${config.max_patch_files}`);
  if (typeof config.max_changed_lines === "number") parts.push(`行数 ${config.max_changed_lines}`);
  if (typeof config.allow_test_edits === "boolean") parts.push(config.allow_test_edits ? "允许改测试" : "禁止改测试");
  if (typeof config.require_diff === "boolean") parts.push(config.require_diff ? "必须有差异" : "无需差异");
  if (typeof config.require_all_tests === "boolean") parts.push(config.require_all_tests ? "全量测试" : "部分测试");
  return parts.length ? parts.join(" / ") : "暂无运行时配置";
}

function formatReleaseGateStatus(value) {
  const map = {
    active: "已发布",
    candidate: "候选发布",
    canary: "灰度中",
    blocked: "已阻断"
  };
  return map[value] || value || "未检查";
}

function formatReleaseStage(value) {
  const map = {
    active: "正式发布",
    candidate: "候选",
    canary: "灰度"
  };
  return map[value] || value || "未指定";
}

function formatTraceType(value) {
  const map = {
    SUCCESS_TRACE: "成功轨迹",
    FAILURE_TRACE: "失败轨迹",
    RECOVERY_TRACE: "恢复轨迹"
  };
  return map[value] || value;
}

function formatQualityLabel(value) {
  const map = {
    good: "优",
    average: "中",
    bad: "差"
  };
  return map[value] || value;
}

function formatUseCase(value) {
  const map = {
    sft_candidate: "SFT 候选",
    failure_case: "失败样本",
    preference_candidate: "偏好候选"
  };
  return map[value] || value;
}

function formatDatasetStatus(value) {
  const map = {
    candidate: "候选",
    reviewed: "已审核",
    approved: "已入库",
    rejected: "已拒绝",
    ready: "可用",
    archived: "已归档"
  };
  return map[value] || value;
}

function formatCitationStatus(value) {
  return {
    pending_review: "待审核",
    approved: "已通过",
    rejected: "已退回"
  }[value] || value || "待审核";
}

function formatEvidenceSource(value) {
  return {
    manual: "手工录入",
    pdf: "PDF 文件",
    note: "研究笔记",
    url: "外部链接",
    local: "本地模板",
    local_fallback: "本地回退",
    crossref: "Crossref"
  }[value] || value || "未知来源";
}

function formatEvidenceMetadata(metadata) {
  const parts = [];
  if (metadata.file_name) parts.push(`文件 ${metadata.file_name}`);
  if (metadata.file_size_bytes !== undefined) parts.push(formatBytes(metadata.file_size_bytes));
  if (metadata.pdf_page_count !== undefined) parts.push(`${metadata.pdf_page_count} 页`);
  if (metadata.job_id) parts.push(`导入任务 ${metadata.job_id}`);
  return parts.join(" / ");
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatFailureDistribution(value) {
  if (!value || Object.keys(value).length === 0) return "无失败样本";
  return Object.entries(value)
    .map(([reason, count]) => `${formatFailureReason(reason)} ${count}`)
    .join("、");
}

function formatGateChecks(checks) {
  if (!checks) return "暂无检查项";
  const failed = Object.entries(checks)
    .filter(([, check]) => check && check.passed === false)
    .map(([name]) => formatGateCheckName(name));
  return failed.length ? `未通过：${failed.join("、")}` : "全部检查通过";
}

function formatCriteriaSummary(metrics) {
  if (!metrics || typeof metrics !== "object") return "";
  const failed = Array.isArray(metrics.expected_checks)
    ? metrics.expected_checks.filter((item) => item && item.passed === false)
    : [];
  if (failed.length) {
    return `未通过：${failed.map((item) => item.label || item.criterion).filter(Boolean).join("、")}`;
  }
  const total = Number(metrics.criteria_total || 0);
  const met = Number(metrics.criteria_met || 0);
  if (total > 0) return `期望检查 ${met}/${total}`;
  return "";
}

function formatGateCheckName(value) {
  const map = {
    success_rate: "成功率",
    avg_score: "平均分",
    avg_trace_completeness: "轨迹完整度",
    expected_alignment: "期望对齐率",
    policy_violation_count: "策略违规",
    regression_count: "回归数",
    cost_growth_ratio: "成本增长"
  };
  return map[value] || value;
}

function formatEvaluationBaseline(summary) {
  if (!summary) return "无基线";
  const baseline = summary.baseline_evaluation_id || "无基线";
  const delta = Number(summary.score_delta_vs_baseline || 0).toFixed(1);
  const regressions = Number(summary.regression_count || 0);
  return `基线 ${baseline} / 分差 ${delta} / 回归 ${regressions}`;
}

function formatCriticReview(artifact) {
  let data = null;
  try {
    data = JSON.parse(artifact.content || "{}");
  } catch {
    return artifact.content || "暂无评审结果";
  }
  const checks = data.checks || artifact.metadata?.checks || {};
  const policy = data.policy || artifact.metadata?.policy || {};
  const checkLines = Object.entries(checks).map(
    ([name, passed]) => `${formatCriticCheckName(name)}：${passed ? "通过" : "未通过"}`
  );
  const policyLine = Object.keys(policy).length
    ? `策略阈值：文件<=${policy.max_patch_files ?? "-"} / 行数<=${policy.max_changed_lines ?? "-"} / ${policy.allow_test_edits ? "允许改测试" : "禁止改测试"}`
    : "";
  return [
    `结论：${data.accepted ? "通过" : "拒绝"}`,
    `分数：${Number(data.score || artifact.metadata?.score || 0).toFixed(2)}`,
    policyLine,
    data.observation ? `说明：${formatObservation(data.observation)}` : "",
    checkLines.length ? `检查项：\n${checkLines.join("\n")}` : ""
  ].filter(Boolean).join("\n");
}

function formatModelAssistArtifact(artifact) {
  let data = null;
  try {
    data = JSON.parse(artifact.content || "{}");
  } catch {
    return artifact.content || "暂无模型辅助记录";
  }
  const usage = data.usage || {};
  return [
    `阶段：${artifact.metadata?.phase || artifact.name.replace("model-assist-", "").replace(".json", "")}`,
    `模型：${data.model_name || artifact.metadata?.model_name || "-"}`,
    `提供方：${formatModelProvider(data.provider || artifact.metadata?.provider || "-")}`,
    `回退：${data.fallback_used ? "是" : "否"}`,
    `Token：${usage.total_tokens || artifact.metadata?.total_tokens || 0}`,
    `成本：$${Number(data.estimated_cost || artifact.metadata?.estimated_cost || 0).toFixed(6)}`,
    "",
    formatObservation(data.output_text || "")
  ].join("\n");
}

function formatCriticCheckName(value) {
  const map = {
    diff_present: "存在可评审差异",
    tests_passed: "验证测试通过",
    no_test_changes: "未修改测试",
    patch_file_limit: "修改文件数受控",
    changed_line_limit: "修改行数受控"
  };
  return map[value] || value;
}

function formatModelProvider(value) {
  const map = {
    mock: "本地模拟",
    openai: "OpenAI",
    openai_compatible: "OpenAI 兼容",
    "openai-compatible": "OpenAI 兼容"
  };
  return map[value] || value;
}

function formatModelSource(config) {
  if (!config || typeof config !== "object") return "默认种子";
  return config.source === "environment" ? "环境注入" : "默认种子";
}

function formatModelHealthReason(value) {
  const map = {
    MOCK_PROVIDER: "本地模拟",
    CONFIGURED: "已配置",
    API_KEY_MISSING: "缺少密钥",
    BASE_URL_MISSING: "缺少接口地址",
    UNSUPPORTED_PROVIDER: "不支持的提供方"
  };
  return map[value] || value || "未知";
}

function formatModelConfigSummary(config) {
  if (!config || typeof config !== "object") return "暂无额外配置";
  const parts = [];
  if (config.base_url) parts.push(`接口地址 ${config.base_url}`);
  if (config.api_key_env) parts.push(`密钥环境 ${config.api_key_env}`);
  if (Array.isArray(config.strategy_ids) && config.strategy_ids.length) {
    parts.push(`策略 ${config.strategy_ids.join("、")}`);
  }
  if (typeof config.max_retries === "number") parts.push(`重试 ${config.max_retries} 次`);
  return parts.length ? parts.join(" / ") : "暂无额外配置";
}

function formatModelRole(value) {
  const map = {
    coding: "代码",
    research: "研究",
    evaluation: "评测",
    critic: "评审"
  };
  return map[value] || value;
}

function formatModelStatus(value) {
  const map = {
    active: "已启用",
    inactive: "已停用",
    draft: "草稿"
  };
  return map[value] || formatRunStatus(value);
}

function formatHealthStatus(value) {
  const map = {
    healthy: "健康",
    configured: "已配置",
    warning: "需检查",
    missing: "未找到"
  };
  return map[value] || value;
}

function formatRepositoryStatus(value) {
  const map = {
    active: "已启用",
    inactive: "已停用",
    error: "异常"
  };
  return map[value] || value;
}

function formatMemoryType(value) {
  const map = {
    failure: "失败记忆",
    project: "项目记忆",
    strategy: "策略记忆",
    success: "成功记忆"
  };
  return map[value] || value;
}

function formatMemoryScope(value) {
  const map = {
    project: "项目范围",
    failure: "失败范围",
    strategy: "策略范围",
    success: "成功范围"
  };
  return map[value] || value;
}

function formatApprovalStatus(value) {
  const map = {
    pending: "待审批",
    approved: "已通过",
    denied: "已拒绝"
  };
  return map[value] || value;
}

function formatExtensionType(value) {
  const map = {
    mcp_tool: "MCP 工具",
    skill: "技能",
    hook: "钩子"
  };
  return map[value] || value;
}

function formatExtensionAction(value) {
  const map = {
    inspect: "检查",
    status: "状态",
    run: "运行"
  };
  return map[value] || value;
}

function formatExtensionStatus(value) {
  const map = {
    enabled: "已启用",
    disabled: "已停用",
    error: "异常"
  };
  return map[value] || value;
}

function formatJobKind(value) {
  const map = {
    agent_run: "智能体运行",
    agent_run_resume: "审批后恢复运行",
    evaluation: "评测",
    strategy_comparison: "策略对比",
    coding_acceptance: "代码验收",
    research_acceptance: "研究验收",
    release_gate: "发布门禁",
    notebook_run: "研究笔记本",
    strategy_lifecycle: "策略生命周期",
    dataset_export: "数据集导出",
    research_evidence_import: "研究证据导入",
    extension_status: "扩展状态更新",
    extension_health: "扩展健康检查",
    extension_invoke: "扩展试调",
    hook_dispatch: "钩子派发",
    model_invocation: "模型调用",
    research: "研究",
    repository_sync: "仓库同步"
  };
  return map[value] || value;
}

function formatJobRelation(item) {
  const parts = [];
  if (item.retry_of_job_id) parts.push(`重试自 ${item.retry_of_job_id}`);
  if (item.parent_job_id) parts.push(`父任务 ${item.parent_job_id}`);
  return parts.join(" / ");
}

function canCancelJob(item) {
  const agentRun = ["agent_run", "agent_run_resume"].includes(item.kind);
  const queuedBatch = [
    "evaluation",
    "repository_sync",
    "coding_acceptance",
    "strategy_comparison",
    "research_acceptance",
    "notebook_run"
  ].includes(item.kind);
  return hasPermission("runs:write")
    && ((agentRun && ["queued", "running"].includes(item.status))
      || (queuedBatch && item.status === "queued"));
}

function canRetryJob(item) {
  return hasPermission("runs:write")
    && [
      "agent_run",
      "agent_run_resume",
      "evaluation",
      "repository_sync",
      "coding_acceptance",
      "strategy_comparison",
      "research_acceptance",
      "notebook_run"
    ].includes(item.kind)
    && ["failed", "cancelled"].includes(item.status);
}

function canResumeJob(item) {
  return hasPermission("runs:write")
    && ["agent_run", "agent_run_resume"].includes(item.kind)
    && item.status === "paused";
}

function formatHookEvent(value) {
  const map = {
    "run.completed": "运行完成",
    "run.failed": "运行失败",
    "run.cancelled": "运行已取消",
    "run.paused": "运行已暂停"
  };
  return map[value] || value;
}

function formatJobResult(result) {
  if (!result || typeof result !== "object" || !Object.keys(result).length) {
    return "";
  }
  if (result.paper_id) {
    const title = result.title || result.paper_id;
    const source = result.source ? `来源 ${formatEvidenceSource(result.source)}` : "";
    const snippets = `片段 ${result.snippet_count || 0}`;
    return [title, source, snippets].filter(Boolean).join(" / ");
  }
  if (result.model_name) {
    const parts = [`模型 ${result.model_name}`];
    if (result.fallback_used !== undefined) {
      parts.push(result.fallback_used ? "已回退" : "直连");
    }
    if (result.attempts !== undefined) {
      parts.push(`尝试 ${result.attempts} 次`);
    }
    if (result.fallback_reason) {
      parts.push(formatFailureReason(result.fallback_reason));
    }
    if (result.estimated_cost !== undefined) {
      parts.push(`成本 ${Number(result.estimated_cost).toFixed(4)}`);
    }
    return parts.join(" / ");
  }
  if (result.passed_count !== undefined && result.task_count !== undefined) {
    const report = result.report_path ? " · 含报告" : "";
    return `通过 ${result.passed_count}/${result.task_count} · 平均分 ${result.avg_score ?? "-"}${report}`;
  }
  if (result.release_gate_status) {
    const stage = result.release_stage ? ` / ${formatReleaseStage(result.release_stage)}` : "";
    const canary = result.canary_percentage ? ` ${result.canary_percentage}%` : "";
    const failed = Array.isArray(result.failed_checks) && result.failed_checks.length
      ? `未通过：${result.failed_checks.map(formatGateCheckName).join("、")}`
      : "全部检查通过";
    return `${formatReleaseGateStatus(result.release_gate_status)}${stage}${canary} · ${failed}`;
  }
  if (result.extension_id) {
    const action = result.action ? formatExtensionAction(result.action) : "试调";
    const output = result.output && typeof result.output === "object" ? result.output : {};
    const extras = [];
    if (output.event_type) extras.push(`事件 ${formatHookEvent(output.event_type)}`);
    if (output.git_version) extras.push(`Git ${output.git_version}`);
    if (output.status_summary) extras.push(output.status_summary);
    if (output.exit_code !== undefined && output.exit_code !== null) extras.push(`退出码 ${output.exit_code}`);
    if (output.message) extras.push(output.message);
    if (output.record_id) extras.push(`记录 ${output.record_id}`);
    if (output.reason) extras.push(formatFailureReason(output.reason));
    const suffix = extras.length ? ` · ${extras.join(" / ")}` : "";
    return `${formatExtensionType(result.type)} · ${action} · ${formatExtensionStatus(result.status)}${suffix}`;
  }
  if (result.notebook_run_id || result.execution_status) {
    return `执行 ${formatJobStatus(result.execution_status || "-")} · 复现 ${result.reproducibility_score ?? "-"}`;
  }
  if (result.success_rate !== undefined || result.avg_score !== undefined) {
    return `成功率 ${formatPercent(result.success_rate || 0)} · 平均分 ${result.avg_score ?? "-"}`;
  }
  if (result.winner_strategy_id) {
    return `胜出策略 ${formatStrategyName(result.winner_strategy_id)} · 策略数 ${result.strategy_count || 0}`;
  }
  if (result.strategy_id && result.action) {
    return `${formatAuditAction(result.action)} · ${formatStrategyStatus(result.previous_status)} -> ${formatStrategyStatus(result.status)}`;
  }
  if (result.record_id && result.event_type) {
    return `${result.event_type} · 送达 ${result.delivered || 0} / 跳过 ${result.skipped || 0}`;
  }
  if (result.schema_version === "training_bundle_v1") {
    return `SFT ${result.sft_record_count || 0} / 偏好 ${result.preference_record_count || 0} / 失败 ${result.failure_record_count || 0}`;
  }
  if (result.agent_run_id) {
    return `运行 ${result.status || "-"} · 工具 ${result.tool_call_count || 0} 次`;
  }
  if (result.health_status) {
    return `仓库 ${result.health_status} · ${result.current_branch || "-"}`;
  }
  return Object.entries(result)
    .slice(0, 2)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" / ");
}

function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function formatJobStatus(value) {
  const map = {
    queued: "排队中",
    running: "运行中",
    paused: "已暂停",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消"
  };
  return map[value] || value;
}

function formatQueueBackend(value) {
  const map = {
    local_background: "本地后台",
    redis: "Redis 队列"
  };
  return map[value] || value || "本地后台";
}

function formatArtifactStoreBackend(value) {
  const map = {
    filesystem: "文件产物存储",
    s3: "S3 / MinIO 对象存储",
    inline: "内联产物存储"
  };
  return map[value] || value || "文件产物存储";
}

function formatStorageSummary(storage) {
  if (!storage) return "内存模式";
  const snapshot = storage.snapshot_mode === "postgres" ? "PostgreSQL 快照" : storage.persistent ? "JSON 持久化" : "内存模式";
  const artifactStore = storage.artifact_store_backend
    ? ` / ${formatArtifactStoreBackend(storage.artifact_store_backend)}${storage.artifact_store_bucket ? `(${storage.artifact_store_bucket})` : ""}`
    : "";
  const migrations = storage.postgres_migrations?.available
    ? ` / ${storage.postgres_migrations.recorded ?? storage.postgres_migrations.applied ?? 0}/${storage.postgres_migrations.available} 迁移`
    : "";
  return `${snapshot}${artifactStore}${migrations}`;
}

function formatRole(value) {
  const map = {
    admin: "管理员",
    operator: "操作员",
    viewer: "只读"
  };
  return map[value] || value;
}

function formatWorkspaceStatus(value) {
  const map = {
    active: "已启用",
    paused: "已暂停",
    archived: "已归档"
  };
  return map[value] || value;
}

function formatUserStatus(value) {
  const map = {
    active: "已启用",
    disabled: "已停用",
    invited: "已邀请"
  };
  return map[value] || value;
}

function formatStrategyStatus(value) {
  const map = {
    draft: "草稿",
    candidate: "候选",
    active: "已启用",
    rolled_back: "已回滚",
    archived: "已归档"
  };
  return map[value] || formatRunStatus(value);
}

function formatAuditAction(value) {
  const map = {
    "run.create": "创建运行",
    "run.resume": "恢复运行",
    "run.finish": "结束运行",
    "run.cancel_request": "请求取消",
    "tool.policy_decision": "工具策略决策",
    "tool.policy_preview": "工具策略预检",
    "tool.call": "工具调用",
    "job.create": "创建后台任务",
    "job.update": "更新后台任务",
    "approval.request": "请求审批",
    "approval.decide": "审批决定",
    "approval.consume": "消费审批",
    "extension.upsert": "保存扩展",
    "extension.status": "更新扩展状态",
    "extension.health": "检查扩展健康",
    "extension.invoke": "扩展试调",
    "hook.dispatch": "派发 Hook",
    "model.invoke": "模型调用",
    "evaluation.run": "执行评测",
    "evaluation.compare": "策略对比",
    "benchmark.acceptance": "批量验收",
    "notebook.run": "运行研究笔记本",
    "release_gate.evaluate": "发布门禁评估",
    "release_gate.stage": "记录发布阶段",
    "release_gate.promote": "自动发布策略",
    "release_gate.rollback": "发布阻断回滚",
    "strategy.candidate": "标记候选策略",
    "strategy.promote": "启用策略",
    "strategy.rollback": "回滚策略",
    "research.evidence.import": "导入研究证据",
    "research.evidence.attach": "挂载研究证据",
    "research.citation.review": "审核研究引用",
    "memory.create": "创建记忆",
    "memory.update": "更新记忆",
    "preference_pair.update": "审核偏好对"
  };
  return map[value] || value;
}

function formatAuditDecision(value) {
  const map = {
    allow: "允许",
    deny: "拒绝",
    requested: "已请求",
    fallback: "已回退",
    completed: "已完成",
    queued: "已排队",
    running: "运行中",
    failed: "失败",
    active: "已发布",
    candidate: "候选发布",
    canary: "灰度中",
    blocked: "已阻断"
  };
  return map[value] || formatRunStatus(value);
}

function formatAuditResource(value) {
  const map = {
    run: "运行",
    tool: "工具",
    tool_call: "工具调用",
    evaluation_run: "评测运行",
    model: "模型",
    benchmark: "基准评测",
    research_benchmark: "研究基准",
    research_paper: "研究证据",
    research_brief: "研究简报",
    notebook_run: "研究笔记本",
    job: "后台任务",
    approval_request: "审批请求",
    extension: "扩展",
    hook: "Hook",
    memory_item: "记忆项"
  };
  return map[value] || value;
}

function formatAuditDetail(detail) {
  if (!detail || typeof detail !== "object") return "";
  const parts = [];
  if (detail.task_id) parts.push(`任务 ${detail.task_id}`);
  if (detail.policy_version_id) parts.push(`策略 ${detail.policy_version_id}`);
  if (detail.tool_name) parts.push(`工具 ${formatAction(detail.tool_name)}`);
  if (detail.phase) parts.push(`阶段 ${formatPhase(detail.phase)}`);
  if (detail.consumed_by_run_id) parts.push(`恢复运行 ${detail.consumed_by_run_id}`);
  if (detail.source_type) parts.push(`来源 ${formatEvidenceSource(detail.source_type)}`);
  if (typeof detail.snippet_count === "number") parts.push(`${detail.snippet_count} 条证据片段`);
  if (detail.source_metadata) {
    const metadata = formatEvidenceMetadata(detail.source_metadata);
    if (metadata) parts.push(metadata);
  }
  if (detail.reason) parts.push(formatFailureReason(detail.reason));
  if (detail.error_summary) parts.push(formatFailureReason(detail.error_summary));
  if (typeof detail.duration_ms === "number") parts.push(`${detail.duration_ms}毫秒`);
  if (typeof detail.tool_call_count === "number") parts.push(`${detail.tool_call_count} 次工具调用`);
  return parts.join(" · ");
}

function formatReport(value) {
  return String(value ?? "")
    .replaceAll("# Repair Report", "# 修复报告")
    .replaceAll("# Failure Report", "# 失败报告")
    .replaceAll("# Run Cancelled", "# 运行已取消")
    .replaceAll("## Task", "## 任务")
    .replaceAll("## Root Cause", "## 根因")
    .replaceAll("## Change", "## 修改内容")
    .replaceAll("## Validation", "## 验证")
    .replaceAll("## Risk", "## 风险")
    .replaceAll("## Reason", "## 原因")
    .replaceAll("## Summary", "## 摘要")
    .replaceAll("The implementation missed an edge case covered by the failing tests.", "实现遗漏了失败测试覆盖的边界情况。")
    .replaceAll("Applied a minimal implementation patch within policy limits.", "已在策略限制内应用最小实现补丁。")
    .replaceAll("Low. The change is implementation-only and the validation evidence passed.", "低。修改仅限实现代码，验证证据已通过。")
    .replaceAll("Validation tests still failed after the generated patch.", "生成补丁后验证测试仍失败。")
    .replaceAll("Patch was blocked by the active policy before validation.", "补丁在验证前被当前策略阻止。")
    .replaceAll("The run was cancelled by the user before the workflow completed.", "该运行在工作流完成前被用户取消。")
    .replaceAll("Final:", "最终：")
    .replaceAll("passed.", "通过。")
    .replaceAll("Fix date parser edge case", "修复日期解析边界问题")
    .replaceAll("Fix price calculator rounding", "修复价格计算四舍五入")
    .replaceAll("Repair pagination off-by-one", "修复分页边界错误")
    .replaceAll("Force failure recovery sample", "强制失败恢复样例")
    .replaceAll("Normalize user email addresses", "规范化用户邮箱地址")
    .replaceAll("Repair slug generator punctuation", "修复短链接标识的标点处理")
    .replaceAll("Fix inventory reorder threshold", "修复库存补货阈值")
    .replaceAll("Repair CSV row counter", "修复 CSV 行计数")
    .replaceAll("Fix timezone offset formatter", "修复时区偏移格式化")
    .replaceAll("Repair retry backoff calculation", "修复重试退避计算");
}

elements.taskForm.addEventListener("submit", createTask);
elements.runButton.addEventListener("click", runSelectedTask);
elements.pauseButton?.addEventListener("click", pauseSelectedRun);
elements.resumeButton?.addEventListener("click", resumePausedRun);
elements.cancelButton.addEventListener("click", cancelSelectedRun);
elements.githubLoginButton?.addEventListener("click", startGitHubLogin);
elements.logoutButton?.addEventListener("click", logoutCurrentUser);
elements.seedButton.addEventListener("click", seedGoldenTasks);
elements.benchmarkButton.addEventListener("click", runBenchmark);
elements.goldenAcceptanceButton.addEventListener("click", runGoldenAcceptance);
elements.compareButton.addEventListener("click", compareStrategies);
elements.gateButton.addEventListener("click", runReleaseGate);
elements.labelButton.addEventListener("click", labelSelectedRun);
elements.newAnnotationButton?.addEventListener("click", newAnnotationForCurrentRun);
elements.preferenceButton.addEventListener("click", createPreferencePair);
elements.snapshotButton.addEventListener("click", createDatasetSnapshot);
elements.researchButton.addEventListener("click", createResearchBrief);
elements.notebookButton.addEventListener("click", runNotebookForLatestBrief);
elements.researchAcceptanceButton.addEventListener("click", runResearchBenchmarkAcceptance);
elements.importEvidenceButton.addEventListener("click", importResearchEvidence);
elements.attachEvidenceButton.addEventListener("click", attachSelectedResearchEvidence);
elements.modelInvokeButton.addEventListener("click", invokeModel);
elements.saveModelButton?.addEventListener("click", saveModelConfig);
elements.resetModelButton?.addEventListener("click", resetModelConfigForm);
elements.saveStrategyButton?.addEventListener("click", saveStrategyConfig);
elements.resetStrategyButton?.addEventListener("click", resetStrategyConfigForm);
elements.savePolicyButton?.addEventListener("click", savePolicyConfig);
elements.resetPolicyButton?.addEventListener("click", resetPolicyConfigForm);
elements.policyPreviewButton?.addEventListener("click", previewToolPolicy);
elements.saveMemoryButton?.addEventListener("click", saveMemoryItem);
elements.resetMemoryButton?.addEventListener("click", resetMemoryForm);
elements.saveWorkspaceButton?.addEventListener("click", saveWorkspace);
elements.resetWorkspaceButton?.addEventListener("click", resetWorkspaceForm);
elements.saveUserButton?.addEventListener("click", saveUser);
elements.resetUserButton?.addEventListener("click", resetUserForm);
elements.sandboxCheckButton.addEventListener("click", checkSandbox);
elements.createRepositoryButton?.addEventListener("click", createRepositoryConnection);
elements.resetRepositoryButton?.addEventListener("click", resetRepositoryForm);
elements.saveTaskButton?.addEventListener("click", saveCurrentTask);
elements.auditFilterButton?.addEventListener("click", () => {
  state.auditFilters = currentAuditFilters();
  refreshAll();
});
elements.auditClearFilterButton?.addEventListener("click", clearAuditFilters);
[ 
  elements.auditActionFilterInput,
  elements.auditResourceTypeFilterInput,
  elements.auditResourceIdFilterInput
].forEach((input) => input?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    state.auditFilters = currentAuditFilters();
    refreshAll();
  }
}));
[
  elements.qualitySelect,
  elements.useCaseSelect,
  elements.traceTypeSelect,
  elements.failureTypeInput,
  elements.traceErrorStepSelect,
  elements.usableForSftInput,
  elements.usableForPreferenceInput,
  elements.rootCauseInput,
  elements.preferredActionInput,
  elements.labelNotesInput
].forEach((input) => {
  input?.addEventListener("input", () => {
    state.datasetFormDirty = true;
  });
  input?.addEventListener("change", () => {
    state.datasetFormDirty = true;
  });
});
[
  elements.memoryTaskIdInput,
  elements.memoryRunIdInput,
  elements.memoryKeyInput,
  elements.memorySummaryInput,
  elements.memoryDetailInput
].forEach((input) => input?.addEventListener("input", () => {
  state.memoryFormDirty = true;
}));
[
  elements.workspaceNameInput,
  elements.workspaceOwnerInput,
  elements.workspaceStatusSelect,
  elements.workspaceMaxTasksInput,
  elements.workspaceMaxActiveRunsInput,
  elements.workspaceMaxDailyCostInput
].forEach((input) => {
  input?.addEventListener("input", () => {
    state.workspaceFormDirty = true;
  });
  input?.addEventListener("change", () => {
    state.workspaceFormDirty = true;
  });
});
[
  elements.userWorkspaceSelect,
  elements.userEmailInput,
  elements.userNameInput,
  elements.userRoleSelect,
  elements.userStatusSelect
].forEach((input) => {
  input?.addEventListener("input", () => {
    state.userFormDirty = true;
  });
  input?.addEventListener("change", () => {
    state.userFormDirty = true;
  });
});
[
  elements.repositoryNameInput,
  elements.repositoryProviderSelect,
  elements.repositoryUrlInput,
  elements.repositoryLocalPathInput,
  elements.repositoryDefaultBranchInput,
  elements.repositoryCredentialInput
].forEach((input) => {
  input?.addEventListener("input", () => {
    state.repositoryFormDirty = true;
  });
  input?.addEventListener("change", () => {
    state.repositoryFormDirty = true;
  });
});
[
  elements.memoryScopeSelect,
  elements.memoryTypeSelect,
  elements.memoryStatusSelect
].forEach((select) => select?.addEventListener("change", () => {
  state.memoryFormDirty = true;
}));
elements.taskSearchInput?.addEventListener("input", scheduleTaskFilterRefresh);
elements.taskStatusFilter?.addEventListener("change", () => {
  state.taskFormDirty = false;
  refreshAll();
});
elements.taskTypeFilter?.addEventListener("change", () => {
  state.taskFormDirty = false;
  refreshAll();
});
elements.taskWorkspaceFilter?.addEventListener("change", () => {
  state.taskFormDirty = false;
  refreshAll();
});
elements.clearTaskFiltersButton?.addEventListener("click", clearTaskFilters);
elements.sessionUserSelect?.addEventListener("change", () => {
  state.currentUserId = elements.sessionUserSelect.value || "user_admin";
  globalThis.localStorage?.setItem("researchforge_user_id", state.currentUserId);
  refreshAll();
});
elements.runHistorySelect?.addEventListener("change", async () => {
  const runId = elements.runHistorySelect.value;
  if (!runId) return;
  try {
    await loadRun(runId);
    render();
  } catch (error) {
    showError(error);
  }
});
[
  elements.titleInput,
  elements.repoInput,
  elements.testInput,
  elements.goalInput,
  elements.executionConfigInput,
  elements.taskWorkspaceSelect,
  elements.maxStepsInput,
  elements.testTimeoutInput,
  elements.maxToolCallsInput,
  elements.maxCostInput
].forEach((input) => input?.addEventListener("input", () => {
  state.taskFormDirty = true;
}));
[
  elements.qualityFilterSelect,
  elements.traceFilterSelect,
  elements.useCaseFilterSelect,
  elements.statusFilterSelect
].forEach((select) => select?.addEventListener("change", render));

handleGitHubCallback()
  .catch(showError)
  .then(() => refreshAll())
  .then(() => {
  if (!state.modelFormInitialized) {
    syncModelFormToSelection();
  }
  if (!state.strategyFormInitialized) {
    syncStrategyFormToSelection();
  }
  if (!state.policyFormInitialized) {
    syncPolicyFormToSelection();
  }
  syncPolicyPreviewForm();
});
