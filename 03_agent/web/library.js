"use strict";

const libraryState = {
  documents: [], loaded: false, documentsBusy: false, uploadBusy: false,
  page: 1, pageSize: 12, indexJobId: null, indexPollTimer: null,
  indexStatusBusy: false, indexActive: false, dragDepth: 0,
};
const LIBRARY_EXTENSIONS = new Set(["txt", "md", "markdown", "docx"]);
const MAX_LIBRARY_FILE_BYTES = 25 * 1024 * 1024;

function updateWorkspaceNavigation() {
  $$(".side-link[data-view]").forEach((button) => {
    const active = state.view === "library"
      ? button.dataset.view === "library"
      : button.dataset.view === "chat" && button.dataset.setMode === state.mode;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function switchView(view) {
  if (!["chat", "library"].includes(view)) return;
  const changed = state.view !== view;
  state.view = view;
  $("#chat-view").hidden = view !== "chat";
  $("#library-view").hidden = view !== "library";
  if (view === "library") {
    $("#page-title").textContent = "资料管理";
    $("#page-subtitle").textContent = "拖入本地文档，自动转换并加入知识索引";
  } else {
    $("#page-title").textContent = state.mode === "ask" ? "知识问答" : "原文检索";
    $("#page-subtitle").textContent = state.mode === "ask" ? "从本地资料中寻找答案，让结论有据可查" : "只返回真实原文，不调用生成模型改写";
  }
  updateWorkspaceNavigation();
  closeSidebar();
  if (view === "library" && changed) {
    loadDocuments();
    resumeIndexJob();
  }
}

async function libraryFetch(path, options = {}) {
  const headers = {
    "Accept": "application/json", "X-PyAgent-Client": "local-ui",
    ...(options.headers || {}),
  };
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (_) {
    throw new Error("无法连接本地服务，请确认启动 PyAgent 的命令窗口仍然打开");
  }
  let payload;
  try { payload = await response.json(); }
  catch (_) { throw new Error(`本地服务返回了无法解析的响应（HTTP ${response.status}）`); }
  if (!response.ok) throw new Error(payload.error?.message || `请求失败（HTTP ${response.status}）`);
  return payload;
}

function formatFileSize(bytes) {
  const value = Number(bytes || 0);
  if (value <= 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10240 ? 1 : 0)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function updateLibraryControls() {
  const locked = libraryState.uploadBusy || libraryState.indexActive;
  $("#choose-files").disabled = locked;
  $("#file-input").disabled = locked;
  $("#refresh-index").disabled = locked;
  $("#drop-zone").setAttribute("aria-disabled", String(locked));
}

function showDocumentsError(message) {
  const list = $("#document-list");
  list.textContent = "";
  const panel = makeNode("div", "documents-loading");
  panel.appendChild(makeNode("p", "", `资料列表读取失败：${message}`));
  const retry = makeNode("button", "library-button secondary", "重新读取");
  retry.type = "button";
  retry.addEventListener("click", () => loadDocuments(true));
  panel.appendChild(retry);
  list.appendChild(panel);
  $("#documents-empty").hidden = true;
}

async function loadDocuments(force = false) {
  if (libraryState.documentsBusy || (libraryState.loaded && !force)) {
    if (libraryState.loaded) renderDocuments();
    return;
  }
  libraryState.documentsBusy = true;
  $("#refresh-documents").disabled = true;
  if (!libraryState.loaded) {
    $("#document-list").textContent = "";
    const loading = makeNode("div", "documents-loading");
    loading.appendChild(makeNode("span", "loading-dots", "● ● ●"));
    loading.appendChild(makeNode("p", "", "正在读取本地资料…"));
    $("#document-list").appendChild(loading);
  }
  try {
    const payload = await libraryFetch("/api/documents");
    libraryState.documents = Array.isArray(payload.documents) ? payload.documents : [];
    libraryState.loaded = true;
    $("#library-total").textContent = Number(payload.total || 0).toLocaleString("zh-CN");
    $("#library-imported").textContent = Number(payload.imported_count || 0).toLocaleString("zh-CN");
    $("#document-count").textContent = Number(payload.total || 0).toLocaleString("zh-CN");
    renderDocuments();
  } catch (error) {
    if (!libraryState.loaded) showDocumentsError(error.message);
    else showToast(`资料列表刷新失败：${error.message}`);
  } finally {
    libraryState.documentsBusy = false;
    $("#refresh-documents").disabled = false;
  }
}

function createDocumentRow(documentInfo) {
  const row = makeNode("article", "document-row");
  const main = makeNode("div", "document-main");
  const extension = String(documentInfo.extension || "txt").toUpperCase();
  main.appendChild(makeNode("span", "document-icon", extension === "MARKDOWN" ? "MD" : extension));
  const titleBox = makeNode("div", "document-title");
  const title = makeNode("button", "", documentInfo.title || "未命名资料");
  title.type = "button";
  title.title = documentInfo.first_chunk_id ? "阅读全文" : "这个来源暂时没有可阅读的知识片段";
  title.disabled = !documentInfo.first_chunk_id;
  title.dataset.action = "read-document";
  title.dataset.chunkId = documentInfo.first_chunk_id || "";
  titleBox.appendChild(title);
  const path = makeNode("p", documentInfo.file_exists ? "" : "missing", documentInfo.source_path || "");
  path.title = documentInfo.file_exists ? documentInfo.source_path : "文件不可用，请更新索引";
  titleBox.appendChild(path);
  main.appendChild(titleBox);
  row.appendChild(main);
  row.appendChild(makeNode("span", `document-type ${documentInfo.managed ? "imported" : ""}`, documentInfo.managed ? "页面导入" : "原有资料"));
  row.appendChild(makeNode("span", "document-chunks", Number(documentInfo.chunk_count || 0).toLocaleString("zh-CN")));
  row.appendChild(makeNode("span", "document-size", formatFileSize(documentInfo.file_size)));
  const actions = makeNode("div", "document-actions");
  const read = makeNode("button", "document-action", "阅读");
  read.type = "button";
  read.dataset.action = "read-document";
  read.dataset.chunkId = documentInfo.first_chunk_id || "";
  read.disabled = !documentInfo.first_chunk_id;
  actions.appendChild(read);
  if (documentInfo.managed) {
    const remove = makeNode("button", "document-action remove", "移除");
    remove.type = "button";
    remove.dataset.action = "remove-document";
    remove.dataset.sourcePath = documentInfo.source_path || "";
    remove.dataset.title = documentInfo.title || "这份资料";
    actions.appendChild(remove);
  }
  row.appendChild(actions);
  return row;
}

function filteredDocuments() {
  const query = $("#library-search").value.trim().toLocaleLowerCase("zh-CN");
  const scope = $("#library-scope").value;
  return libraryState.documents.filter((item) => {
    if (scope === "import" && !item.managed) return false;
    if (scope === "library" && item.managed) return false;
    if (!query) return true;
    return `${item.title || ""}\n${item.source_path || ""}`.toLocaleLowerCase("zh-CN").includes(query);
  });
}

function renderDocuments() {
  const documents = filteredDocuments();
  const pages = Math.max(1, Math.ceil(documents.length / libraryState.pageSize));
  libraryState.page = Math.min(Math.max(1, libraryState.page), pages);
  const start = (libraryState.page - 1) * libraryState.pageSize;
  const visible = documents.slice(start, start + libraryState.pageSize);
  const list = $("#document-list");
  list.textContent = "";
  for (const documentInfo of visible) list.appendChild(createDocumentRow(documentInfo));
  $("#documents-empty").hidden = documents.length > 0;
  list.hidden = documents.length === 0;
  $("#document-range").textContent = documents.length
    ? `第 ${start + 1}–${Math.min(start + libraryState.pageSize, documents.length)} 条，共 ${documents.length} 个来源`
    : "0 个来源";
  $("#documents-page").textContent = `${libraryState.page} / ${pages}`;
  $("#documents-prev").disabled = libraryState.page <= 1;
  $("#documents-next").disabled = libraryState.page >= pages;
}

async function removeManagedDocument(button) {
  if (libraryState.indexActive || libraryState.uploadBusy) {
    showToast("请等待当前上传或索引任务完成后再移除资料");
    return;
  }
  const sourcePath = button.dataset.sourcePath || "";
  const title = button.dataset.title || "这份资料";
  if (!sourcePath.startsWith("01_corpus/imports/")) {
    showToast("只能移除资料管理页面导入的文件");
    return;
  }
  if (!window.confirm(`确定从知识库移除“${title}”吗？\n\n文件会移到本地移除备份中，原有书籍不受影响；随后会自动更新索引。`)) return;
  button.disabled = true;
  button.textContent = "移除中…";
  try {
    await libraryFetch("/api/documents/remove", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_path: sourcePath }),
    });
    showToast("资料已移出知识目录并保留备份，正在更新索引");
    await startIndexJob();
  } catch (error) {
    showToast(`移除未完成：${error.message}`);
    if (button.isConnected) {
      button.disabled = false;
      button.textContent = "移除";
    }
  }
}

function createUploadItem(file) {
  const node = makeNode("article", "upload-item queued");
  node.appendChild(makeNode("span", "upload-state-icon", "·"));
  const copy = makeNode("div", "upload-copy");
  copy.appendChild(makeNode("strong", "", file.name));
  copy.appendChild(makeNode("p", "", `${formatFileSize(file.size)} · 等待上传`));
  node.appendChild(copy);
  node.appendChild(makeNode("span", "upload-status", "等待"));
  $("#upload-list").appendChild(node);
  return { file, node };
}

function setUploadState(item, status, detail) {
  const icons = { queued: "·", uploading: "↥", imported: "✓", duplicate: "=", error: "!" };
  const labels = { queued: "等待", uploading: "处理中", imported: "已保存", duplicate: "已存在", error: "失败" };
  item.node.className = `upload-item ${status}`;
  item.node.querySelector(".upload-state-icon").textContent = icons[status] || "·";
  item.node.querySelector(".upload-copy p").textContent = detail;
  item.node.querySelector(".upload-status").textContent = labels[status] || status;
}

async function handleLibraryFiles(fileList) {
  if (libraryState.uploadBusy || libraryState.indexActive) {
    showToast("请等待当前上传或索引任务完成");
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  libraryState.uploadBusy = true;
  updateLibraryControls();
  const list = $("#upload-list");
  list.hidden = false;
  list.textContent = "";
  let importedCount = 0;
  let duplicateCount = 0;
  let failedCount = 0;
  for (const file of files) {
    const item = createUploadItem(file);
    const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!LIBRARY_EXTENSIONS.has(extension)) {
      failedCount += 1;
      setUploadState(item, "error", "不支持这个格式，请选择 TXT、Markdown 或 DOCX");
      continue;
    }
    if (!file.size || file.size > MAX_LIBRARY_FILE_BYTES) {
      failedCount += 1;
      setUploadState(item, "error", file.size ? "单个文件不能超过 25 MiB" : "文件为空");
      continue;
    }
    setUploadState(item, "uploading", extension === "docx" ? "正在上传并提取 DOCX 文字…" : "正在上传并检查文本编码…");
    try {
      const result = await libraryFetch(`/api/documents/import?filename=${encodeURIComponent(file.name)}`, {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
      });
      if (result.status === "duplicate") {
        duplicateCount += 1;
        setUploadState(item, "duplicate", `内容相同，已复用 ${result.source_path}`);
      } else {
        importedCount += 1;
        const conversion = result.converted ? "DOCX 已转换为 Markdown · " : "已保存 · ";
        setUploadState(item, "imported", conversion + result.source_path);
      }
    } catch (error) {
      failedCount += 1;
      setUploadState(item, "error", error.message);
    }
  }
  libraryState.uploadBusy = false;
  $("#file-input").value = "";
  updateLibraryControls();
  if (importedCount) {
    showToast(`已保存 ${importedCount} 份新资料${failedCount ? `，${failedCount} 份失败` : ""}，正在自动更新索引`);
    await startIndexJob();
  } else {
    showToast(failedCount ? `${failedCount} 份文件未导入，请查看具体原因` : `${duplicateCount} 份文件已存在，无需重复索引`);
    await loadDocuments(true);
  }
}

function renderIndexJob(job) {
  if (!job || job.status === "idle") return;
  const active = ["queued", "running"].includes(job.status);
  libraryState.indexActive = active;
  updateLibraryControls();
  const panel = $("#index-job");
  panel.hidden = false;
  panel.classList.toggle("failed", job.status === "failed");
  const icon = $("#job-icon");
  icon.classList.toggle("running", active);
  icon.textContent = active ? "↻" : job.status === "completed" ? "✓" : "!";
  const bar = $("#index-progress-bar");
  bar.classList.remove("indeterminate");
  $("#index-job-path").textContent = job.current_file || "";
  if (active) {
    const titles = {
      queued: "索引任务已排队", waiting: "等待本地模型空闲",
      scanning: "正在扫描并更新资料", embedding: "正在生成新增向量",
    };
    $("#index-job-title").textContent = titles[job.stage] || "正在更新索引";
    $("#index-job-detail").textContent = job.stage === "embedding"
      ? `已处理 ${Number(job.completed || 0)} / ${Number(job.total || 0)} 个知识片段`
      : job.stage === "scanning" ? `已更新 ${Number(job.updated_files || 0)} 个文件；未变化内容会被复用` : "问答与索引共用本地模型，任务会自动开始";
    if (job.stage === "embedding" && Number(job.total) > 0) {
      const percent = Math.min(100, Math.round(Number(job.completed || 0) / Number(job.total) * 100));
      bar.style.width = `${percent}%`;
      $("#job-percent").textContent = `${percent}%`;
    } else {
      bar.classList.add("indeterminate");
      bar.style.width = "";
      $("#job-percent").textContent = "";
    }
    if (!job.current_file) $("#index-job-path").textContent = "增量索引进行中，请不要关闭启动 PyAgent 的命令窗口";
    return;
  }
  if (job.status === "completed") {
    const result = job.result || {};
    const pending = Number(result.embedding_pending || 0);
    $("#index-job-title").textContent = pending ? "关键词索引已更新，向量仍待处理" : "资料索引已更新";
    $("#index-job-detail").textContent = `新增或变更 ${Number(result.updated_files || 0)} 个文件，移除 ${Number(result.removed_files || 0)} 个${pending ? `，${pending} 个片段等待向量` : "，现在可以检索新资料"}`;
    bar.style.width = "100%";
    $("#job-percent").textContent = "100%";
    $("#index-job-path").textContent = `${Number(result.files || 0)} 个来源 · ${Number(result.chunks || 0)} 个片段 · ${Number(result.vectors || 0)} 个向量`;
    return;
  }
  $("#index-job-title").textContent = "索引更新未完成";
  $("#index-job-detail").textContent = job.error || "本地服务没有返回详细错误";
  bar.style.width = "100%";
  $("#job-percent").textContent = "";
  $("#index-job-path").textContent = "已保存的资料不会丢失；处理问题后可点击“更新索引”重试";
}

async function pollIndexJob() {
  clearTimeout(libraryState.indexPollTimer);
  const jobId = libraryState.indexJobId;
  if (!jobId) return;
  try {
    const job = await libraryFetch(`/api/index/status?job_id=${encodeURIComponent(jobId)}`);
    if (libraryState.indexJobId !== jobId) return;
    renderIndexJob(job);
    if (["queued", "running"].includes(job.status)) {
      libraryState.indexPollTimer = setTimeout(pollIndexJob, 900);
      return;
    }
    libraryState.indexJobId = null;
    if (job.status === "completed") {
      await Promise.all([loadDocuments(true), refreshStatus()]);
      showToast("资料库已更新，新内容现在可以检索和引用");
    }
  } catch (error) {
    if (libraryState.indexJobId !== jobId) return;
    libraryState.indexJobId = null;
    renderIndexJob({ status: "failed", error: error.message });
  }
}

async function startIndexJob() {
  if (libraryState.uploadBusy) return;
  if (libraryState.indexActive) {
    showToast("已有一个索引任务正在进行");
    return;
  }
  try {
    const job = await libraryFetch("/api/index", { method: "POST" });
    libraryState.indexJobId = job.job_id;
    renderIndexJob(job);
    await pollIndexJob();
  } catch (error) {
    renderIndexJob({ status: "failed", error: error.message });
    showToast(`索引任务未能启动：${error.message}`);
  }
}

async function resumeIndexJob() {
  if (libraryState.indexStatusBusy || libraryState.indexActive) return;
  libraryState.indexStatusBusy = true;
  try {
    const job = await libraryFetch("/api/index/status");
    if (["queued", "running"].includes(job.status)) {
      libraryState.indexJobId = job.job_id;
      renderIndexJob(job);
      pollIndexJob();
    } else if (["completed", "failed"].includes(job.status) && !libraryState.indexJobId) {
      renderIndexJob(job);
    }
  } catch (_) {
    // 资料列表仍可使用；主动更新索引时会展示具体连接错误。
  } finally {
    libraryState.indexStatusBusy = false;
  }
}

$(".side-link[data-view='library']").addEventListener("click", () => switchView("library"));
$("#choose-files").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (event) => handleLibraryFiles(event.target.files));
$("#refresh-index").addEventListener("click", startIndexJob);
$("#refresh-documents").addEventListener("click", () => loadDocuments(true));
$("#library-search").addEventListener("input", () => { libraryState.page = 1; renderDocuments(); });
$("#library-scope").addEventListener("change", () => { libraryState.page = 1; renderDocuments(); });
$("#documents-prev").addEventListener("click", () => {
  if (libraryState.page > 1) { libraryState.page -= 1; renderDocuments(); }
});
$("#documents-next").addEventListener("click", () => {
  const pages = Math.max(1, Math.ceil(filteredDocuments().length / libraryState.pageSize));
  if (libraryState.page < pages) { libraryState.page += 1; renderDocuments(); }
});
$("#document-list").addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]");
  if (!action) return;
  if (action.dataset.action === "read-document") openSourceDocument(action.dataset.chunkId || "", action);
  if (action.dataset.action === "remove-document") removeManagedDocument(action);
});

const dropZone = $("#drop-zone");
dropZone.addEventListener("dragenter", (event) => {
  event.preventDefault();
  libraryState.dragDepth += 1;
  if (!libraryState.uploadBusy && !libraryState.indexActive) dropZone.classList.add("dragging");
});
dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
});
dropZone.addEventListener("dragleave", (event) => {
  event.preventDefault();
  libraryState.dragDepth = Math.max(0, libraryState.dragDepth - 1);
  if (!libraryState.dragDepth) dropZone.classList.remove("dragging");
});
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  event.stopPropagation();
  libraryState.dragDepth = 0;
  dropZone.classList.remove("dragging");
  handleLibraryFiles(event.dataTransfer?.files || []);
});
$("#library-view").addEventListener("dragover", (event) => event.preventDefault());
$("#library-view").addEventListener("drop", (event) => event.preventDefault());

updateWorkspaceNavigation();
updateLibraryControls();
