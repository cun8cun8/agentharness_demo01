const operationElement = (id) => document.getElementById(id);

function setOperationOptions(id, rows, label) {
  const select = operationElement(id);
  const previous = select.value;
  select.innerHTML = rows.map((row) => `<option value="${escapeHtml(row.id)}">${escapeHtml(label(row))}</option>`).join("");
  if (rows.some((row) => row.id === previous)) select.value = previous;
}

document.addEventListener("rf:updated", () => {
  setOperationOptions("knowledgeWorkspace", state.workspaces, (row) => row.name);
  setOperationOptions("notebookBrief", state.researchBriefs, (row) => row.question);
  setOperationOptions("publishRepository", state.repositories, (row) => row.name);
  setOperationOptions("publishRun", state.runs.filter((row) => row.status === "completed"), (row) => `${state.tasks.find((task) => task.id === row.task_id)?.title || row.task_id} / ${row.id}`);
});

const operationErrors = {
  AUTHENTICATION_REQUIRED: "请先登录。", UNAUTHENTICATED: "登录密钥无效。", SESSION_INVALID: "会话已失效，请重新登录。",
  NETWORK_DISABLED: "当前环境未启用网络访问。", EMBEDDING_NOT_CONFIGURED: "尚未配置向量模型。",
  NEO4J_NOT_CONFIGURED: "尚未配置关系数据库。", RUN_REPOSITORY_MISMATCH: "运行记录与所选仓库不匹配。",
  RUN_REQUIRES_CLEAN_SOURCE_REVISION: "该运行缺少干净的原始提交记录，请在干净仓库上重新运行任务。",
  NOTEBOOK_ISOLATION_REQUIRED: "生产环境需要配置容器沙箱。", STORE_WRITE_CONFLICT: "数据已被其他进程更新，请刷新后重试。"
};

async function operation(statusId, button, action) {
  const output = operationElement(statusId);
  button.disabled = true;
  output.textContent = "处理中";
  try {
    await action(output);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output.textContent = Object.entries(operationErrors).find(([code]) => message.includes(code))?.[1] || `操作失败：${message}`;
  } finally {
    button.disabled = false;
  }
}

async function downloadOperation(path, filename) {
  const response = await fetch(`${API_BASE}${path}`, { credentials: "include", headers: { ...(API_KEY ? { "X-API-Key": API_KEY } : {}), ...(state.developmentAuth && state.currentUserId ? { "X-User-ID": state.currentUserId } : {}) } });
  if (!response.ok) throw new Error(`下载失败 (${response.status})`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

operationElement("apiKeyLoginForm").addEventListener("submit", (event) => {
  event.preventDefault();
  operation("loginResult", event.submitter, async (output) => {
    API_KEY = operationElement("loginApiKeyInput").value;
    state.currentUserId = "";
    try {
      const session = await api("/auth/session");
      state.session = session;
      state.currentUserId = session.user.id;
      operationElement("loginApiKeyInput").value = "";
      output.textContent = "已登录";
    } finally {
      API_KEY = "";
    }
    await refreshAll();
  });
});

operationElement("knowledgeSearchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  operation("knowledgeStatus", event.submitter, async (output) => {
    const query = new URLSearchParams({ q: operationElement("knowledgeQuery").value, workspace_id: operationElement("knowledgeWorkspace").value });
    const result = await api(`/knowledge/search?${query}`);
    operationElement("knowledgeResults").innerHTML = result.items.map((item) => `<article class="knowledge-result"><strong>${escapeHtml(item.title)}</strong><small>匹配度 ${Number(item.score).toFixed(3)} / ${escapeHtml(item.source_id)}</small><pre>${escapeHtml(item.content)}</pre></article>`).join("");
    output.textContent = `找到 ${result.items.length} 条证据`;
  });
});

operationElement("indexKnowledgeButton").addEventListener("click", (event) => operation("knowledgeStatus", event.currentTarget, async (output) => {
  const result = await api("/knowledge/index", { method: "POST", body: JSON.stringify({ workspace_id: operationElement("knowledgeWorkspace").value, graph: operationElement("syncKnowledgeGraph").checked }) });
  output.textContent = `索引完成：${result.documents} 份文档，${result.chunks} 个片段`;
}));

operationElement("loadKnowledgeGraph").addEventListener("click", (event) => operation("knowledgeStatus", event.currentTarget, async (output) => {
  const result = await api(`/knowledge/graph?workspace_id=${encodeURIComponent(operationElement("knowledgeWorkspace").value)}`);
  const names = Object.fromEntries(result.nodes.map((node) => [node.id, node.title]));
  operationElement("knowledgeResults").innerHTML = result.edges.map((edge) => `<div class="relation-row"><span>${escapeHtml(names[edge.source] || edge.source)}</span><span>${edge.relation === "cites" ? "引用" : "产出"}</span><span>${escapeHtml(names[edge.target] || edge.target)}</span></div>`).join("");
  output.textContent = `${result.nodes.length} 个条目，${result.edges.length} 条关系`;
}));

operationElement("addNotebookCell").addEventListener("click", () => {
  const count = document.querySelectorAll(".notebook-source").length;
  if (count >= 30) return;
  const label = document.createElement("label");
  label.textContent = `代码单元 ${count + 1}`;
  const input = document.createElement("textarea");
  input.className = "notebook-source";
  input.rows = 5;
  input.spellcheck = false;
  input.required = true;
  label.append(input);
  operationElement("notebookCells").append(label);
  input.focus();
});

operationElement("executeNotebookForm").addEventListener("submit", (event) => {
  event.preventDefault();
  operation("notebookStatus", event.submitter, async (output) => {
    let result = await api(`/research/briefs/${encodeURIComponent(operationElement("notebookBrief").value)}/execute`, { method: "POST", body: JSON.stringify({ cells: Array.from(document.querySelectorAll(".notebook-source"), (node) => node.value), timeout_seconds: Number(operationElement("notebookTimeout").value) }) });
    operationElement("notebookOutput").replaceChildren();
    if (result.status === "queued") {
      const deadline = Date.now() + 900000;
      const jobId = result.job_id;
      while (Date.now() < deadline) {
        const job = await api(`/jobs/${encodeURIComponent(jobId)}`);
        output.textContent = job.status === "running" ? "实验执行中" : `等待实验结果：${jobId}`;
        if (["completed", "failed", "cancelled"].includes(job.status)) {
          if (!job.result_json?.notebook_id) throw new Error(job.error || `实验${job.status === "cancelled" ? "已取消" : "失败"}`);
          result = await api(`/research/notebook-runs/${encodeURIComponent(job.result_json.notebook_id)}`);
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      if (result.status === "queued") throw new Error(`等待超时，任务仍保留在队列中：${jobId}`);
    }
    output.textContent = result.status === "queued" ? `实验已排队：${result.job_id}` : result.status === "completed" ? "实验完成" : "实验执行失败";
    if (result.cells) {
      operationElement("notebookOutput").innerHTML = result.cells.filter((cell) => cell.outputs?.length).map((cell) => `<pre>${escapeHtml(cell.outputs.map((item) => item.text || item.data?.["text/plain"] || item.evalue || "").join("\n"))}</pre>`).join("");
      const download = document.createElement("button");
      download.className = "ghost-button";
      download.textContent = "下载实验记录";
      download.addEventListener("click", () => operation("notebookStatus", download, async (status) => { await downloadOperation(`/research/notebook-runs/${result.id}/download`, `${result.id}.ipynb`); status.textContent = "已下载"; }));
      operationElement("notebookOutput").append(download);
    }
  });
});

operationElement("publishPr").addEventListener("change", (event) => { if (event.target.checked) operationElement("publishPush").checked = true; });
operationElement("publishPush").addEventListener("change", (event) => { if (!event.target.checked) operationElement("publishPr").checked = false; });
operationElement("publishPatchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  operation("publishStatus", event.submitter, async (output) => {
    const result = await api(`/integrations/repositories/${encodeURIComponent(operationElement("publishRepository").value)}/publish`, { method: "POST", body: JSON.stringify({ run_id: operationElement("publishRun").value, branch: operationElement("publishBranch").value, title: operationElement("publishTitle").value, body: operationElement("publishBody").value, push: operationElement("publishPush").checked, create_pull_request: operationElement("publishPr").checked }) });
    output.textContent = result.pull_request_error ? "分支已推送，合并请求创建失败" : result.pushed ? "分支已推送" : "发布包已生成";
    const container = operationElement("publishResult");
    container.textContent = `提交 ${result.commit}`;
    const button = document.createElement("button");
    button.className = "ghost-button";
    button.textContent = "下载代码发布包";
    button.addEventListener("click", () => operation("publishStatus", button, async (status) => { await downloadOperation(result.download_path.replace("/api/v1", ""), `${result.branch.replaceAll("/", "-")}.bundle`); status.textContent = "已下载"; }));
    container.append(button);
    if (result.pull_request?.url && new URL(result.pull_request.url).protocol === "https:") {
      const link = document.createElement("a");
      link.href = result.pull_request.url;
      link.textContent = `合并请求 #${result.pull_request.number}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      container.append(link);
    }
  });
});
