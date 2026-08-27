"use strict";

const API_BASE = "http://127.0.0.1:1236";
const POLL_FAST = 1000;
const POLL_SLOW = 3000;
const LOG_N = 200;

const els = {
  list: document.getElementById("service-list"),
  empty: document.getElementById("empty-state"),
  badge: document.getElementById("conn-badge"),
  toasts: document.getElementById("toast-container"),
};

// 每个服务的"稳定布局"模板：用于过渡态(stopped/starting 等)复用按钮结构
// key = 进入过渡态前的"锚定稳定态"
const stableLayout = new Map();
// 记录哪些行的日志面板处于展开状态
const openLogs = new Set();
// 记录每个展开行日志面板是否贴底（用于自动滚底判定）
const logAtBottom = new Map();

let pollTimer = null;
let connected = true;
// 全局日志面板拖拽状态
let dragState = null;

function fmtUptime(sec) {
  if (sec === null || sec === undefined) return null;
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return h + "h" + String(m).padStart(2, "0") + "m";
  if (m > 0) return m + "m" + String(s).padStart(2, "0") + "s";
  return s + "s";
}

function statusDotClass(status) {
  return "dot dot-" + (status || "stopped");
}

// 稳定态的按钮布局（提交类按钮）；invalid 返回空
function stableButtons(status) {
  switch (status) {
    case "stopped":
    case "failed":
    case "invalid": // invalid 也会产出"空布局"，仅用于锚定，后续被清空
      return status === "invalid" ? [] : [{ label: "启动", action: "start" }];
    case "running":
      return [{ label: "重启", action: "restart" }, { label: "停止", action: "stop" }];
    default:
      return [];
  }
}

// 返回该服务当前真正要渲染的按钮集合（含过渡态复用逻辑）
function buttonsFor(svc) {
  const status = svc.status || "stopped";
  if (status === "starting" || status === "stopping") {
    // 复用进入过渡态前的稳定布局
    const anchor = stableLayout.get(svc.name);
    const base = anchor ? stableButtons(anchor) : stableButtons("stopped");
    return base.map((b) => ({ label: b.label, action: b.action, busy: true }));
  }
  return stableButtons(status).map((b) => ({ label: b.label, action: b.action, busy: false }));
}

function cssEscape(s) {
  return String(s).replace(/(["\\])/g, "\\$1");
}

function toast(message, kind) {
  const t = document.createElement("div");
  t.className = "toast " + (kind === "err" ? "err" : "ok");
  t.textContent = message;
  els.toasts.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => {
    t.classList.remove("show");
    setTimeout(() => t.remove(), 320);
  }, 3000);
}

async function apiFetch(url, opts) {
  try {
    const res = await fetch(url, opts);
    if (!connected) { connected = true; els.badge.hidden = true; }
    return res;
  } catch (e) {
    if (connected) { connected = false; els.badge.hidden = false; }
    throw e;
  }
}

async function postAction(name, action) {
  try {
    const res = await apiFetch(API_BASE + "/api/services/" + encodeURIComponent(name) + "/" + action, { method: "POST" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    await res.json().catch(() => ({}));
    toast(action === "start" ? "已发送启动请求" : action === "stop" ? "已发送停止请求" : "已发送重启请求", "ok");
    schedulePoll(true); // 尽快反映状态变化
    return true;
  } catch (e) {
    toast("操作失败：" + e.message, "err");
    return false;
  }
}

async function postAutostart(name, value) {
  try {
    const res = await apiFetch(API_BASE + "/api/config/" + encodeURIComponent(name), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ autostart: value }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    await res.json().catch(() => ({}));
    return true;
  } catch (e) {
    toast("保存自启动失败：" + e.message, "err");
    return false;
  }
}

async function fetchServices() {
  const res = await apiFetch(API_BASE + "/api/services");
  if (!res.ok) throw new Error("HTTP " + res.status);
  const data = await res.json();
  return Array.isArray(data.services) ? data.services : [];
}

async function fetchLogs(name) {
  const res = await apiFetch(API_BASE + "/api/logs/" + encodeURIComponent(name) + "?n=" + LOG_N);
  if (!res.ok) throw new Error("HTTP " + res.status);
  const data = await res.json();
  return Array.isArray(data.lines) ? data.lines : [];
}

function buildButton(def, svc) {
  const b = document.createElement("button");
  b.className = "btn";
  b.dataset.action = def.action;
  b.disabled = !!def.busy;
  if (def.busy) {
    const spin = document.createElement("span");
    spin.className = "spin";
    b.appendChild(spin);
    b.appendChild(document.createTextNode(" " + def.label));
  } else {
    b.textContent = def.label;
    b.addEventListener("click", () => postAction(svc.name, def.action));
  }
  return b;
}

// 更新按钮内容（busy 切换时只改内部，不换 DOM 节点）
function setButtonDef(btn, def) {
  if (def.busy) {
    btn.disabled = true;
    if (!btn.querySelector(".spin")) {
      btn.textContent = "";
      const spin = document.createElement("span");
      spin.className = "spin";
      btn.appendChild(spin);
      btn.appendChild(document.createTextNode(" " + def.label));
    }
  } else {
    btn.disabled = false;
    if (btn.querySelector(".spin")) {
      btn.textContent = def.label;
    } else if (btn.textContent !== def.label) {
      btn.textContent = def.label;
    }
  }
}

function renderCard(svc, card) {
  const status = svc.status || "stopped";
  const uptime = fmtUptime(svc.uptime_sec);
  const focusedBtn = card.querySelector("button.btn:focus");
  const focusedAction = focusedBtn ? focusedBtn.dataset.action : null;

  // 记录稳定布局锚点
  if (status === "stopped" || status === "failed" || status === "running") {
    stableLayout.set(svc.name, status);
  }

  // ---- 左侧主区 ----
  let main = card.querySelector(".card-main");
  if (!main) {
    main = document.createElement("div");
    main.className = "card-main";
    card.insertBefore(main, card.firstChild);
  }
  main.innerHTML = "";
  const dot = document.createElement("span");
  dot.className = statusDotClass(status);
  main.appendChild(dot);

  const titles = document.createElement("span");
  titles.className = "svc-titles";
  const nameEl = document.createElement("span");
  nameEl.className = "svc-name";
  nameEl.textContent = svc.display || svc.name;
  const keyEl = document.createElement("span");
  keyEl.className = "svc-key";
  keyEl.textContent = svc.name;
  titles.appendChild(nameEl);
  titles.appendChild(keyEl);
  main.appendChild(titles);

  if (svc.port) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = "端口 " + svc.port;
    main.appendChild(chip);
  }
  if (uptime) {
    const up = document.createElement("span");
    up.className = "uptime";
    up.textContent = uptime;
    main.appendChild(up);
  }
  // 描述 tooltip
  const desc = svc.description || "";
  if (desc) {
    const dEl = document.createElement("span");
    dEl.className = "chip";
    dEl.title = desc;
    dEl.style.cursor = "help";
    dEl.textContent = "说明";
    main.appendChild(dEl);
  }
  // 失败原因
  if (svc.error) {
    const errEl = document.createElement("span");
    errEl.className = "chip";
    errEl.title = svc.error;
    errEl.style.color = "var(--red)";
    errEl.style.borderColor = "var(--red)";
    errEl.textContent = svc.error;
    main.appendChild(errEl);
  }

  // ---- 右侧操作区 ----
  let actions = card.querySelector(".card-actions");
  if (!actions) {
    actions = document.createElement("div");
    actions.className = "card-actions";
    card.appendChild(actions);
  }

  // 自启动开关（始终保留）
  let autoWrap = actions.querySelector(".autostart");
  if (!autoWrap) {
    autoWrap = document.createElement("label");
    autoWrap.className = "autostart";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.dataset.role = "autostart";
    const span = document.createElement("span");
    span.textContent = "自启动";
    autoWrap.appendChild(cb);
    autoWrap.appendChild(span);
    actions.appendChild(autoWrap);
    cb.addEventListener("change", async () => {
      const newVal = cb.checked;
      const ok = await postAutostart(svc.name, newVal);
      if (!ok) { cb.checked = !newVal; toast("自启动设置已回滚", "err"); }
      else { toast("已保存自启动设置", "ok"); }
    });
  }
  autoWrap.querySelector("input").checked = !!svc.autostart;

  // 提交类按钮分组
  const wanted = buttonsFor(svc);
  let group = actions.querySelector(".btn-group");
  if (!group) {
    group = document.createElement("div");
    group.className = "btn-group";
    actions.appendChild(group);
  }

  if (focusedAction) {
    // 不打断交互：仅就地更新已有按钮，数量不一致才重建
    const existing = Array.from(group.querySelectorAll("button.btn"));
    // 先尝试按 action 配对更新
    const defByAction = {};
    wanted.forEach((d) => (defByAction[d.action] = d));
    let matched = 0;
    existing.forEach((b) => {
      const act = b.dataset.action;
      if (defByAction[act]) { setButtonDef(b, defByAction[act]); matched++; }
    });
    if (matched === wanted.length && existing.length === wanted.length) {
      // 完全一致，无需重建
    } else {
      group.innerHTML = "";
      wanted.forEach((d) => group.appendChild(buildButton(d, svc)));
    }
  } else {
    group.innerHTML = "";
    wanted.forEach((d) => group.appendChild(buildButton(d, svc)));
  }

  // 日志按钮（始终保留，不受 focus 影响）
  let logBtn = group.querySelector("button.btn-log");
  if (!logBtn) {
    logBtn = document.createElement("button");
    logBtn.className = "btn btn-log";
    logBtn.dataset.action = "logs";
    logBtn.addEventListener("click", () => toggleLog(svc.name, card));
    group.appendChild(logBtn);
  }
  const isOpen = openLogs.has(svc.name);
  logBtn.classList.toggle("active", isOpen);
  logBtn.textContent = isOpen ? "收起" : "日志";
}

function toggleLog(name, card) {
  if (openLogs.has(name)) {
    openLogs.delete(name);
    logAtBottom.delete(name);
    const p = card.querySelector(".log-panel");
    if (p) p.remove();
  } else {
    openLogs.add(name);
    logAtBottom.set(name, true);
    const panel = document.createElement("div");
    panel.className = "log-panel";
    panel.dataset.role = "logpanel";
    card.appendChild(panel);
    bindLogDrag(panel);
    bindLogScroll(name, panel);
    refreshLog(name, panel);
  }
  const btn = card.querySelector("button.btn-log");
  if (btn) {
    const isOpen = openLogs.has(name);
    btn.classList.toggle("active", isOpen);
    btn.textContent = isOpen ? "收起" : "日志";
  }
}

function bindLogDrag(panel) {
  panel.addEventListener("mousedown", (e) => {
    if (panel.scrollWidth - panel.clientWidth > 4) {
      dragState = { panel, startX: e.clientX, startLeft: panel.scrollLeft };
      panel.classList.add("dragging");
      e.preventDefault();
    }
  });
}

window.addEventListener("mousemove", (e) => {
  if (!dragState) return;
  dragState.panel.scrollLeft = dragState.startLeft - (e.clientX - dragState.startX);
});
window.addEventListener("mouseup", () => {
  if (dragState) { dragState.panel.classList.remove("dragging"); dragState = null; }
});

function bindLogScroll(name, panel) {
  panel.addEventListener("scroll", () => {
    const atBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 4;
    logAtBottom.set(name, atBottom);
  }, { passive: true });
}

async function refreshLog(name, panel) {
  if (!panel || !openLogs.has(name)) return;
  const wasBottom = logAtBottom.get(name) !== false;
  try {
    const lines = await fetchLogs(name);
    panel.innerHTML = "";
    if (!lines.length) {
      const empty = document.createElement("div");
      empty.className = "log-empty";
      empty.textContent = "（暂无日志）";
      panel.appendChild(empty);
    } else {
      const frag = document.createDocumentFragment();
      lines.forEach((ln) => {
        const div = document.createElement("div");
        div.className = "log-line";
        div.textContent = ln;
        frag.appendChild(div);
      });
      panel.appendChild(frag);
    }
    if (wasBottom) panel.scrollTop = panel.scrollHeight;
    const atBottomNow = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 4;
    logAtBottom.set(name, atBottomNow);
  } catch (e) {
    // 网络错误仅保留面板，连接徽标由 apiFetch 处理
  }
}

async function render(data) {
  const names = data.map((s) => s.name);
  els.empty.hidden = data.length > 0;
  els.list.hidden = data.length === 0;

  // 移除已不存在的服务卡片
  Array.from(els.list.children).forEach((card) => {
    const nm = card.dataset.name;
    if (nm && !names.includes(nm)) {
      if (openLogs.has(nm)) { openLogs.delete(nm); logAtBottom.delete(nm); }
      stableLayout.delete(nm);
      card.remove();
    }
  });

  data.forEach((svc) => {
    let card = els.list.querySelector('.card[data-name="' + cssEscape(svc.name) + '"]');
    if (!card) {
      card = document.createElement("div");
      card.className = "card";
      card.dataset.name = svc.name;
      els.list.appendChild(card);
    }
    renderCard(svc, card);
  });

  // 刷新所有展开的日志面板（跟随轮询）
  openLogs.forEach((name) => {
    const card = els.list.querySelector('.card[data-name="' + cssEscape(name) + '"]');
    if (!card) return;
    const panel = card.querySelector(".log-panel");
    if (panel) refreshLog(name, panel);
  });

  schedulePoll(false);
}

async function tick() {
  if (document.hidden) return;
  try {
    const data = await fetchServices();
    await render(data);
  } catch (e) {
    // 连接错误由 apiFetch 处理徽标
  }
}

function schedulePoll(forceFast) {
  if (pollTimer) clearTimeout(pollTimer);
  let fast = forceFast;
  if (!fast && els.list) {
    fast = els.list.querySelector(".dot-starting, .dot-stopping") !== null;
  }
  pollTimer = setTimeout(tick, fast ? POLL_FAST : POLL_SLOW);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) schedulePoll(true);
});

(async function init() {
  try {
    const data = await fetchServices();
    await render(data);
  } catch (e) {
    schedulePoll(false);
  }
})();