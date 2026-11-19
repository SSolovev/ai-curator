import { renderResult } from "./result.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const generationStorageKey = "curator_generation_id";
const pollIntervalMs = 3000;
const maxPollRetries = 10;
const runtimeNames = {
  llama_cpp: "llama.cpp (Docker)",
  ollama: "Ollama (Native)",
  external: "Внешняя LLM",
};

let csrfToken = "";
let currentResult = null;
let currentGeneration = null;
let currentRuntime = "llama_cpp";
let employeePage = 1;

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        ...options.headers,
      },
    });
  } catch (cause) {
    const error = new Error("Не удалось связаться с приложением.", { cause });
    error.code = "NETWORK_ERROR";
    throw error;
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = new Error(body?.error?.message || "Ошибка запроса.");
    error.code = body?.error?.code;
    throw error;
  }
  return response;
}

async function api(path, options) {
  const response = await request(path, options);
  return response.json();
}

function showNotice(text = "", isError = false) {
  const notice = $("#notice");
  notice.textContent = text;
  notice.classList.toggle("hidden", !text);
  notice.classList.toggle("error", isError);
  if (text) notice.scrollIntoView({ behavior: "smooth", block: "center" });
}

function showResultNotice(text = "") {
  const notice = $("#generation-result-notice");
  notice.textContent = text;
  notice.classList.toggle("hidden", !text);
}

function showPanel(id) {
  $$(".panel:not(.diagnostics)").forEach((panel) => {
    panel.classList.toggle("active", panel.id === id);
  });
  $$(".step").forEach((step) => {
    step.classList.toggle("active", step.dataset.target === id);
  });
}

const jiraSources = {
  corporate: {
    url: "https://project.gpb.tech/jira",
    hint: "Используется рабочая Jira по адресу https://project.gpb.tech/jira.",
    tokenPlaceholder: "Оставьте пустым для PAT из secret-файла",
  },
  demo: {
    url: "http://mock-jira:9000",
    hint: "Демо Jira содержит только вымышленные данные. Демо-токен подставится автоматически.",
    tokenPlaceholder: "Для демо PAT не требуется",
  },
  custom: {
    url: "",
    hint: "Введите полный адрес своей Jira без /rest/api/... и укажите PAT.",
    tokenPlaceholder: "Введите PAT или оставьте пустым для secret-файла",
  },
};

function selectJiraSource() {
  const mode = $("#jira-source").value;
  const source = jiraSources[mode];
  const url = $("#jira-base-url");
  const token = $("#connect-form").elements.token;
  url.value = source.url;
  url.readOnly = mode !== "custom";
  url.placeholder = mode === "custom" ? "https://jira.company.local" : "";
  token.value = "";
  token.disabled = mode === "demo";
  token.placeholder = source.tokenPlaceholder;
  $("#jira-source-hint").textContent = source.hint;
  if (mode === "custom") url.focus();
}

async function connectToJira(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const demo = $("#jira-source").value === "demo";
  const fields = new FormData(form);
  const body = {
    jira_base_url: fields.get("jira_base_url"),
    token: demo ? "demo-token" : fields.get("token") || "",
    ca_bundle_path: fields.get("ca_bundle_path") || null,
    proxy_url: fields.get("proxy_url") || null,
  };

  await api("/api/connect", { method: "POST", body: JSON.stringify(body) });
  form.elements.token.value = "";
  showNotice(demo
    ? "Демо Jira подключена. Все данные в ней вымышлены."
    : "Подключение к Jira проверено. Токен хранится только в памяти сессии.");
  showPanel("people");
  await searchEmployees(1);
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function searchEmployees(page) {
  const params = new URLSearchParams({
    q: $("#employee-search").value,
    billable_only: $("#billable").checked,
    page,
    page_size: 20,
  });
  const result = await api(`/api/employees?${params}`);
  employeePage = result.page;
  $("#employees-page").textContent = `Страница ${result.page} · найдено ${result.total}`;
  $("#employees-prev").disabled = employeePage <= 1;
  $("#employees-next").disabled = !result.has_next;

  const cards = result.items.map((employee) => {
    const card = element("div", null, "employee-card");
    const details = element("div");
    details.append(
      element("b", employee.summary || "Без названия"),
      element("br"),
      element("small", `${employee.key} · ${employee.status || "Статус не указан"}`),
    );
    const button = element("button", "Выбрать");
    button.addEventListener("click", reportErrors(() => loadEmployee(employee.key)));
    card.append(details, button);
    return card;
  });
  if (!cards.length) cards.push(element("p", "Доступные Employee-задачи не найдены."));
  $("#employees").replaceChildren(...cards);
}

async function loadEmployee(key) {
  const context = await api(`/api/employees/${encodeURIComponent(key)}/load`, { method: "POST" });
  renderContext(context);
  showPanel("preview");
  showNotice("Контекст загружен с использованием настроенного сопоставления полей.");
}

async function restoreContext() {
  try {
    renderContext(await api("/api/context"));
  } catch (error) {
    $("#context-summary").textContent = error.message;
    $("#records").replaceChildren(element("p",
      "Серверный контекст недоступен. Если генерация ещё выполняется, дождитесь итогового статуса."));
  }
}

function renderRecord(item, kind) {
  const record = element("div", null, "record");
  const label = element("label");
  const checkbox = element("input", null, "record-check");
  checkbox.type = "checkbox";
  checkbox.checked = true;
  checkbox.value = item.source_ref;
  const date = item.date || "дата не указана";
  const type = item.type || item.period || "";
  label.append(checkbox, element("b", `${kind} · ${date} · ${type}`));

  const scores = Object.entries(item.scores || {})
    .filter(([, score]) => score != null)
    .map(([metric, score]) => `${metric}: ${score}`)
    .join(", ");
  const text = item.text || item.comment || scores || "Без текстового комментария";
  record.append(label, element("p", text), element("small", item.source_ref));
  return record;
}

function renderContext(context) {
  const { employee, context_selection: selection } = context;
  const summary = element("p");
  summary.append(
    element("b", employee.jira_key),
    ` · ${employee.role || "роль не сопоставлена"} · ${employee.status || "статус не указан"}`,
  );
  $("#context-summary").replaceChildren(
    summary,
    ...selection.warnings.map((warning) => element("p", warning, "warning")),
  );

  const groups = [
    [context.mood_monitoring, "Mood Monitoring"],
    [context.incomplete_mood_monitoring, "Незавершённый MM"],
    [context.interactions, "Взаимодействие"],
    [context.feedback, "Feedback"],
  ];
  const records = groups.flatMap(([items, kind]) => items.map((item) => renderRecord(item, kind)));
  if (!records.length) records.push(element("p", "Содержательных записей для анализа нет."));
  $("#records").replaceChildren(...records);
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pollGeneration(generationId, updateElapsed) {
  const path = `/api/generate/${generationId}`;
  let networkFailures = 0;
  while (currentGeneration === generationId) {
    try {
      const job = await api(`${path}/status`);
      updateElapsed(job.elapsed_seconds);
      if (job.status === "succeeded") {
        const completed = await api(`${path}/result`);
        return { result: completed.result, elapsedSeconds: job.elapsed_seconds };
      }
      if (job.status !== "running") {
        const error = new Error(job.error?.message || "Не удалось подготовить сценарий.");
        error.code = job.error?.code;
        error.elapsedSeconds = job.elapsed_seconds;
        throw error;
      }
      networkFailures = 0;
    } catch (error) {
      if (error.code !== "NETWORK_ERROR" || networkFailures >= maxPollRetries) throw error;
      networkFailures += 1;
    }
    await wait(pollIntervalMs);
  }

  throw new Error("Отслеживание генерации завершено.");
}

function setGenerating(isGenerating) {
  $("#generate").disabled = isGenerating;
  $("#cancel").classList.toggle("hidden", !isGenerating);
}

async function trackGeneration(generationId, initialElapsedSeconds = 0) {
  currentGeneration = generationId;
  sessionStorage.setItem(generationStorageKey, generationId);
  const runtime = runtimeNames[currentRuntime];

  let serverElapsedSeconds = Number(initialElapsedSeconds) || 0;
  let lastSync = Date.now();
  const elapsedSeconds = () => serverElapsedSeconds + (Date.now() - lastSync) / 1000;
  const progressText = () => `${runtime} готовит сценарий… ${Math.floor(elapsedSeconds())} с`;
  const renderProgress = () => { $("#notice").textContent = progressText(); };

  setGenerating(true);
  showNotice(progressText());
  const ticker = setInterval(renderProgress, 1000);
  try {
    const completed = await pollGeneration(generationId, (seconds) => {
      serverElapsedSeconds = Number(seconds) || 0;
      lastSync = Date.now();
      renderProgress();
    });
    renderResult(completed.result);
    currentResult = completed.result;
    showPanel("result");
    showNotice();
    showResultNotice(`Сценарий готов за ${completed.elapsedSeconds.toFixed(1)} с. Проверьте вывод и раскройте источники.`);
  } catch (error) {
    if (error.code === "GENERATION_NOT_FOUND") sessionStorage.removeItem(generationStorageKey);
    const duration = error.elapsedSeconds ?? elapsedSeconds();
    const status = error.code === "GENERATION_CANCELLED" ? "Прошло" : "До остановки прошло";
    showNotice(`${error.message} ${status} ${duration.toFixed(1)} с.`, true);
  } finally {
    clearInterval(ticker);
    currentGeneration = null;
    setGenerating(false);
  }
}

async function generate() {
  const excludedRefs = $$(".record-check:not(:checked)").map((checkbox) => checkbox.value);
  showResultNotice();
  $("#generate").disabled = true;
  showNotice(`${runtimeNames[currentRuntime]} готовит сценарий…`);
  try {
    const job = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({ excluded_source_refs: excludedRefs }),
    });
    await trackGeneration(job.generation_id, job.elapsed_seconds);
  } catch (error) {
    showNotice(`${error.message} Формирование не запущено.`, true);
  } finally {
    setGenerating(false);
  }
}

async function cancelGeneration() {
  if (!currentGeneration) return;
  const job = await api(`/api/generate/${currentGeneration}/cancel`, { method: "POST" });
  if (job.cancelled) showNotice("Отмена генерации…");
}

function showDiagnosticStatus(selector, message, state) {
  const status = $(selector);
  status.textContent = message;
  status.className = state ? `model-status ${state}` : "model-status";
}

function showPrivacyDiagnostics(data) {
  $("#privacy-filter-model").textContent = data?.model || "Нет данных";
  const ok = data?.health?.ok;
  showDiagnosticStatus("#privacy-filter-status", ok ? "Готова" : "Недоступна", ok ? "ok" : "error");
}

async function refreshPrivacyDiagnostics(showDetails = true) {
  showDiagnosticStatus("#privacy-filter-status", "Проверка…");
  try {
    const data = await api("/api/model/privacy-filter/diagnostics");
    showPrivacyDiagnostics(data);
    if (showDetails) $("#diagnostic-result").textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    showDiagnosticStatus("#privacy-filter-status", "Диагностика недоступна", "error");
    if ($("#privacy-filter-model").textContent === "Проверка…") {
      $("#privacy-filter-model").textContent = "Нет данных";
    }
    if (showDetails) $("#diagnostic-result").textContent = error.message;
  }
}

async function refreshModelDiagnostics(showDetails = true) {
  try {
    const data = await api("/api/model/diagnostics");
    currentRuntime = data.runtime;
    const external = data.runtime === "external";
    $("#model-runtime").textContent = data.runtime_label;
    $("#privacy").textContent = external
      ? "Контекст обезличивается локально перед отправкой во внешнюю LLM"
      : "Контекст обезличивается локально перед основной моделью";
    $("#model-context-title").textContent = external
      ? "Что будет обезличено перед внешней моделью"
      : "Что будет обезличено перед локальной моделью";
    const ok = data.health?.ok;
    showDiagnosticStatus(
      "#model-status",
      `${ok ? "Готова" : "Недоступна"} · ${data.configured_model}`,
      ok ? "ok" : "error",
    );
    showPrivacyDiagnostics(data.privacy_filter);
    if (showDetails) $("#diagnostic-result").textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    showDiagnosticStatus("#model-status", "Диагностика недоступна", "error");
    if (!showDetails) await refreshPrivacyDiagnostics(false);
    if (showDetails) $("#diagnostic-result").textContent = error.message;
  }
}

async function copyResult() {
  const field = $("#editable-result");
  try {
    await navigator.clipboard.writeText(field.value);
  } catch {
    field.focus();
    field.select();
    if (!document.execCommand("copy")) {
      showNotice("Не удалось скопировать текст. Выделите его и скопируйте вручную.", true);
      return;
    }
  }
  showNotice("Отредактированный текст скопирован.");
}

function downloadBlob(blob, filename) {
  const link = element("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function downloadResult(format) {
  if (!currentResult) return;
  if (format === "markdown") {
    const text = $("#editable-result").value;
    downloadBlob(new Blob([text], { type: "text/markdown;charset=utf-8" }), "one-on-one.md");
    showNotice("Отредактированный сценарий сохранён в Markdown.");
    return;
  }

  const response = await request("/api/export", {
    method: "POST",
    body: JSON.stringify({ format: "json", result: currentResult }),
  });
  downloadBlob(await response.blob(), "one-on-one.json");
}

async function forgetSession() {
  await api("/api/session", { method: "DELETE" });
  sessionStorage.removeItem(generationStorageKey);
  location.reload();
}

function reportErrors(action) {
  return async (...args) => {
    try {
      await action(...args);
    } catch (error) {
      showNotice(error.message, true);
    }
  };
}

function onClick(selector, action) {
  $(selector).addEventListener("click", reportErrors(action));
}

async function initialize() {
  selectJiraSource();
  csrfToken = (await api("/api/session")).csrf_token;
  $$(".step").forEach((button) => {
    button.addEventListener("click", () => showPanel(button.dataset.target));
  });
  $("#jira-source").addEventListener("change", selectJiraSource);
  $("#connect-form").addEventListener("submit", reportErrors(connectToJira));
  onClick("#search-button", () => searchEmployees(1));
  onClick("#employees-prev", () => searchEmployees(employeePage - 1));
  onClick("#employees-next", () => searchEmployees(employeePage + 1));
  onClick("#load-key", () => loadEmployee($("#employee-key").value));
  onClick("#generate", generate);
  onClick("#cancel", cancelGeneration);
  onClick("#diagnostics", () => refreshModelDiagnostics());
  onClick("#privacy-diagnostics", () => refreshPrivacyDiagnostics());
  onClick("#copy", copyResult);
  onClick("#forget", forgetSession);
  $$("[data-export]").forEach((button) => {
    button.addEventListener("click", reportErrors(() => downloadResult(button.dataset.export)));
  });

  const pending = sessionStorage.getItem(generationStorageKey);
  if (pending) $("#generate").disabled = true;
  await refreshModelDiagnostics(false);
  if (pending) {
    showPanel("preview");
    await restoreContext();
    await trackGeneration(pending);
  }
}

void reportErrors(initialize)();
