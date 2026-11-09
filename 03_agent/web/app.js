"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const STORAGE_KEY = "pyagent-web-history-v1";
const state = { mode: "ask", view: "chat", busy: false, messages: [], toastTimer: null, readerRequest: 0, readerDocument: null, readerReturnFocus: null, readerResizeTimer: null };

function makeNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("visible"), 2800);
}

function loadHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    if (Array.isArray(saved)) {
      state.messages = saved.filter((item) => item && ["user", "assistant", "error"].includes(item.role) && typeof item.content === "string").slice(-24);
    }
  } catch (_) { state.messages = []; }
}

function saveHistory() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.messages.slice(-24))); } catch (_) { /* 存储不可用时仍保留当前页面记录。 */ }
}

function scrollMessages() {
  requestAnimationFrame(() => { $("#messages").scrollTop = $("#messages").scrollHeight; });
}

function appendCitations(container, text) {
  const parts = String(text).split(/(\[S\d+\])/g);
  for (const part of parts) {
    const match = part.match(/^\[S(\d+)\]$/);
    if (!match) {
      container.appendChild(document.createTextNode(part));
      continue;
    }
    const button = makeNode("button", "citation", `[S${match[1]}]`);
    button.type = "button";
    button.dataset.source = `S${match[1]}`;
    button.title = `查看来源 S${match[1]}`;
    container.appendChild(button);
  }
}

function messageBadge(message) {
  if (message.role === "error") return "请求失败";
  if (message.mode === "refusal") return "资料不足";
  if (message.requestMode === "search") return "原文检索";
  if (message.mode === "search") return "原文降级";
  return "本地回答";
}

function createMessageNode(message) {
  const wrapper = makeNode("article", `message ${message.role}-message`);
  wrapper.dataset.messageId = message.id;
  if (message.role === "user") {
    const bubble = makeNode("div", "user-bubble", message.content);
    wrapper.appendChild(bubble);
    return wrapper;
  }
  const avatar = makeNode("div", "assistant-avatar", message.role === "error" ? "!" : "P");
  const body = makeNode("div", "assistant-body");
  const heading = makeNode("div", "message-heading");
  heading.appendChild(makeNode("strong", "", "PyAgent"));
  heading.appendChild(makeNode("span", `answer-badge ${message.mode || "error"}`, messageBadge(message)));
  if (message.elapsedSeconds) heading.appendChild(makeNode("span", "message-time", `${Number(message.elapsedSeconds).toFixed(1)} 秒`));
  body.appendChild(heading);
  const answer = makeNode("div", "answer-text");
  appendCitations(answer, message.content);
  body.appendChild(answer);
  if (message.warning) body.appendChild(makeNode("div", "message-warning", `提示：${message.warning}`));
  const footer = makeNode("div", "message-footer");
  const copy = makeNode("button", "message-action", "复制回答");
  copy.type = "button";
  copy.dataset.action = "copy";
  footer.appendChild(copy);
  const sourceCount = (message.sources || []).length + (message.candidates || []).length;
  if (sourceCount) {
    const sources = makeNode("button", "message-action", `查看 ${sourceCount} 条来源`);
    sources.type = "button";
    sources.dataset.action = "sources";
    footer.appendChild(sources);
  }
  if (message.retrievalMode) footer.appendChild(makeNode("span", "retrieval-label", message.retrievalMode === "hybrid" ? "混合检索" : "关键词检索"));
  body.appendChild(footer);
  wrapper.append(avatar, body);
  return wrapper;
}

function renderMessage(message, save = true) {
  $("#welcome").hidden = true;
  $("#messages").appendChild(createMessageNode(message));
  if (save) {
    state.messages.push(message);
    state.messages = state.messages.slice(-24);
    saveHistory();
  }
  scrollMessages();
}

function newIdentifier() { return `${Date.now()}-${Math.random().toString(16).slice(2)}`; }

function renderHistory() {
  $$(".message").forEach((node) => node.remove());
  $("#welcome").hidden = state.messages.length > 0;
  for (const message of state.messages) renderMessage(message, false);
  const last = state.messages[state.messages.length - 1];
  if (last?.role === "assistant") renderEvidence(last.sources || [], last.candidates || [], false);
  if (last?.role === "error") renderEvidence([], [], false);
}

function renderEvidence(sources, candidates, openPanel = true) {
  const list = $("#sources-list");
  list.textContent = "";
  const official = Array.isArray(sources) ? sources : [];
  const weak = Array.isArray(candidates) ? candidates : [];
  const evidence = official.length ? official : weak;
  $("#source-count").textContent = String(evidence.length);
  $("#evidence-kind").textContent = official.length ? "正式来源 · 可在项目文件中核查" : "弱相关候选 · 未作为回答依据";
  $("#evidence-caption").textContent = official.length ? "点击章节标题可阅读全文并定位引用段落" : evidence.length ? "这些候选不足以支持可靠回答" : "回答引用的原文会显示在这里";
  if (!evidence.length) {
    const empty = makeNode("div", "sources-empty");
    empty.appendChild(makeNode("div", "empty-symbol", "⌁"));
    empty.appendChild(makeNode("h3", "", "暂无可展示的来源"));
    empty.appendChild(makeNode("p", "", "当前结果没有正式引用。可以换一个更具体的问题重新检索。"));
    list.appendChild(empty);
    return;
  }
  for (const source of evidence) {
    const card = makeNode("article", "source-card");
    card.id = `source-${source.id}`;
    card.dataset.source = source.id;
    const head = makeNode("div", "source-card-head");
    head.appendChild(makeNode("span", "source-id", source.id));
    if (typeof source.score === "number") head.appendChild(makeNode("span", "source-score", `相关度 ${source.score.toFixed(3)}`));
    card.appendChild(head);
    const title = makeNode("h3", "source-title");
    const titleButton = makeNode("button", "source-title-button", source.title || "未命名资料");
    titleButton.type = "button";
    titleButton.dataset.action = "open-source";
    titleButton.dataset.chunkId = source.chunk_id || "";
    titleButton.title = "阅读全文并定位到引用段落";
    titleButton.disabled = !source.chunk_id;
    title.appendChild(titleButton);
    card.appendChild(title);
    const location = makeNode("p", "source-location", source.locator || "无章节定位");
    card.appendChild(location);
    const path = makeNode("p", "source-path", source.source_path || "");
    path.title = source.source_path || "";
    card.appendChild(path);
    const snippet = makeNode("p", "source-snippet collapsed", source.snippet || "无原文摘录");
    card.appendChild(snippet);
    const actions = makeNode("div", "source-actions");
    const read = makeNode("button", "source-action read-source", "阅读全文 ↗");
    read.type = "button";
    read.dataset.action = "open-source";
    read.dataset.chunkId = source.chunk_id || "";
    read.disabled = !source.chunk_id;
    actions.appendChild(read);
    const expand = makeNode("button", "source-action", "展开摘录");
    expand.type = "button";
    expand.dataset.action = "expand-source";
    actions.appendChild(expand);
    const copy = makeNode("button", "source-action", "复制路径");
    copy.type = "button";
    copy.dataset.action = "copy-path";
    copy.dataset.path = source.source_path || "";
    actions.appendChild(copy);
    card.appendChild(actions);
    list.appendChild(card);
  }
  if (openPanel && window.innerWidth <= 1050) $("#sources-panel").classList.add("open");
}

function focusSource(sourceId) {
  const card = document.getElementById(`source-${sourceId}`);
  if (!card) { showToast(`当前来源面板中没有 ${sourceId}`); return; }
  if (window.innerWidth <= 1050) $("#sources-panel").classList.add("open");
  $$(".source-card.highlight").forEach((node) => node.classList.remove("highlight"));
  card.classList.add("highlight");
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => card.classList.remove("highlight"), 1800);
}

function scrollReaderToTarget(behavior = "smooth") {
  const content = $("#reader-content");
  const target = content.querySelector(".reader-line.target-start");
  if (!target) return;
  const top = Math.max(0, target.offsetTop - content.clientHeight * 0.28);
  content.scrollTo({ top, behavior });
}

function renderReaderDocument(payload) {
  state.readerDocument = payload;
  $("#reader-title").textContent = payload.title || "未命名资料";
  $("#reader-path").textContent = payload.source_path || "";
  $("#reader-location").textContent = payload.locator || "无章节定位";
  const target = payload.target;
  if (target) {
    const lines = target.line_start === target.line_end ? `第 ${target.line_start} 行` : `第 ${target.line_start}–${target.line_end} 行`;
    $("#reader-match").textContent = `${target.exact ? "已定位到引用原文" : "已定位到所属章节"} · ${lines} · 高亮显示`;
  } else {
    $("#reader-match").textContent = "已显示全文，但当前文件中没有匹配到引用位置";
  }
  const warning = $("#reader-warning");
  warning.textContent = payload.warning || "";
  warning.hidden = !payload.warning;
  $("#reader-copy-path").dataset.path = payload.source_path || "";
  $("#reader-target").disabled = !target;
  $("#reader-stats").textContent = `${Number(payload.line_count || 0).toLocaleString("zh-CN")} 行 · ${(Number(payload.file_size || 0) / 1024).toFixed(1)} KB · 只读`;
  const content = $("#reader-content");
  content.textContent = "";
  const fragment = document.createDocumentFragment();
  const lines = String(payload.content || "").split("\n");
  lines.forEach((text, index) => {
    const number = index + 1;
    const line = makeNode("div", "reader-line");
    line.id = `reader-line-${number}`;
    if (/^#{1,6}\s/.test(text)) line.classList.add("markdown-heading");
    if (target && number >= target.line_start && number <= target.line_end) line.classList.add("target-line");
    if (target && number === target.line_start) line.classList.add("target-start");
    const lineNumber = makeNode("span", "reader-line-number", String(number));
    lineNumber.setAttribute("aria-hidden", "true");
    line.appendChild(lineNumber);
    line.appendChild(makeNode("span", "reader-line-text", text || " "));
    fragment.appendChild(line);
  });
  content.appendChild(fragment);
  content.scrollTop = 0;
  $("#reader-loading").hidden = true;
  content.hidden = false;
  requestAnimationFrame(() => {
    scrollReaderToTarget("instant");
    content.focus({ preventScroll: true });
  });
}

async function openSourceDocument(chunkId, trigger) {
  if (!chunkId) { showToast("这条历史来源缺少原文定位，请重新检索"); return; }
  const requestId = ++state.readerRequest;
  state.readerDocument = null;
  state.readerReturnFocus = trigger || document.activeElement;
  $("#reader-overlay").hidden = false;
  $("#reader-title").textContent = "正在打开原文";
  $("#reader-path").textContent = chunkId;
  $("#reader-location").textContent = "正在读取引用位置…";
  $("#reader-match").textContent = "原文只从本机读取，不会被修改";
  $("#reader-warning").hidden = true;
  $("#reader-loading").hidden = false;
  $("#reader-content").hidden = true;
  $("#reader-content").textContent = "";
  $("#reader-target").disabled = true;
  $("#reader-copy-path").dataset.path = "";
  $("#reader-close").focus();
  try {
    const response = await fetch(`/api/source?chunk_id=${encodeURIComponent(chunkId)}`, {
      headers: { "Accept": "application/json", "X-PyAgent-Client": "local-ui" },
    });
    let payload;
    try { payload = await response.json(); } catch (_) { throw new Error(`原文服务返回了无法解析的响应（HTTP ${response.status}）`); }
    if (!response.ok) throw new Error(payload.error?.message || `原文读取失败（HTTP ${response.status}）`);
    if (requestId !== state.readerRequest) return;
    if (typeof payload.content !== "string") throw new Error("原文服务没有返回有效内容");
    renderReaderDocument(payload);
  } catch (error) {
    if (requestId !== state.readerRequest) return;
    $("#reader-title").textContent = "无法打开原文";
    $("#reader-location").textContent = "读取请求未完成";
    $("#reader-match").textContent = "可以关闭阅读器后重试，或先查看右侧摘录";
    $("#reader-loading").hidden = true;
    const content = $("#reader-content");
    content.hidden = false;
    const panel = makeNode("div", "reader-error");
    panel.appendChild(makeNode("h3", "", "原文暂时不可用"));
    panel.appendChild(makeNode("p", "", error.message));
    content.appendChild(panel);
  }
}

function closeReader() {
  if ($("#reader-overlay").hidden) return;
  ++state.readerRequest;
  state.readerDocument = null;
  clearTimeout(state.readerResizeTimer);
  $("#reader-overlay").hidden = true;
  $("#reader-content").textContent = "";
  const previous = state.readerReturnFocus;
  state.readerReturnFocus = null;
  if (previous?.isConnected) previous.focus();
}

async function copyText(text, successMessage = "已复制") {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    showToast(successMessage);
  } catch (_) { showToast("复制失败，请手动选择文本"); }
}

function createLoadingNode(mode) {
  const wrapper = makeNode("article", "message assistant-message loading-message");
  wrapper.appendChild(makeNode("div", "assistant-avatar", "P"));
  const body = makeNode("div", "assistant-body");
  body.appendChild(makeNode("strong", "loading-title", mode === "ask" ? "正在检索并整理回答" : "正在检索本地原文"));
  const row = makeNode("div", "loading-row");
  row.appendChild(makeNode("span", "loading-dots", "● ● ●"));
  row.appendChild(makeNode("span", "loading-elapsed", "0 秒"));
  body.appendChild(row);
  body.appendChild(makeNode("p", "loading-note", mode === "ask" && !$("#lexical-only").checked ? "本地 CPU 模型首次加载可能需要一些时间，请稍候。" : "正在检查本地索引，请稍候。"));
  wrapper.appendChild(body);
  return wrapper;
}

function setBusy(busy) {
  state.busy = busy;
  $("#send-button").disabled = busy;
  $("#question-input").readOnly = busy;
  $$("[data-set-mode], .sample-card, #new-chat").forEach((node) => { node.disabled = busy; });
}

async function sendQuestion(rawQuestion) {
  const question = String(rawQuestion || "").trim();
  if (!question) { showToast("请先输入一个问题"); return; }
  if (state.busy) return;
  const requestMode = state.mode;
  const lexicalOnly = $("#lexical-only").checked;
  const topK = Number($("#top-k").value);
  setBusy(true);
  $("#question-input").value = "";
  resizeInput();
  renderMessage({ id: newIdentifier(), role: "user", content: question });
  const loading = createLoadingNode(requestMode);
  $("#messages").appendChild(loading);
  scrollMessages();
  const started = Date.now();
  const timer = setInterval(() => {
    const elapsed = loading.querySelector(".loading-elapsed");
    if (elapsed) elapsed.textContent = `${Math.floor((Date.now() - started) / 1000)} 秒`;
  }, 1000);
  try {
    const response = await fetch(`/api/${requestMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PyAgent-Client": "local-ui" },
      body: JSON.stringify({ question, top_k: topK, lexical_only: lexicalOnly }),
    });
    let payload;
    try { payload = await response.json(); } catch (_) { throw new Error(`本地服务返回了无法解析的响应（HTTP ${response.status}）`); }
    if (!response.ok) throw new Error(payload.error?.message || `请求失败（HTTP ${response.status}）`);
    loading.remove();
    const message = {
      id: newIdentifier(), role: "assistant", content: payload.answer || "本地服务没有返回回答文本。",
      mode: payload.mode, requestMode: payload.request_mode || requestMode,
      retrievalMode: payload.retrieval_mode || (payload.request_mode === "search" ? payload.mode : null),
      warning: payload.warning || null, sources: payload.sources || [], candidates: payload.candidates || [],
      elapsedSeconds: payload.elapsed_seconds,
    };
    renderMessage(message);
    renderEvidence(message.sources, message.candidates);
  } catch (error) {
    loading.remove();
    const message = { id: newIdentifier(), role: "error", content: `这次请求没有完成。\n${error.message}\n\n请确认启动 PyAgent 的命令窗口仍然打开，并查看窗口中的错误信息。` };
    renderMessage(message);
    renderEvidence([], [], false);
  } finally {
    clearInterval(timer);
    setBusy(false);
    $("#question-input").focus();
  }
}

async function refreshStatus() {
  if (state.statusBusy) return;
  state.statusBusy = true;
  const pill = $("#connection-pill");
  try {
    const response = await fetch("/api/status", { headers: { "Accept": "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (status.app !== "pyagent-local") throw new Error("不是 PyAgent 本地服务");
    const index = status.index || {};
    for (const [key, id] of [["files","#stat-files"],["chunks","#stat-chunks"],["vectors","#stat-vectors"],["pending","#stat-pending"]]) {
      $(id).textContent = Number(index[key] || 0).toLocaleString("zh-CN");
    }
    $("#library-chunks").textContent = Number(index.chunks || 0).toLocaleString("zh-CN");
    const ready = index.exists && !index.error && index.chunks > 0;
    $("#index-state").innerHTML = "";
    $("#index-state").appendChild(makeNode("span", "status-dot"));
    $("#index-state").appendChild(document.createTextNode(index.error ? "索引读取失败" : ready ? "索引就绪 · 可以提问" : "尚未建立知识索引"));
    $("#welcome-sources").textContent = ready ? `${Number(index.files).toLocaleString("zh-CN")} 个来源 · ${Number(index.chunks).toLocaleString("zh-CN")} 个片段` : "索引尚未就绪";
    const configuration = status.configuration || {};
    $("#privacy-title").textContent = configuration.local ? "资料留在本机" : "本地检索 · 按配置生成";
    $("#privacy-copy").textContent = configuration.local ? "页面仅连接本机服务，无广告、无追踪、无云端依赖。" : "资料检索在本机完成；生成请求遵循当前模型后端配置。";
    $("#model-name").textContent = configuration.model || "未配置生成模型";
    $("#model-name").title = configuration.model || "未配置生成模型";
    $("#embed-name").textContent = configuration.embedding_model || "未配置向量模型";
    $("#embed-name").title = configuration.embedding_model || "未配置向量模型";
    if (!state.statusInitialized && configuration.default_top_k) $("#top-k").value = String(configuration.default_top_k);
    state.statusInitialized = true;
    pill.classList.remove("error", "warning");
    if (status.ollama?.connected === false) {
      pill.classList.add("warning");
      $("#connection-text").textContent = "界面已连接 · 模型未就绪";
      pill.title = status.ollama.error || "请点击重试；仍可尝试原文关键词检索";
    } else {
      $("#connection-text").textContent = configuration.local ? "本地服务已连接" : "服务已连接";
      pill.title = `网页与索引正常。Ollama：${status.ollama?.url || "未检测"}`;
    }
  } catch (_) {
    pill.classList.remove("warning");
    pill.classList.add("error");
    $("#connection-text").textContent = "本地服务连接失败";
    pill.title = "请确认启动 PyAgent 的命令窗口仍然打开，然后点击重试";
  } finally { state.statusBusy = false; }
}

function setMode(mode) {
  if (state.busy || !["ask", "search"].includes(mode)) return;
  state.mode = mode;
  $$("[data-set-mode]").forEach((button) => button.classList.toggle("active", button.dataset.setMode === mode));
  if (typeof updateWorkspaceNavigation === "function") updateWorkspaceNavigation();
  $("#page-title").textContent = mode === "ask" ? "知识问答" : "原文检索";
  $("#page-subtitle").textContent = mode === "ask" ? "从本地资料中寻找答案，让结论有据可查" : "只返回真实原文，不调用生成模型改写";
  $("#question-input").placeholder = mode === "ask" ? "问一个与本地资料有关的问题…" : "输入概念、代码或技术名词，查找原始段落…";
  closeSidebar();
}

function resizeInput() {
  const input = $("#question-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  $("#char-count").textContent = `${input.value.length} / 4000`;
}

function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#sidebar-scrim").classList.remove("visible");
}

function initializeTheme() {
  let theme = "light";
  try { theme = localStorage.getItem("pyagent-web-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); } catch (_) { /* 使用默认主题。 */ }
  document.documentElement.dataset.theme = theme;
}

$("#question-form").addEventListener("submit", (event) => { event.preventDefault(); sendQuestion($("#question-input").value); });
$("#question-input").addEventListener("input", resizeInput);
$("#question-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendQuestion(event.currentTarget.value); }
});
$$("[data-set-mode]").forEach((button) => button.addEventListener("click", () => {
  if (button.dataset.view === "chat" && typeof switchView === "function") switchView("chat");
  setMode(button.dataset.setMode);
}));
$$("[data-question]").forEach((button) => button.addEventListener("click", () => { setMode("ask"); sendQuestion(button.dataset.question); }));

$("#new-chat").addEventListener("click", () => {
  if (state.busy) return;
  if (state.messages.length && !window.confirm("清空当前对话和浏览器中保存的记录？本地知识索引不会受影响。")) return;
  if (typeof switchView === "function") switchView("chat");
  state.messages = [];
  saveHistory();
  $$(".message").forEach((node) => node.remove());
  $("#welcome").hidden = false;
  renderEvidence([], [], false);
  $("#question-input").focus();
});

$("#theme-toggle").addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("pyagent-web-theme", theme); } catch (_) { /* 主题仅在本次页面生效。 */ }
});
$("#connection-pill").addEventListener("click", refreshStatus);
$("#mobile-menu").addEventListener("click", () => { $("#sidebar").classList.add("open"); $("#sidebar-scrim").classList.add("visible"); });
$("#sidebar-scrim").addEventListener("click", closeSidebar);
$("#sources-close").addEventListener("click", () => $("#sources-panel").classList.remove("open"));
$("#reader-close").addEventListener("click", closeReader);
$("#reader-overlay").addEventListener("click", (event) => { if (event.target === event.currentTarget) closeReader(); });
$("#reader-target").addEventListener("click", () => scrollReaderToTarget());
$("#reader-copy-path").addEventListener("click", (event) => {
  const path = event.currentTarget.dataset.path || "";
  if (path) copyText(path, "原文路径已复制");
});
window.addEventListener("resize", () => {
  if ($("#reader-overlay").hidden) return;
  clearTimeout(state.readerResizeTimer);
  state.readerResizeTimer = setTimeout(() => scrollReaderToTarget("instant"), 120);
});

$("#messages").addEventListener("click", (event) => {
  const citation = event.target.closest(".citation");
  if (citation) {
    const wrapper = citation.closest(".message");
    const message = state.messages.find((item) => item.id === wrapper?.dataset.messageId);
    if (message) renderEvidence(message.sources || [], message.candidates || [], false);
    focusSource(citation.dataset.source);
    return;
  }
  const action = event.target.closest("[data-action]");
  if (!action) return;
  const wrapper = action.closest(".message");
  const message = state.messages.find((item) => item.id === wrapper?.dataset.messageId);
  if (!message) return;
  if (action.dataset.action === "copy") copyText(message.content, "回答已复制");
  if (action.dataset.action === "sources") renderEvidence(message.sources || [], message.candidates || []);
});

$("#sources-list").addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]");
  if (!action) return;
  if (action.dataset.action === "open-source") {
    openSourceDocument(action.dataset.chunkId || "", action);
    return;
  }
  if (action.dataset.action === "copy-path") copyText(action.dataset.path || "", "来源路径已复制");
  if (action.dataset.action === "expand-source") {
    const snippet = action.closest(".source-card").querySelector(".source-snippet");
    const collapsed = snippet.classList.toggle("collapsed");
    action.textContent = collapsed ? "展开摘录" : "收起摘录";
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#reader-overlay").hidden) {
    event.preventDefault();
    closeReader();
    return;
  }
  if (event.key === "Tab" && !$("#reader-overlay").hidden) {
    const focusable = $$("#reader-overlay button:not([disabled]), #reader-overlay [tabindex='0']").filter((node) => node.offsetParent !== null);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (first && event.shiftKey && (document.activeElement === first || !$("#reader-overlay").contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (last && !event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  if (event.key === "Escape") { closeSidebar(); $("#sources-panel").classList.remove("open"); }
  if ((event.ctrlKey || event.metaKey) && event.key === "/" && state.view === "chat") { event.preventDefault(); $("#question-input").focus(); }
});

initializeTheme();
loadHistory();
renderHistory();
resizeInput();
refreshStatus();
setInterval(refreshStatus, 30000);

