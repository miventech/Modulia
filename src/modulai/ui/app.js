const today = new Date().toISOString().slice(0, 10);
const state = { health: null, commands: [], modules: [], memories: [], tasks: [], finance: { entries: [], summary: {} }, financeMonth: today.slice(0, 7), jobs: [], config: null, media: [], messageLog: [], mediaFilter: "all", showCompletedTasks: false, audioQueue: [], queuedPlayer: null };

const views = {
  chat: ["Conversación", document.querySelector("#chat-view")],
  modules: ["Módulos", document.querySelector("#modules-view")],
  memories: ["Memoria", document.querySelector("#memories-view")],
  tasks: ["Mis tareas", document.querySelector("#tasks-view")],
  finance: ["Finanzas", document.querySelector("#finance-view")],
  gallery: ["Galería multimedia", document.querySelector("#gallery-view")],
  log: ["Registro", document.querySelector("#log-view")],
  diagnostics: ["Diagnóstico", document.querySelector("#diagnostics-view")],
  config: ["Configuración", document.querySelector("#config-view")],
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const envelope = await response.json();
  if (!response.ok || envelope.error) {
    const detail = envelope.error?.details || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return envelope.data;
}

async function refresh() {
  try {
    const [health, commands, modules, memories, tasks, finance, jobs, config, media, messageLog] = await Promise.all([
      api("/api/v1/health"), api("/api/v1/commands"),
      api("/api/v1/modules"), api("/api/v1/memories"), api("/api/v1/tasks"), api(`/api/v1/finance?month=${encodeURIComponent(state.financeMonth)}`), api("/api/v1/jobs"), api("/api/v1/config"), api("/api/v1/media"), api("/api/v1/message-log?limit=200"),
    ]);
    Object.assign(state, { health, commands, modules, memories, tasks, finance, jobs, config, media, messageLog });
    render();
    document.querySelector("#status-dot").classList.add("online");
    document.querySelector("#status-text").textContent = "Núcleo conectado";
  } catch (error) {
    document.querySelector("#status-dot").classList.remove("online");
    document.querySelector("#status-text").textContent = "Sin conexión";
    console.error(error);
  }
}

function render() {
  document.querySelector("#module-count").textContent = state.modules.length;
  document.querySelector("#memory-count").textContent = state.memories.length;
  document.querySelector("#task-count").textContent = state.tasks.filter((task) => !task.completed).length;
  document.querySelector("#media-count").textContent = state.media.length;
  document.querySelector("#health-status").textContent = state.health?.status || "—";
  document.querySelector("#active-modules").textContent = state.health?.active_modules ?? "—";
  document.querySelector("#module-errors").textContent = state.health?.module_errors ?? "—";
  document.querySelector("#command-count").textContent = state.commands.length;
  renderModules();
  renderMemories();
  renderTasks();
  renderFinance();
  renderMedia();
  renderMessageLog();
  renderCommands();
  renderJobs();
  renderConfig();
}

function renderMessageLog() {
  const root = document.querySelector("#message-log-list");
  if (!root) return;
  root.replaceChildren();
  for (const item of state.messageLog) {
    const card = element("article", `message-log-item ${item.direction}`);
    card.append(element("strong", "", item.direction === "inbound" ? "Recibido" : "Enviado"), element("small", "", `${item.channel} · ${item.created_at}`), element("p", "", item.text));
    root.append(card);
  }
  if (!state.messageLog.length) root.append(empty("Aún no hay mensajes registrados."));
}

function renderMedia() {
  const root = document.querySelector("#media-list");
  root.replaceChildren();
  const items = state.media.filter((media) => state.mediaFilter === "all" || media.kind === state.mediaFilter);
  if (!items.length) return root.append(empty(state.media.length ? "No hay archivos para este filtro." : "Aún no hay archivos multimedia guardados."));
  for (const media of items) {
    const card = element("article", `media-card ${media.kind}`);
    const preview = document.createElement(media.kind === "image" ? "img" : media.kind === "video" ? "video" : "div");
    preview.className = "media-preview";
    if (media.kind === "image") {
      preview.src = media.url;
      preview.alt = media.name;
      preview.loading = "lazy";
    } else if (media.kind === "video") {
      preview.src = media.url;
      preview.controls = true;
      preview.preload = "metadata";
    } else if (media.kind === "audio") {
      preview.classList.add("audio-preview");
      preview.append(element("span", "media-symbol", "♫"));
    } else {
      preview.classList.add("text-preview");
      preview.append(element("span", "media-symbol", "≡"));
    }
    const content = element("div", "media-content");
    content.append(element("strong", "media-name", media.name), element("span", "media-meta", `${mediaLabel(media.kind)} · ${formatBytes(media.size_bytes)} · ${formatDate(media.modified_at)}`));
    if (media.kind === "audio") {
      const player = document.createElement("audio");
      player.src = media.url;
      player.controls = true;
      player.preload = "metadata";
      player.addEventListener("play", () => pauseOtherAudio(player));
      content.append(player);
    } else if (media.kind === "text") {
      const details = document.createElement("details");
      details.className = "transcript-details";
      const summary = element("summary", "", "Ver transcripción");
      const text = element("p", "transcript-text", media.text || "No se pudo leer la transcripción.");
      details.append(summary, text);
      if (media.text_truncated) details.append(element("small", "", "Vista previa limitada a 20 000 caracteres."));
      content.append(details);
    }
    const actions = element("div", "media-actions");
    const download = element("a", "artifact-link", "Descargar");
    download.href = media.url;
    download.download = media.name;
    const remove = element("button", "delete-button", "Eliminar");
    remove.addEventListener("click", () => deleteMedia(media));
    actions.append(download, remove);
    content.append(actions);
    card.append(preview, content);
    root.append(card);
  }
}

function mediaLabel(kind) { return ({ image: "Imagen", video: "Video", audio: "Música", text: "Transcripción" })[kind]; }
function formatBytes(bytes) { return new Intl.NumberFormat("es", { maximumFractionDigits: 1, style: "unit", unit: bytes < 1024 * 1024 ? "kilobyte" : "megabyte" }).format(bytes / (bytes < 1024 * 1024 ? 1024 : 1024 * 1024)); }
function pauseOtherAudio(current) {
  document.querySelectorAll("audio").forEach((player) => { if (player !== current) player.pause(); });
  if (state.queuedPlayer && state.queuedPlayer !== current) state.queuedPlayer.pause();
}

function playAllAudio() {
  state.audioQueue = state.media.filter((media) => media.kind === "audio");
  if (!state.audioQueue.length) return setPlayerStatus("No hay música disponible para reproducir.");
  state.queuedPlayer?.pause();
  playQueuedAudio(0);
}

function playQueuedAudio(index) {
  const media = state.audioQueue[index];
  if (!media) return setPlayerStatus("Terminó la cola de música.");
  const player = new Audio(media.url);
  state.queuedPlayer = player;
  pauseOtherAudio(player);
  setPlayerStatus(`Reproduciendo ${index + 1} de ${state.audioQueue.length}: ${media.name}`);
  player.addEventListener("ended", () => playQueuedAudio(index + 1), { once: true });
  player.addEventListener("error", () => playQueuedAudio(index + 1), { once: true });
  player.play().catch(() => setPlayerStatus("El navegador bloqueó la reproducción. Vuelve a pulsar el botón."));
}

function setPlayerStatus(text) { document.querySelector("#player-status").textContent = text; }

function renderModules() {
  const root = document.querySelector("#module-list");
  root.replaceChildren();
  if (!state.modules.length) return root.append(empty("No hay módulos instalados."));
  for (const module of state.modules) {
    const card = element("article", "module-card");
    const head = element("div", "module-head");
    const title = element("h3", "", module.name);
    const badge = element("span", `badge ${module.status === "error" ? "error" : ""}`, module.status);
    head.append(title, badge);
    card.append(head, element("p", "module-id", `${module.id} · v${module.version}`));
    const capabilities = element("div", "capabilities");
    for (const capability of module.capabilities) capabilities.append(element("span", "", capability));
    card.append(capabilities);
    if (Object.hasOwn(module.config, "enabled")) {
      const setting = element("label", "module-setting");
      const caption = element("span", "", "Habilitado al reiniciar");
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = module.config.enabled;
      toggle.addEventListener("change", () => saveModuleConfig(module, { ...module.config, enabled: toggle.checked }));
      setting.append(caption, toggle);
      card.append(setting);
    }
    const properties = module.config_schema?.properties || {};
    const editable = Object.entries(properties).filter(([key]) => key !== "enabled");
    if (editable.length) card.append(moduleConfigForm(module, editable));
    if (module.error) card.append(element("p", "module-error", module.error));
    root.append(card);
  }
}

function moduleConfigForm(module, editable) {
  const form = element("form", "module-config-form");
  const controls = [];
  for (const [key, definition] of editable) {
    const label = element("label", "", definition.description || key);
    let control;
    if (module.id === "local.opencode_ai" && key === "model") {
      control = document.createElement("select");
      const loading = document.createElement("option");
      loading.value = module.config[key] || "";
      loading.textContent = module.config[key] || "Cargando modelos…";
      control.append(loading);
      queueMicrotask(() => loadAiModels(control, module.config[key] || ""));
    } else if (Array.isArray(definition.enum)) {
      control = document.createElement("select");
      for (const optionValue of definition.enum) {
        const option = document.createElement("option");
        option.value = optionValue;
        option.textContent = optionValue;
        control.append(option);
      }
    } else if (definition.type === "boolean") {
      control = document.createElement("input");
      control.type = "checkbox";
      control.checked = module.config[key] ?? definition.default ?? false;
    } else {
      control = document.createElement("input");
      control.type = definition.type === "integer" ? "number" : "text";
      if (definition.type === "integer") control.step = "1";
    }
    if (definition.type !== "boolean") control.value = module.config[key] ?? definition.default ?? "";
    label.append(control);
    form.append(label);
    controls.push([key, definition, control]);
  }
  const save = element("button", "module-config-save", "Guardar opciones");
  save.type = "submit";
  form.append(save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const config = { ...module.config };
    for (const [key, definition, control] of controls) {
      config[key] = definition.type === "integer"
        ? Number(control.value)
        : definition.type === "boolean" ? control.checked : control.value;
    }
    await saveModuleConfig(module, config);
  });
  return form;
}

async function loadAiModels(select, current) {
  try {
    const models = await api("/api/v1/ai/models");
    select.replaceChildren();
    if (!models.length) {
      const emptyOption = document.createElement("option");
      emptyOption.value = current;
      emptyOption.textContent = current || "No hay modelos disponibles";
      select.append(emptyOption);
      return;
    }
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      option.selected = model === current;
      select.append(option);
    }
    if (current && !models.includes(current)) {
      const currentOption = document.createElement("option");
      currentOption.value = current;
      currentOption.textContent = `${current} (actual) `;
      currentOption.selected = true;
      select.append(currentOption);
    }
  } catch (error) {
    select.replaceChildren();
    const errorOption = document.createElement("option");
    errorOption.value = current;
    errorOption.textContent = current || "Activa el módulo para cargar modelos";
    select.append(errorOption);
    console.warn("No se pudieron cargar modelos de OpenCode", error);
  }
}

async function saveModuleConfig(module, config) {
  try {
    await api(`/api/v1/modules/${encodeURIComponent(module.id)}/config`, {
      method: "PUT",
      body: JSON.stringify(config),
    });
    module.config = config;
    addMessage("assistant", `Configuración de ${module.name} guardada. Reinicia ModulAI para aplicarla.`);
  } catch (error) {
    addMessage("assistant", `No pude guardar la configuración: ${error.message}`, true);
    await refresh();
  }
}

function renderMemories() {
  const root = document.querySelector("#memory-list");
  root.replaceChildren();
  if (!state.memories.length) return root.append(empty("Todavía no hay recuerdos guardados."));
  for (const memory of state.memories) {
    const card = element("article", "memory-card");
    const body = document.createElement("div");
    body.append(element("p", "", memory.text), element("small", "", `#${memory.id} · ${formatDate(memory.created_at)}`));
    const remove = element("button", "delete-button", "Eliminar");
    remove.addEventListener("click", () => deleteMemory(memory));
    card.append(body, remove);
    root.append(card);
  }
}

function renderTasks() {
  const root = document.querySelector("#task-list");
  root.replaceChildren();
  const tasks = state.tasks.filter((task) => state.showCompletedTasks || !task.completed);
  const categoryOptions = document.querySelector("#task-categories");
  categoryOptions.replaceChildren();
  for (const category of [...new Set(state.tasks.map((task) => task.category))].sort((a, b) => a.localeCompare(b, "es"))) {
    const option = document.createElement("option");
    option.value = category;
    categoryOptions.append(option);
  }
  if (!tasks.length) return root.append(empty(state.tasks.length ? "No hay tareas con este filtro." : "Tu tablero está vacío. Crea la primera nota."));
  const grouped = new Map();
  for (const task of tasks) {
    if (!grouped.has(task.category)) grouped.set(task.category, []);
    grouped.get(task.category).push(task);
  }
  for (const [category, categoryTasks] of grouped) {
    const section = element("section", "task-category-group");
    section.append(element("h3", "task-category-title", category));
    const grid = element("div", "sticky-grid");
    for (const task of categoryTasks) {
    const card = element("article", `sticky-note ${task.color} ${task.completed ? "completed" : ""}`);
    const header = element("div", "sticky-head");
    const importance = element("span", `importance ${task.importance}`, task.importance);
    const remove = element("button", "sticky-delete", "×");
    remove.title = "Eliminar nota";
    remove.addEventListener("click", () => deleteTask(task));
    header.append(importance, remove);
    const text = element("p", "sticky-text", task.text);
    const footer = element("div", "sticky-footer");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = task.completed;
    check.title = task.completed ? "Marcar como pendiente" : "Marcar como hecha";
    check.addEventListener("change", () => updateTask(task, { completed: check.checked }));
    const completeLabel = element("label", "sticky-check", task.completed ? "Hecha" : "Marcar hecha");
    completeLabel.prepend(check);
    const edit = element("button", "sticky-edit", "Editar");
    edit.addEventListener("click", () => editTask(task));
    footer.append(completeLabel, edit);
    card.append(header, text, footer);
      grid.append(card);
    }
    section.append(grid);
    root.append(section);
  }
}

function renderFinance() {
  const finance = state.finance || { entries: [], summary: {} };
  const summary = finance.summary || {};
  const currency = state.modules.find((module) => module.id === "local.finance")?.config?.currency || "PEN";
  document.querySelector("#finance-month").value = state.financeMonth;
  document.querySelector("#finance-currency").textContent = currency;
  const summaryRoot = document.querySelector("#finance-summary");
  summaryRoot.replaceChildren();
  for (const [label, key] of [["Ingresos", "income"], ["Gastos pagados", "expenses"], ["Gastos pendientes", "pending_expenses"], ["Facturas pagadas", "paid_invoices"], ["Facturas pendientes", "pending_invoices"], ["Balance", "balance"]]) {
    const card = element("article", "finance-metric");
    card.append(element("span", "", label), element("strong", "", formatMoney(summary[key] || "0.00", currency)));
    summaryRoot.append(card);
  }
  const root = document.querySelector("#finance-list");
  root.replaceChildren();
  if (!finance.entries.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "finance-empty";
    cell.textContent = "No hay movimientos en este mes.";
    row.append(cell);
    root.append(row);
    return;
  }
  for (const entry of finance.entries) {
    const row = document.createElement("tr");
    const type = { expense: "Gasto", income: "Ingreso", invoice: "Factura" }[entry.kind] || entry.kind;
    row.append(
      element("td", "", entry.occurred_on),
      element("td", `finance-kind ${entry.kind}`, type),
      element("td", "", entry.description),
      element("td", "", entry.category),
      element("td", `finance-amount ${entry.kind}`, formatMoney(entry.amount, currency)),
      element("td", "", entry.status === "pending" ? "Pendiente" : entry.status === "paid" ? "Pagada" : "Registrado"),
    );
    const actions = element("td", "finance-actions");
    if (entry.kind === "expense" || entry.kind === "invoice") {
      const checkLabel = element("label", "finance-paid-toggle", "Pagado");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = entry.paid;
      check.title = entry.paid ? "Marcar pendiente" : "Marcar pagado";
      check.addEventListener("change", () => updateFinance(entry, { status: check.checked ? "paid" : "pending" }));
      checkLabel.prepend(check);
      actions.append(checkLabel);
    }
    const edit = element("button", "", "Editar");
    edit.addEventListener("click", () => editFinance(entry));
    const remove = element("button", "danger-action", "Eliminar");
    remove.addEventListener("click", () => deleteFinance(entry));
    actions.append(edit, remove);
    row.append(actions);
    root.append(row);
  }
}

function formatMoney(amount, currency) { return `${currency} ${Number(amount).toFixed(2)}`; }

async function createFinance(event) {
  event.preventDefault();
  try {
    const recurring = document.querySelector("#finance-recurring").checked;
    const paid = document.querySelector("#finance-paid").checked;
    await api("/api/v1/finance", {
      method: "POST",
      body: JSON.stringify({
        kind: valueOf("finance-kind"), description: valueOf("finance-description"), amount: valueOf("finance-amount"),
        category: valueOf("finance-category") || "General", occurred_on: valueOf("finance-occurred-on"), due_on: valueOf("finance-due-on") || null,
        recurring, status: valueOf("finance-kind") === "income" ? "registered" : paid ? "paid" : "pending",
      }),
    });
    document.querySelector("#finance-form").reset();
    document.querySelector("#finance-category").value = "General";
    document.querySelector("#finance-occurred-on").value = today;
    document.querySelector("#finance-paid").checked = true;
    document.querySelector("#finance-recurring").checked = false;
    await refresh();
  } catch (error) {
    window.alert(`No pude registrar el movimiento: ${error.message}`);
  }
}

async function updateFinance(entry, changes) {
  try {
    await api(`/api/v1/finance/${entry.id}`, { method: "PUT", body: JSON.stringify(changes) });
    await refresh();
  } catch (error) {
    window.alert(`No pude actualizar el movimiento: ${error.message}`);
  }
}

function editFinance(entry) {
  const description = window.prompt("Descripción", entry.description);
  if (description === null || !description.trim()) return;
  const amount = window.prompt("Monto", entry.amount);
  if (amount === null || !amount.trim()) return;
  updateFinance(entry, { description: description.trim(), amount: amount.trim() });
}

async function deleteFinance(entry) {
  if (!window.confirm(`¿Eliminar ${entry.description} definitivamente?`)) return;
  try {
    await api(`/api/v1/finance/${entry.id}`, { method: "DELETE" });
    await refresh();
  } catch (error) {
    window.alert(`No pude eliminar el movimiento: ${error.message}`);
  }
}

async function createTask(event) {
  event.preventDefault();
  const text = valueOf("task-text");
  if (!text) return;
  try {
    await api("/api/v1/tasks", {
      method: "POST",
      body: JSON.stringify({ text, color: valueOf("task-color"), importance: valueOf("task-importance"), category: valueOf("task-category") || "General" }),
    });
    document.querySelector("#task-form").reset();
    document.querySelector("#task-importance").value = "media";
    document.querySelector("#task-category").value = "General";
    await refresh();
  } catch (error) {
    window.alert(`No pude crear la nota: ${error.message}`);
  }
}

async function updateTask(task, changes) {
  try {
    await api(`/api/v1/tasks/${task.id}`, { method: "PUT", body: JSON.stringify(changes) });
    await refresh();
  } catch (error) {
    window.alert(`No pude actualizar la nota: ${error.message}`);
    await refresh();
  }
}

function editTask(task) {
  const text = window.prompt("Editar nota", task.text);
  if (text === null || !text.trim() || text.trim() === task.text) return;
  updateTask(task, { text: text.trim() });
}

async function deleteTask(task) {
  if (!window.confirm("¿Eliminar esta nota definitivamente?")) return;
  try {
    await api(`/api/v1/tasks/${task.id}`, { method: "DELETE" });
    await refresh();
  } catch (error) {
    window.alert(`No pude eliminar la nota: ${error.message}`);
  }
}

function renderCommands() {
  const root = document.querySelector("#command-list");
  root.replaceChildren();
  for (const command of state.commands) {
    const row = element("div", "command-row");
    row.append(element("code", "", `/${command.name}`), element("span", "", command.description));
    root.append(row);
  }
}

function renderJobs() {
  const root = document.querySelector("#job-list");
  root.replaceChildren();
  if (!state.jobs.length) return root.append(empty("No hay trabajos en segundo plano."));
  for (const job of state.jobs.slice(0, 8)) {
    const row = element("div", "job-row");
    const summary = element("div", "job-summary");
    summary.append(element("strong", "", `${job.label} · ${job.id}`));
    summary.append(element("span", "", `${job.detail}${job.status === "running" ? ` · ${Math.round(job.progress)}%` : ""}`));
    if (job.result) summary.append(element("span", "", job.result));
    if (job.error) summary.append(element("span", "", job.error));
    row.append(summary);
    if (job.artifacts.length) {
      for (const artifact of job.artifacts) {
        const link = element("a", "artifact-link", "Descargar MP3");
        link.href = artifact;
        link.download = "";
        row.append(link);
      }
    } else if (["queued", "running"].includes(job.status)) {
      const cancel = element("button", "delete-button", "Cancelar");
      cancel.addEventListener("click", () => cancelJob(job));
      row.append(cancel);
    }
    root.append(row);
  }
}

function renderConfig() {
  if (!state.config) return;
  const values = state.config.values || {};
  const app = values.app || {};
  const modules = values.modules || {};
  const logging = values.logging || {};
  const web = values.web || {};
  const telegram = values.telegram || {};
  setValue("config-app-name", app.name || "");
  setValue("config-data-dir", app.data_dir || "data");
  setValue("config-module-paths", (modules.paths || []).join(", "));
  setValue("config-web-host", web.host || "127.0.0.1");
  setValue("config-web-port", web.port || 8765);
  setChecked("config-web-browser", web.open_browser !== false);
  setValue("config-log-level", logging.level || "INFO");
  setValue("config-log-file", logging.file_name || "modulai.log");
  setChecked("config-log-enabled", logging.file_enabled !== false);
  setChecked("config-tg-enabled", telegram.enabled === true);
  setValue("config-tg-token-env", telegram.token_env || "MODULAI_TELEGRAM_TOKEN");
  setValue("config-tg-password-env", telegram.activation_password_env || "MODULAI_TELEGRAM_ACTIVATION_PASSWORD");
  setValue("config-tg-timeout", telegram.activation_timeout_seconds || 300);
  setValue("config-tg-attempts", telegram.max_activation_attempts || 3);
  setValue("config-tg-network-timeout", telegram.network_timeout_seconds || 30);
  setValue("config-tg-users", (telegram.allowed_user_ids || []).join(", "));
  setChecked("config-tg-drop", telegram.drop_pending_updates === true);
  const stateLabel = document.querySelector("#config-state");
  stateLabel.textContent = state.created_from_example ? "Creado" : "Activo";
  document.querySelector("#config-note").textContent = state.created_from_example
    ? "No existía app.toml. Se creó una copia editable desde app.example.toml."
    : "Los cambios se guardan en app.toml y se aplican al reiniciar ModulAI.";
}

function readConfigForm() {
  return {
    app: { name: valueOf("config-app-name"), data_dir: valueOf("config-data-dir") },
    modules: { paths: listOf("config-module-paths") },
    logging: {
      level: valueOf("config-log-level"),
      file_enabled: checkedOf("config-log-enabled"),
      file_name: valueOf("config-log-file"),
    },
    web: {
      host: valueOf("config-web-host"),
      port: Number(valueOf("config-web-port")),
      open_browser: checkedOf("config-web-browser"),
    },
    telegram: {
      enabled: checkedOf("config-tg-enabled"),
      token_env: valueOf("config-tg-token-env"),
      activation_password_env: valueOf("config-tg-password-env"),
      activation_timeout_seconds: Number(valueOf("config-tg-timeout")),
      max_activation_attempts: Number(valueOf("config-tg-attempts")),
      network_timeout_seconds: Number(valueOf("config-tg-network-timeout")),
      allowed_user_ids: listOf("config-tg-users").filter((value) => value.length).map(Number),
      drop_pending_updates: checkedOf("config-tg-drop"),
    },
  };
}

function setValue(id, value) { document.querySelector(`#${id}`).value = value; }
function setChecked(id, value) { document.querySelector(`#${id}`).checked = value; }
function valueOf(id) { return document.querySelector(`#${id}`).value.trim(); }
function checkedOf(id) { return document.querySelector(`#${id}`).checked; }
function listOf(id) { return valueOf(id).split(",").map((value) => value.trim()).filter(Boolean); }

async function cancelJob(job) {
  try {
    await api(`/api/v1/jobs/${job.id}/cancel`, { method: "POST" });
    await refresh();
  } catch (error) {
    addMessage("assistant", `No pude cancelar el trabajo: ${error.message}`, true);
  }
}

async function deleteMemory(memory) {
  if (!window.confirm(`¿Eliminar el recuerdo #${memory.id}?`)) return;
  try {
    await api(`/api/v1/memories/${memory.id}`, { method: "DELETE" });
    await refresh();
  } catch (error) {
    addMessage("assistant", `No pude eliminar el recuerdo: ${error.message}`, true);
  }
}

async function deleteMedia(media) {
  if (!window.confirm(`¿Eliminar definitivamente “${media.name}”?`)) return;
  try {
    await api(`/api/v1/media/${encodeMediaPath(media.id)}`, { method: "DELETE" });
    if (state.queuedPlayer?.src.endsWith(encodeMediaPath(media.id))) state.queuedPlayer.pause();
    await refresh();
  } catch (error) {
    addMessage("assistant", `No pude eliminar el archivo: ${error.message}`, true);
  }
}

function encodeMediaPath(path) { return path.split("/").map(encodeURIComponent).join("/"); }

async function send(text) {
  const input = document.querySelector("#message-input");
  const button = document.querySelector("#send-button");
  addMessage("user", text);
  input.value = "";
  resizeInput(input);
  button.disabled = true;
  try {
    const result = await api("/api/v1/messages", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    addMessage("assistant", result.text, !result.ok);
    if (/^\/(no_olvidar|olvidar|audio|fb_video|video|playlist|pendiente|agenda|hecho|quitar_pendiente)\b/.test(text)) await refresh();
  } catch (error) {
    addMessage("assistant", `Error de comunicación: ${error.message}`, true);
  } finally {
    button.disabled = false;
    input.focus();
  }
}

async function transcribeSelectedAudio(file) {
  const button = document.querySelector("#send-button");
  button.disabled = true;
  addMessage("user", `Audio seleccionado: ${file.name}`);
  try {
    const duration = await readAudioDuration(file);
    const response = await fetch("/api/v1/transcriptions", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream", "X-ModulAI-Filename": file.name, "X-ModulAI-Audio-Duration": String(duration ?? "") },
      body: file,
    });
    const envelope = await response.json();
    if (!response.ok || envelope.error) throw new Error(envelope.error?.details || `HTTP ${response.status}`);
    addMessage("assistant", envelope.data.text, !envelope.data.ok);
    await refresh();
  } catch (error) {
    addMessage("assistant", `No pude cargar el audio: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

function readAudioDuration(file) {
  return new Promise((resolve) => {
    const audio = document.createElement("audio");
    audio.preload = "metadata";
    audio.onloadedmetadata = () => { URL.revokeObjectURL(audio.src); resolve(Number.isFinite(audio.duration) ? audio.duration : null); };
    audio.onerror = () => resolve(null);
    audio.src = URL.createObjectURL(file);
  });
}

function addMessage(role, text, isError = false) {
  const root = document.querySelector("#messages");
  const article = element("article", `message ${role} ${isError ? "error" : ""}`);
  if (role === "assistant") article.append(element("div", "avatar", "M"));
  const bubble = element("div", "bubble");
  bubble.append(element("span", "speaker", role === "user" ? "Tú" : "ModulAI"), element("p", "", text));
  article.append(bubble);
  root.append(article);
  article.scrollIntoView({ behavior: "smooth", block: "end" });
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function empty(text) { return element("div", "empty", text); }
function formatDate(value) { return new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function resizeInput(input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; }

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item, .view").forEach((node) => node.classList.remove("active"));
    button.classList.add("active");
    const [title, view] = views[button.dataset.view];
    view.classList.add("active");
    document.querySelector("#view-title").textContent = title;
  });
});

document.querySelector("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.querySelector("#message-input");
  const text = input.value.trim();
  if (text) send(text);
});

document.querySelector("#message-input").addEventListener("input", (event) => resizeInput(event.target));
document.querySelector("#audio-input").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) transcribeSelectedAudio(file);
  event.target.value = "";
});
document.querySelector("#message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    document.querySelector("#composer").requestSubmit();
  }
});
document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => send(button.dataset.command)));
document.querySelectorAll("[data-media-filter]").forEach((button) => button.addEventListener("click", () => {
  state.mediaFilter = button.dataset.mediaFilter;
  document.querySelectorAll("[data-media-filter]").forEach((filter) => filter.classList.toggle("active", filter === button));
  renderMedia();
}));
document.querySelector("#play-all-audio").addEventListener("click", playAllAudio);
document.querySelector("#task-form").addEventListener("submit", createTask);
document.querySelector("#finance-month").value = state.financeMonth;
document.querySelector("#finance-occurred-on").value = today;
document.querySelector("#finance-form").addEventListener("submit", createFinance);
document.querySelector("#finance-kind").addEventListener("change", (event) => {
  const isExpense = event.target.value === "expense";
  document.querySelector("#finance-recurring").disabled = !isExpense;
  if (!isExpense) document.querySelector("#finance-recurring").checked = false;
});
document.querySelector("#finance-recurring").addEventListener("change", (event) => {
  if (event.target.checked) document.querySelector("#finance-paid").checked = false;
});
document.querySelector("#finance-month").addEventListener("change", (event) => {
  if (event.target.value) {
    state.financeMonth = event.target.value;
    refresh();
  }
});
document.querySelector("#show-completed-tasks").addEventListener("change", (event) => {
  state.showCompletedTasks = event.target.checked;
  renderTasks();
});
document.querySelector("#refresh").addEventListener("click", refresh);
document.querySelector("#refresh-log").addEventListener("click", refresh);
document.querySelector("#config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#config-message");
  message.textContent = "Guardando…";
  try {
    const saved = await api("/api/v1/config", { method: "PUT", body: JSON.stringify(readConfigForm()) });
    message.textContent = saved.message;
    message.className = "success-text";
    await refresh();
  } catch (error) {
    message.textContent = `No se pudo guardar: ${error.message}`;
    message.className = "error-text";
  }
});

refresh();
window.setInterval(() => {
  if (state.jobs.some((job) => ["queued", "running"].includes(job.status))) refresh();
}, 2000);
