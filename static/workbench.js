const moduleMeta = {
  data: {
    title: "编辑部数据汇总",
    eyebrow: "数据工作区",
    description: "AI 查数、数据库浏览和编辑部汇总数据核对。",
  },
  ethics: {
    title: "稿件伦理审查",
    eyebrow: "合规工作区",
    description: "按稿件号或本地文件批量检查伦理声明风险。",
  },
  citation: {
    title: "稿件引用检查",
    eyebrow: "引用工作区",
    description: "解析参考文献并复核年份、来源与作者重复情况。",
  },
};

const state = {
  activeRoute: "home",
  activeModule: null,
  overview: null,
  frames: new Map(),
  themePreference: localStorage.getItem("workbench-theme") || "light",
  toastTimer: null,
  rebuildTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return new Intl.NumberFormat("zh-CN").format(Number(value));
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function showToast(message, type = "success") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", type === "error");
  toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("show"), 3600);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { success: false, error: `请求失败（HTTP ${response.status}）` };
  }
  if (!response.ok || payload.success === false) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

function routeFromHash() {
  const match = location.hash.match(/^#\/module\/(data|ethics|citation)$/);
  if (match) return match[1];
  return "home";
}

function setRoute(route, action = "") {
  if (route === "home") {
    if (location.hash !== "#/home") location.hash = "#/home";
    else renderRoute("home");
    return;
  }
  state.pendingModuleAction = action;
  const hash = `#/module/${route}`;
  if (location.hash !== hash) location.hash = hash;
  else renderRoute(route, action);
}

function renderRoute(route, action = state.pendingModuleAction || "") {
  state.activeRoute = route;
  const isHome = route === "home";
  document.body.classList.toggle("module-mode", !isHome);
  $("#homeView").classList.toggle("active", isHome);
  $("#moduleView").classList.toggle("active", !isHome);
  $("#currentLocation").textContent = isHome ? "工作台" : moduleMeta[route].title;

  $$(".rail-item[data-route]").forEach((button) => {
    const active = button.dataset.route === route;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  if (!isHome) activateModule(route, action);
  state.pendingModuleAction = "";
}

function createModuleFrame(moduleKey) {
  const frame = document.createElement("iframe");
  frame.className = "module-frame";
  frame.dataset.module = moduleKey;
  frame.title = moduleMeta[moduleKey].title;
  frame.src = `/modules/${moduleKey}/?embedded=1`;
  frame.setAttribute("allow", "clipboard-read; clipboard-write");
  frame.addEventListener("load", () => {
    frame.dataset.loaded = "true";
    syncFrameTheme(frame);
    if (state.activeModule === moduleKey) $("#framePlaceholder").classList.add("hidden");
    applyPendingModuleAction(moduleKey);
  });
  $("#moduleFrames").appendChild(frame);
  state.frames.set(moduleKey, frame);
  return frame;
}

function syncFrameTheme(frame) {
  try {
    frame.contentWindow.postMessage(
      { type: "workbench-theme", theme: resolvedTheme(state.themePreference) },
      location.origin,
    );
  } catch {
    // 模块独立打开或仍在载入时，不阻塞工作台主题切换。
  }
}

function applyPendingModuleAction(moduleKey) {
  if (moduleKey !== "data" || !state.pendingFrameAction) return;
  const frame = state.frames.get(moduleKey);
  try {
    if (typeof frame.contentWindow.showPage === "function") {
      frame.contentWindow.showPage(state.pendingFrameAction);
    }
  } catch {
    // 模块仍可正常打开，快捷页签切换失败时不阻塞主流程。
  }
  state.pendingFrameAction = "";
}

function activateModule(moduleKey, action = "") {
  state.activeModule = moduleKey;
  const meta = moduleMeta[moduleKey];
  $("#moduleEyebrow").textContent = meta.eyebrow;
  $("#moduleTitle").textContent = meta.title;
  $("#moduleDescription").textContent = meta.description;
  if (action) state.pendingFrameAction = action;

  let frame = state.frames.get(moduleKey);
  if (!frame) frame = createModuleFrame(moduleKey);
  state.frames.forEach((item, key) => item.classList.toggle("active", key === moduleKey));
  $("#framePlaceholder").classList.toggle("hidden", frame.dataset.loaded === "true");
  syncFrameTheme(frame);
  applyPendingModuleAction(moduleKey);
  updateActiveModuleStatus();
}

function moduleStatusLabel(status) {
  if (!status) return { label: "检查中", className: "" };
  if (status.state === "running") return { label: "运行中", className: "ok" };
  if (status.state === "starting") return { label: "启动中", className: "" };
  if (status.state === "stopped") return { label: "已停止", className: "error" };
  return { label: "未启动", className: "error" };
}

function paintModuleStatus(element, status) {
  const result = moduleStatusLabel(status);
  element.textContent = result.label;
  element.classList.remove("ok", "error");
  if (result.className) element.classList.add(result.className);
}

function updateActiveModuleStatus() {
  if (!state.activeModule || !state.overview) return;
  paintModuleStatus($("#activeModuleStatus"), state.overview.modules[state.activeModule]);
}

function updateOverview(payload) {
  state.overview = payload;
  const database = payload.database || {};
  $("#metricManuscripts").textContent = formatNumber(database.manuscripts);
  $("#metricAuthors").textContent = formatNumber(database.authors);
  $("#metricJournals").textContent = formatNumber(database.journals);
  $("#metricWarnings").textContent = formatNumber(database.warnings);

  const statuses = Object.values(payload.modules || {});
  const runningCount = statuses.filter((item) => item.state === "running").length;
  const allRunning = runningCount === statuses.length && statuses.length > 0;
  $("#readinessText").textContent = allRunning
    ? "三个工作区均已就绪"
    : `${runningCount}/${statuses.length} 个工作区可用`;
  $("#railStatusText").textContent = allRunning ? "系统就绪" : "部分模块异常";
  $("#railStatusDot").classList.toggle("ok", allRunning);
  $("#railStatusDot").classList.toggle("error", !allRunning && statuses.length > 0);

  $$("[data-module-state]").forEach((element) => {
    paintModuleStatus(element, payload.modules[element.dataset.moduleState]);
  });
  updateActiveModuleStatus();

  const ai = payload.ai || {};
  $("#aiStatus").textContent = ai.configured ? `${ai.model || "已配置"}` : "未配置";
  $("#aiCompact").textContent = ai.configured
    ? `AI ${ai.model || "已配置"}  ${ai.apiKeyMasked || ""}`
    : "AI 尚未配置";

  if (database.available) {
    $("#databaseStatus").textContent = `${formatNumber(database.manuscripts)} 篇稿件`;
    const updated = formatDateTime(database.updatedAt);
    $("#databaseDetail").textContent = `${database.sizeMb || 0} MB${updated ? `，更新于 ${updated}` : ""}`;
  } else {
    $("#databaseStatus").textContent = "数据库不可用";
    $("#databaseDetail").textContent = database.error || "未找到已复制的 SQLite 文件";
  }
  updateRebuildStatus(payload.rebuild || {});
}

function updateRebuildStatus(rebuild) {
  const status = $("#rebuildStatus");
  const detail = $("#rebuildDetail");
  const button = $("#rebuildButton");
  button.disabled = rebuild.state === "running";
  button.textContent = rebuild.state === "running" ? "正在重建" : "重建数据库";

  const labels = {
    idle: "尚未在本次运行中重建",
    running: "正在重建数据库",
    completed: "数据库重建完成",
    failed: "数据库重建失败",
  };
  status.textContent = labels[rebuild.state] || rebuild.message || "状态未知";
  const timestamp = rebuild.finishedAt || rebuild.startedAt;
  detail.textContent = [formatDateTime(timestamp), rebuild.message].filter(Boolean).join("，");

  if (rebuild.state === "running") startRebuildPolling();
  else stopRebuildPolling();
}

async function refreshOverview({ silent = false } = {}) {
  const button = $("#refreshButton");
  if (!silent) {
    button.disabled = true;
    button.textContent = "刷新中";
  }
  try {
    const payload = await requestJson("/api/overview");
    updateOverview(payload);
  } catch (error) {
    $("#readinessText").textContent = "无法读取系统状态";
    $("#railStatusText").textContent = "状态异常";
    $("#railStatusDot").classList.add("error");
    if (!silent) showToast(error.message, "error");
  } finally {
    if (!silent) {
      button.disabled = false;
      button.textContent = "刷新状态";
    }
  }
}

async function startDatabaseRebuild() {
  const confirmed = window.confirm(
    "将使用工作区内已复制的 Excel 和映射配置重新生成 SQLite 数据库。旧数据库会由原脚本在当前工作区内备份。是否继续？",
  );
  if (!confirmed) return;
  const button = $("#rebuildButton");
  button.disabled = true;
  button.textContent = "正在启动";
  try {
    const payload = await requestJson("/api/data/rebuild", { method: "POST" });
    updateRebuildStatus(payload.rebuild);
    showToast("数据库重建任务已启动。可以继续使用其他模块。");
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
    button.textContent = "重建数据库";
  }
}

function startRebuildPolling() {
  if (state.rebuildTimer) return;
  state.rebuildTimer = setInterval(async () => {
    try {
      const payload = await requestJson("/api/data/rebuild");
      updateRebuildStatus(payload.rebuild);
      if (payload.rebuild.state === "completed") {
        showToast("数据库已完成重建，概览数据已刷新。");
        await refreshOverview({ silent: true });
      } else if (payload.rebuild.state === "failed") {
        showToast(payload.rebuild.message || "数据库重建失败。", "error");
      }
    } catch (error) {
      stopRebuildPolling();
      showToast(error.message, "error");
    }
  }, 1600);
}

function stopRebuildPolling() {
  if (!state.rebuildTimer) return;
  clearInterval(state.rebuildTimer);
  state.rebuildTimer = null;
}

function resolvedTheme(preference) {
  if (preference !== "auto") return preference;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(preference) {
  state.themePreference = preference;
  localStorage.setItem("workbench-theme", preference);
  document.documentElement.dataset.theme = resolvedTheme(preference);
  state.frames.forEach((frame) => syncFrameTheme(frame));
  $$('[data-theme-choice]').forEach((button) => {
    button.classList.toggle("active", button.dataset.themeChoice === preference);
  });
}

function setSettingsTab(tab) {
  $$(".settings-tab").forEach((button) => button.classList.toggle("active", button.dataset.settingsTab === tab));
  $$(".settings-section").forEach((section) => section.classList.toggle("active", section.dataset.settingsSection === tab));
  $("#settingsSectionTitle").textContent = tab === "ai" ? "AI 服务" : "常规";
  $("#settingsSectionKicker").textContent = tab === "ai" ? "共享模型配置" : "工作台偏好";
}

async function loadAiSettings() {
  const status = $("#settingsFormStatus");
  status.textContent = "正在读取配置";
  status.className = "settings-form-status";
  try {
    const payload = await requestJson("/api/settings");
    $("#apiKeyMasked").textContent = payload.configured ? payload.apiKeyMasked : "未配置";
    $("#apiUrlInput").value = payload.apiUrl || "https://api.deepseek.com/v1/chat/completions";
    $("#modelInput").value = payload.model || "deepseek-chat";
    $("#apiKeyInput").value = "";
    status.textContent = payload.configured ? "已读取本机配置" : "请填写 API Key";
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  }
}

async function openSettings(tab = "general") {
  const dialog = $("#settingsDialog");
  setSettingsTab(tab);
  applyTheme(state.themePreference);
  if (!dialog.open) dialog.showModal();
  if (tab === "ai") await loadAiSettings();
}

function closeSettings() {
  const dialog = $("#settingsDialog");
  if (dialog.open) dialog.close();
}

async function submitAiSettings({ testOnly = false } = {}) {
  const formStatus = $("#settingsFormStatus");
  const saveButton = $("#saveAiButton");
  const testButton = $("#testConnectionButton");
  const apiUrl = $("#apiUrlInput").value.trim();
  const model = $("#modelInput").value.trim();
  if (!apiUrl || !model) {
    formStatus.textContent = "请填写接口地址和模型。";
    formStatus.className = "settings-form-status error";
    return;
  }

  saveButton.disabled = true;
  testButton.disabled = true;
  formStatus.textContent = testOnly ? "正在测试连接" : "正在测试并保存";
  formStatus.className = "settings-form-status";
  try {
    const payload = await requestJson("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        apiKey: $("#apiKeyInput").value.trim(),
        apiUrl,
        model,
        test: true,
        testOnly,
      }),
    });
    $("#apiKeyInput").value = "";
    $("#apiKeyMasked").textContent = payload.apiKeyMasked || "已配置";
    formStatus.textContent = testOnly ? "连接测试成功，尚未保存改动。" : "连接成功，配置已保存并立即共享。";
    formStatus.className = "settings-form-status ok";
    if (!testOnly) {
      showToast("AI 配置已保存，三个模块将立即使用新设置。");
      await refreshOverview({ silent: true });
    }
  } catch (error) {
    formStatus.textContent = error.message;
    formStatus.className = "settings-form-status error";
  } finally {
    saveButton.disabled = false;
    testButton.disabled = false;
  }
}

function bindEvents() {
  window.addEventListener("hashchange", () => renderRoute(routeFromHash()));
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.data?.type !== "workbench-module-ready") return;
    const frame = state.frames.get(event.data.module);
    if (frame) syncFrameTheme(frame);
  });

  $$("[data-route]").forEach((button) => {
    button.addEventListener("click", () => setRoute(button.dataset.route));
  });

  $$("[data-open-module]").forEach((button) => {
    button.addEventListener("click", () => setRoute(button.dataset.openModule, button.dataset.moduleAction || ""));
  });

  $("#backButton").addEventListener("click", () => setRoute("home"));
  $("#refreshButton").addEventListener("click", () => refreshOverview());
  $("#rebuildButton").addEventListener("click", startDatabaseRebuild);

  $("#reloadModuleButton").addEventListener("click", () => {
    const frame = state.frames.get(state.activeModule);
    if (!frame) return;
    frame.contentWindow.location.reload();
    showToast("正在重新载入工作区。");
  });

  $("#openWindowButton").addEventListener("click", () => {
    if (state.activeModule) window.open(`/modules/${state.activeModule}/`, "_blank", "noopener");
  });

  $("#settingsButton").addEventListener("click", () => openSettings("general"));
  $("#headerSettingsButton").addEventListener("click", () => openSettings("general"));
  $$('[data-open-settings]').forEach((button) => {
    button.addEventListener("click", () => openSettings(button.dataset.openSettings));
  });
  $$('[data-close-settings]').forEach((button) => button.addEventListener("click", closeSettings));
  $$(".settings-tab").forEach((button) => {
    button.addEventListener("click", async () => {
      setSettingsTab(button.dataset.settingsTab);
      if (button.dataset.settingsTab === "ai") await loadAiSettings();
    });
  });

  $("#settingsDialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeSettings();
  });

  $$('[data-theme-choice]').forEach((button) => {
    button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
  });

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (state.themePreference === "auto") applyTheme("auto");
  });

  $("#toggleSecretButton").addEventListener("click", () => {
    const input = $("#apiKeyInput");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $("#toggleSecretButton").textContent = show ? "隐藏" : "显示";
  });

  $("#aiSettingsForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitAiSettings({ testOnly: false });
  });
  $("#testConnectionButton").addEventListener("click", () => submitAiSettings({ testOnly: true }));
}

function initialize() {
  const today = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
  $("#todayDate").textContent = today;
  applyTheme(state.themePreference);
  bindEvents();
  if (!location.hash) history.replaceState(null, "", "#/home");
  renderRoute(routeFromHash());
  refreshOverview();
  setInterval(() => refreshOverview({ silent: true }), 20000);
}

initialize();
