(function () {
  "use strict";

  // ===== 模块常量 =====
  var API_BASE = "http://127.0.0.1:1236";
  var POLL_FAST = 1000;
  var POLL_SLOW = 3000;
  var LOG_N = 200;

  // 中文字义映射表（状态 -> 样式类 + 文案）
  var STATUS_MAP = {
    running:  { cls: "running",  label: "运行中" },
    stopped:  { cls: "stopped",  label: "已停止" },
    starting: { cls: "starting", label: "启动中" },
    stopping: { cls: "stopping", label: "停止中" },
    failed:   { cls: "failed",   label: "失败" },
    invalid:  { cls: "invalid",  label: "清单无效" }
  };

  var els = {
    list: document.getElementById("service-list"),
    empty: document.getElementById("empty-state"),
    dot: document.getElementById("conn-dot"),
    text: document.getElementById("conn-text"),
    badge: document.getElementById("conn-badge"),
    toasts: document.getElementById("toast-container"),
    refresh: document.getElementById("btn-refresh")
  };

  // 状态：同一时刻只允许一张卡片展开日志
  var openLogs = new Set();
  var logAtBottom = new Map();   // name -> 是否贴底
  var stableLayout = new Map();  // name -> 过渡态锚定稳定态
  var pollTimer = null;
  var connected = true;
  var dragState = null;

  // ===== 工具函数 =====
  function fmtUptime(sec) {
    if (sec === null || sec === undefined) return null;
    sec = Math.floor(sec);
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    if (h > 0) return h + "h" + String(m).padStart(2, "0") + "m";
    if (m > 0) return m + "m" + String(s).padStart(2, "0") + "s";
    return s + "s";
  }

  function safeUrl(u) {
    return (typeof u === "string" && /^https?:\/\//.test(u)) ? u : null;
  }

  function cssEscape(s) {
    return String(s).replace(/(["\\])/g, "\\$1");
  }

  function cardByName(name) {
    return els.list.querySelector('.card[data-name="' + cssEscape(name) + '"]');
  }

  // ===== toast =====
  function toast(message, kind) {
    var t = document.createElement("div");
    t.className = "toast " + (kind === "err" ? "err" : "ok");
    t.textContent = message;
    els.toasts.appendChild(t);
    requestAnimationFrame(function () { t.classList.add("show"); });
    setTimeout(function () {
      t.classList.remove("show");
      setTimeout(function () { t.remove(); }, 320);
    }, 3000);
  }

  // ===== 连接状态 =====
  function setConnected(ok) {
    if (ok) {
      connected = true;
      els.dot.classList.remove("off");
      els.badge.hidden = true;
      els.text.classList.remove("off");
    } else {
      connected = false;
      els.dot.classList.add("off");
      els.badge.hidden = false;
      els.text.textContent = "连接断开";
      els.text.classList.add("off");
    }
  }

  // ===== api =====
  async function api(method, path, body) {
    var res;
    try {
      res = await fetch(API_BASE + path, {
        method: method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined
      });
    } catch (e) {
      setConnected(false);
      throw e;
    }
    setConnected(true);
    return res;
  }

  async function fetchServices() {
    var res = await api("GET", "/api/services");
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    return Array.isArray(data.services) ? data.services : [];
  }

  async function fetchLogs(name) {
    var res = await api("GET", "/api/logs/" + encodeURIComponent(name) + "?n=" + LOG_N);
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    return Array.isArray(data.lines) ? data.lines : [];
  }

  async function postAction(name, action) {
    var res = await api("POST", "/api/services/" + encodeURIComponent(name) + "/" + action);
    if (!res.ok) throw new Error("HTTP " + res.status);
    await res.json().catch(function () { return {}; });
  }

  async function postAutostart(name, value) {
    try {
      var res = await api("POST", "/api/config/" + encodeURIComponent(name), { autostart: value });
      if (!res.ok) throw new Error("HTTP " + res.status);
      await res.json().catch(function () { return {}; });
      return true;
    } catch (e) {
      return false;
    }
  }

  // ===== 状态徽章 =====
  function statusBadge(status) {
    var map = STATUS_MAP[status] || STATUS_MAP.stopped;
    var b = document.createElement("span");
    b.className = "status-badge " + map.cls;
    var dot = document.createElement("span");
    dot.className = "dot";
    b.appendChild(dot);
    b.appendChild(document.createTextNode(map.label));
    return b;
  }

  // ===== 操作按钮（焦点安全） =====
  function wantedOps(status) {
    switch (status) {
      case "stopped":
      case "failed":
        return [{ action: "start", label: "启动", busy: false }];
      case "running":
        return [{ action: "restart", label: "重启", busy: false }, { action: "stop", label: "停止", busy: false }];
      case "starting":
        return [{ action: "start", label: "启动中…", busy: true }];
      case "stopping":
        return [{ action: "stop", label: "停止中…", busy: true }];
      default:
        return [];
    }
  }

  function buildOpButton(def, name, card) {
    var b = document.createElement("button");
    b.className = "btn btn-" + def.action;
    b.dataset.action = def.action;
    b.disabled = !!def.busy;
    b.textContent = def.label;
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      doAction(name, def.action, card);
    });
    return b;
  }

  function setOpButton(btn, def) {
    btn.disabled = !!def.busy;
    btn.className = "btn btn-" + def.action;
    btn.textContent = def.label;
  }

  function rebuildOpGroup(opGroup, svc, card, wanted) {
    opGroup.replaceChildren();
    wanted.forEach(function (d) { opGroup.appendChild(buildOpButton(d, svc.name, card)); });
  }

  async function doAction(name, action, card) {
    card.classList.add("updating");
    var cleared = false;
    function clear() {
      if (!cleared) { cleared = true; card.classList.remove("updating"); }
    }
    try {
      await postAction(name, action);
      var label = action === "start" ? "已发送启动请求"
                : action === "stop" ? "已发送停止请求"
                : "已发送重启请求";
      toast(label, "ok");
    } catch (e) {
      toast("操作失败：" + (e && e.message ? e.message : "未知错误"), "err");
    }
    poll(true);
    setTimeout(clear, 2000);
  }

  // ===== 卡片骨架（仅首次创建，后续增量更新以保焦点） =====
  function buildCardSkeleton(card) {
    card.replaceChildren();

    var inner = document.createElement("div");
    inner.className = "card-inner";

    var info = document.createElement("div");
    info.className = "info";

    var actions = document.createElement("div");
    actions.className = "actions";

    // 自启滑块
    var toggle = document.createElement("label");
    toggle.className = "toggle";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "toggle-input";
    var track = document.createElement("span");
    track.className = "toggle-track";
    var knob = document.createElement("span");
    knob.className = "toggle-knob";
    track.appendChild(knob);
    var tlabel = document.createElement("span");
    tlabel.className = "toggle-label";
    tlabel.textContent = "自启";
    toggle.appendChild(cb);
    toggle.appendChild(track);
    toggle.appendChild(tlabel);
    toggle.addEventListener("click", function (e) { e.stopPropagation(); });
    cb.addEventListener("change", async function () {
      var name = card.dataset.name;
      var newVal = cb.checked;
      var ok = await postAutostart(name, newVal);
      if (!ok) { cb.checked = !newVal; toast("自启动设置已回滚", "err"); }
      else { toast("已保存自启动设置", "ok"); }
    });

    var opGroup = document.createElement("div");
    opGroup.className = "op-group";

    var logBtn = document.createElement("button");
    logBtn.className = "btn btn-log";
    logBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openLog(card.dataset.name, card);
    });

    actions.appendChild(toggle);
    actions.appendChild(opGroup);
    actions.appendChild(logBtn);
    inner.appendChild(info);
    inner.appendChild(actions);
    card.appendChild(inner);

    // 整行点击跳转（核心新需求）
    card.addEventListener("click", function () {
      if (!card.classList.contains("clickable")) return;
      var u = card.dataset.url;
      if (u) window.open(u, "_blank");
    });
    card.addEventListener("keydown", function (e) {
      if (!card.classList.contains("clickable")) return;
      if (e.key === "Enter") {
        var u = card.dataset.url;
        if (u) { e.preventDefault(); window.open(u, "_blank"); }
      }
    });

    card.dataset.built = "1";
  }

  function renderCard(svc, card) {
    var status = svc.status || "stopped";
    var url = safeUrl(svc.url);

    // 整行点击：仅 url 合法时 clickable
    if (url) {
      card.classList.add("clickable");
      card.dataset.url = url;
      card.tabIndex = 0;
    } else {
      card.classList.remove("clickable");
      delete card.dataset.url;
      card.removeAttribute("tabindex");
    }

    // 过渡态锚点（用于焦点保护时复用按钮结构）
    if (status === "stopped" || status === "failed" || status === "running") {
      stableLayout.set(svc.name, status);
    }

    card.classList.toggle("log-open", openLogs.has(svc.name));

    // 状态已离开过渡态 -> 解除局部禁用
    if (status !== "starting" && status !== "stopping") {
      card.classList.remove("updating");
    }

    if (!card.dataset.built) buildCardSkeleton(card);

    // 信息区（无焦点元素，可直接重建）
    var info = card.querySelector(".info");
    info.replaceChildren();
    info.appendChild(statusBadge(status));
    var nameEl = document.createElement("span");
    nameEl.className = "svc-name";
    nameEl.textContent = svc.display || svc.name;
    info.appendChild(nameEl);
    if (url) {
      var arrow = document.createElement("span");
      arrow.className = "ext-arrow";
      arrow.textContent = "\u2197";
      arrow.title = "点击整行打开服务网页";
      info.appendChild(arrow);
    }
    if (svc.port) {
      var pc = document.createElement("span");
      pc.className = "chip";
      pc.textContent = "端口 " + svc.port;
      info.appendChild(pc);
    }
    var up = fmtUptime(svc.uptime_sec);
    if (up) {
      var uc = document.createElement("span");
      uc.className = "chip";
      uc.textContent = "运行 " + up;
      info.appendChild(uc);
    }
    if (svc.description) {
      var d = document.createElement("span");
      d.className = "svc-desc";
      d.textContent = svc.description;
      d.title = svc.description;
      info.appendChild(d);
    }
    if (svc.error) {
      var er = document.createElement("span");
      er.className = "svc-error";
      er.textContent = svc.error;
      er.title = svc.error;
      info.appendChild(er);
    }

    // 自启滑块（复用，只更新勾选态）
    card.querySelector(".toggle-input").checked = !!svc.autostart;

    // 操作按钮组（焦点保护）
    var opGroup = card.querySelector(".op-group");
    var focusedBtn = opGroup.querySelector("button.btn:focus");
    var focusedAction = focusedBtn ? focusedBtn.dataset.action : null;
    var wanted = wantedOps(status);

    if (focusedAction) {
      var defByAction = {};
      wanted.forEach(function (d) { defByAction[d.action] = d; });
      var existing = Array.prototype.slice.call(opGroup.querySelectorAll("button.btn"));
      var matched = 0;
      existing.forEach(function (b) {
        if (defByAction[b.dataset.action]) { setOpButton(b, defByAction[b.dataset.action]); matched++; }
      });
      if (!(matched === wanted.length && existing.length === wanted.length)) {
        rebuildOpGroup(opGroup, svc, card, wanted);
      }
    } else {
      rebuildOpGroup(opGroup, svc, card, wanted);
    }

    // invalid 只读提示
    if (status === "invalid") {
      opGroup.replaceChildren();
      var note = document.createElement("span");
      note.className = "readonly-note";
      note.textContent = "清单无效，无法操作";
      opGroup.appendChild(note);
    }

    // 日志按钮（复用，更新文案/激活态）
    var logBtn = card.querySelector(".btn-log");
    var isOpen = openLogs.has(svc.name);
    logBtn.classList.toggle("active", isOpen);
    logBtn.textContent = isOpen ? "收起" : "日志";
  }

  // ===== 日志面板 =====
  function openLog(name, card) {
    if (openLogs.has(name)) {
      openLogs.delete(name);
      logAtBottom.delete(name);
      var p = card.querySelector(".log-panel");
      if (p) { p.classList.remove("open"); setTimeout(function () { p.remove(); }, 260); }
      card.classList.remove("log-open");
      var btn = card.querySelector(".btn-log");
      if (btn) { btn.textContent = "日志"; btn.classList.remove("active"); }
      return;
    }

    // 同一时刻只允许一张展开：收起其他
    openLogs.forEach(function (other) {
      if (other === name) return;
      openLogs.delete(other);
      logAtBottom.delete(other);
      var oc = cardByName(other);
      if (oc) {
        var op = oc.querySelector(".log-panel");
        if (op) op.remove();
        oc.classList.remove("log-open");
        var ob = oc.querySelector(".btn-log");
        if (ob) { ob.textContent = "日志"; ob.classList.remove("active"); }
      }
    });

    openLogs.add(name);
    logAtBottom.set(name, true);
    var panel = document.createElement("div");
    panel.className = "log-panel";
    card.appendChild(panel);
    bindLogScroll(name, panel);
    bindLogDrag(panel);
    panel.addEventListener("click", function (e) { e.stopPropagation(); });
    requestAnimationFrame(function () { panel.classList.add("open"); });
    refreshLog(name, panel);
    card.classList.add("log-open");
  }

  function bindLogScroll(name, panel) {
    panel.addEventListener("scroll", function () {
      var atBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 30;
      logAtBottom.set(name, atBottom);
    }, { passive: true });
  }

  function bindLogDrag(panel) {
    panel.addEventListener("mousedown", function (e) {
      if (panel.scrollWidth - panel.clientWidth > 4) {
        dragState = { panel: panel, startX: e.clientX, startLeft: panel.scrollLeft };
        panel.classList.add("dragging");
        e.preventDefault();
      }
    });
  }

  document.addEventListener("mousemove", function (e) {
    if (!dragState) return;
    dragState.panel.scrollLeft = dragState.startLeft - (e.clientX - dragState.startX);
  });
  document.addEventListener("mouseup", function () {
    if (dragState) { dragState.panel.classList.remove("dragging"); dragState = null; }
  });

  async function refreshLog(name, panel) {
    if (!panel || !openLogs.has(name)) return;
    var wasBottom = logAtBottom.get(name) !== false;
    try {
      var lines = await fetchLogs(name);
      panel.replaceChildren();
      if (!lines.length) {
        var empty = document.createElement("div");
        empty.className = "log-empty";
        empty.textContent = "（暂无日志）";
        panel.appendChild(empty);
      } else {
        lines.forEach(function (ln, i) {
          var row = document.createElement("div");
          row.className = "log-line";
          var num = document.createElement("span");
          num.className = "ln";
          num.textContent = String(i + 1);
          var content = document.createElement("span");
          content.className = "lc";
          content.textContent = ln;
          row.appendChild(num);
          row.appendChild(content);
          panel.appendChild(row);
        });
      }
      if (wasBottom) panel.scrollTop = panel.scrollHeight;
      var atBottomNow = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 30;
      logAtBottom.set(name, atBottomNow);
    } catch (e) {
      // 网络错误仅保留面板，连接徽标由 api() 处理
    }
  }

  // ===== 渲染 + 轮询 =====
  async function render(data) {
    var names = data.map(function (s) { return s.name; });
    var running = data.filter(function (s) { return s.status === "running"; }).length;

    els.empty.hidden = data.length > 0;
    els.list.hidden = data.length === 0;
    els.text.textContent = running + "/" + data.length + " 运行中";
    els.text.classList.remove("off");

    // 移除已不存在的服务卡片
    Array.prototype.slice.call(els.list.children).forEach(function (card) {
      var nm = card.dataset.name;
      if (nm && names.indexOf(nm) === -1) {
        if (openLogs.has(nm)) { openLogs.delete(nm); logAtBottom.delete(nm); }
        stableLayout.delete(nm);
        card.remove();
      }
    });

    data.forEach(function (svc) {
      var card = cardByName(svc.name);
      if (!card) {
        card = document.createElement("div");
        card.className = "card";
        card.dataset.name = svc.name;
        els.list.appendChild(card);
      }
      renderCard(svc, card);
    });

    // 刷新所有已展开日志面板（跟随轮询频率）
    openLogs.forEach(function (name) {
      var card = cardByName(name);
      if (!card) return;
      var panel = card.querySelector(".log-panel");
      if (panel) refreshLog(name, panel);
    });

    poll(false);
  }

  async function tick() {
    if (document.hidden) return;
    try {
      var data = await fetchServices();
      await render(data);
    } catch (e) {
      // 连接错误由 api() 处理徽标与断连
    }
  }

  function poll(forceFast) {
    if (pollTimer) clearTimeout(pollTimer);
    var fast = !!forceFast;
    if (!fast && els.list) {
      fast = els.list.querySelector(".status-badge.starting, .status-badge.stopping") !== null;
    }
    pollTimer = setTimeout(tick, fast ? POLL_FAST : POLL_SLOW);
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll(true);
  });
  els.refresh.addEventListener("click", function () { poll(true); });

  // ===== 初始化 =====
  (async function init() {
    try {
      var data = await fetchServices();
      await render(data);
    } catch (e) {
      poll(false);
    }
  })();
})();
