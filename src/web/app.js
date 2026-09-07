(function () {
  "use strict";

  // ===== 模块常量 =====
  var API_BASE = "http://127.0.0.1:1236";
  var POLL_FAST = 1000;
  var POLL_SLOW = 3000;
  var LOG_N = 200;
  var ALL_KEY = "(all)";   // 登记簿抽屉的伪服务名（不带 svc 过滤）

  // 登记簿动作中文名（与 src/core/audit.py 的 ACTS 保持一致）
  var ACT_MAP = {
    host_start: "宿主启动", host_stop: "宿主停止",
    start: "启动", stop: "停止", restart: "重启",
    autostart: "自启发起", autostart_skip: "自启跳过",
    adopt_external: "外部实例归位", health_flip: "健康翻转",
    idle_reclaim: "空闲回收", crash: "意外退出",
    spawn_fail: "拉起失败", reject_double: "拒绝双开",
    spec_invalid: "注册条无效",
    cron_run: "定时执行", cron_skip: "定时跳过"
  };

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
    refresh: document.getElementById("btn-refresh"),
    hostUptime: document.getElementById("host-uptime"),
    auditBtn: document.getElementById("btn-host-audit"),
    auditDrawer: document.getElementById("audit-drawer"),
    hostRestart: document.getElementById("btn-host-restart"),
    hostStop: document.getElementById("btn-host-stop"),
    cronSection: document.getElementById("cron-section"),
    cronList: document.getElementById("cron-list")
  };

  // 状态：同一时刻只允许一个事件视图展开（某张卡片内，或顶部全局抽屉）
  var openLogs = new Set();
  var logAtBottom = new Map();   // name -> 是否贴底
  var stableLayout = new Map();  // name -> 过渡态锚定稳定态
  var pollTimer = null;
  var connected = true;
  var dragState = null;
  var hostAction = null;   // null | "restart" | "stop"：宿主自操作进行中
  var hostPid = 0;         // 用于判定 restart 后是否已换新进程

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

  async function fetchHost() {
    var res = await api("GET", "/api/host");
    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.json();
  }

  async function fetchCrons() {
    var res = await api("GET", "/api/crons");
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    return Array.isArray(data.crons) ? data.crons : [];
  }

  // ===== 定时任务（reg\cron\ —— 第二物种：到点跑一次脚本，无常驻进程）=====
  function cronStatus(c) {
    if (!c.valid) return "invalid";
    if (c.running) return "running";
    if (!c.enabled) return "disabled";
    return "idle";
  }

  var CRON_STATUS_MAP = {
    running:  { cls: "starting", label: "执行中" },
    idle:     { cls: "running",  label: "已启用" },
    disabled: { cls: "stopped",  label: "已停用" },
    invalid:  { cls: "invalid",  label: "无效" }
  };

  function fmtClock(ts) {
    if (typeof ts !== "number") return null;
    var d = new Date(ts * 1000);
    var p = function (n) { return String(n).padStart(2, "0"); };
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function fmtDur(ms) {
    if (typeof ms !== "number") return "";
    return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";
  }

  function cronChip(text, title) {
    var c = document.createElement("span");
    c.className = "chip";
    c.textContent = text;
    if (title) c.title = title;
    return c;
  }

  function lastResultChip(c) {
    if (typeof c.last_run !== "number") return cronChip("未运行过");
    var t = fmtClock(c.last_run);
    if (c.last_ok === true) return cronChip("上次 ✓ " + t + " · " + fmtDur(c.last_dur_ms), "上次执行成功");
    if (c.last_rc === null || c.last_rc === undefined)
      return cronChip("上次 ✗ " + t + " · 超时/未启动", "上次执行失败（超时被终止，或进程没能起来）");
    return cronChip("上次 ✗ " + t + " · rc=" + c.last_rc, "上次执行失败（退出码 " + c.last_rc + "）");
  }

  function renderCrons(crons) {
    if (!els.cronSection) return;
    els.cronSection.hidden = !crons.length;
    var names = crons.map(function (c) { return c.name; });

    // 移除已消失的任务行
    Array.prototype.slice.call(els.cronList.children).forEach(function (row) {
      if (row.dataset.name && names.indexOf(row.dataset.name) === -1) row.remove();
    });

    crons.forEach(function (c) {
      var row = els.cronList.querySelector('.cron-card[data-name="' + cssEscape(c.name) + '"]');
      if (!row) {
        row = document.createElement("div");
        row.className = "card cron-card";
        row.dataset.name = c.name;
        var inner = document.createElement("div");
        inner.className = "card-inner";
        var info = document.createElement("div");
        info.className = "info";
        var actions = document.createElement("div");
        actions.className = "actions";
        var runBtn = document.createElement("button");
        runBtn.className = "btn btn-run";
        runBtn.textContent = "▶ 立即运行";
        runBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          runCron(c.name, runBtn);
        });
        actions.appendChild(runBtn);
        inner.appendChild(info);
        inner.appendChild(actions);
        row.appendChild(inner);
        els.cronList.appendChild(row);
      }

      var st = CRON_STATUS_MAP[cronStatus(c)];
      var badge = document.createElement("span");
      badge.className = "status-badge " + st.cls;
      var dot = document.createElement("span");
      dot.className = "dot";
      badge.appendChild(dot);
      badge.appendChild(document.createTextNode(st.label));

      var info = row.querySelector(".info");
      info.replaceChildren();
      info.appendChild(badge);
      var nm = document.createElement("span");
      nm.className = "svc-name";
      nm.textContent = c.display || c.name;
      info.appendChild(nm);
      info.appendChild(cronChip(c.schedule, "调度规则（timeout " + c.timeout_sec + "s" +
        (c.catch_up ? "，错过补跑" : "") + "）"));
      if (c.running) {
        info.appendChild(cronChip("本轮运行中…"));
      } else if (c.enabled && c.valid && typeof c.next_run === "number") {
        info.appendChild(cronChip("下次 " + fmtClock(c.next_run)));
      }
      if (c.valid) info.appendChild(lastResultChip(c));
      if (c.description) {
        var d = document.createElement("span");
        d.className = "svc-desc";
        d.textContent = c.description;
        d.title = c.description;
        info.appendChild(d);
      }
      if (c.error) {
        var er = document.createElement("span");
        er.className = "svc-error";
        er.textContent = c.error;
        er.title = c.error;
        info.appendChild(er);
      }

      var runBtn = row.querySelector(".btn-run");
      runBtn.disabled = c.running || !c.valid || !c.enabled;
    });
  }

  async function runCron(name, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "已触发…"; }
    try {
      var res = await api("POST", "/api/crons/" + encodeURIComponent(name) + "/run?via=manual");
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.message || ("HTTP " + res.status));
      toast("已触发 " + name + "（结果稍后见卡片与登记簿）", "ok");
    } catch (e) {
      toast("触发失败：" + (e && e.message ? e.message : "未知错误"), "err");
    }
    poll(true);
  }

  async function fetchAudit(name) {
    // 控制面登记簿：只含 my-service 记的启停事件，服务的业务日志由服务自管（框架不读）
    var q = "/api/audit?n=" + LOG_N
      + (name && name !== ALL_KEY ? "&svc=" + encodeURIComponent(name) : "");
    var res = await api("GET", q);
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    return Array.isArray(data.events) ? data.events : [];
  }

  async function postAction(name, action) {
    var res = await api("POST", "/api/services/" + encodeURIComponent(name) + "/" + action);
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.message || ("HTTP " + res.status));
    return data;
  }

  // 懒打开：直接打开"启动中转页"（opening.html，秒开、带转圈），由中转页负责
  // 启动服务、轮询就绪后跳转。window.open 在用户手势的同步栈内执行，不被弹窗拦截。
  function openService(name, url, display) {
    var target = "/opening.html?name=" + encodeURIComponent(name)
      + "&url=" + encodeURIComponent(url)
      + "&title=" + encodeURIComponent(display || name);
    var w = null;
    try { w = window.open(target, "_blank"); } catch (e) { /* 忽略：w 为 null 时下方提示 */ }
    if (!w) toast("浏览器拦截了弹窗，请允许此站点弹窗后重试", "err");
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

    // 整行点击跳转（懒打开：先弹中转页，就绪后跳转）
    card.addEventListener("click", function () {
      if (!card.classList.contains("clickable")) return;
      var u = card.dataset.url;
      if (u) openService(card.dataset.name, u, card.dataset.display);
    });
    card.addEventListener("keydown", function (e) {
      if (!card.classList.contains("clickable")) return;
      if (e.key === "Enter") {
        var u = card.dataset.url;
        if (u) { e.preventDefault(); openService(card.dataset.name, u, card.dataset.display); }
      }
    });

    card.dataset.built = "1";
  }

  function renderCard(svc, card) {
    var status = svc.status || "stopped";
    var url = safeUrl(svc.url);
    card.dataset.display = svc.display || svc.name;

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
    if (svc.mode === "on_demand") {
      var idleMin = Math.round((svc.idle_timeout || 600) / 60);
      var od = document.createElement("span");
      od.className = "chip";
      if (svc.status === "running" && typeof svc.last_active === "number") {
        var remainSec = Math.max(0, (svc.idle_timeout || 600) - (Date.now() / 1000 - svc.last_active));
        var rm = Math.floor(remainSec / 60);
        var rs = Math.floor(remainSec % 60);
        od.textContent = "空闲回收 " + rm + ":" + String(rs).padStart(2, "0");
        od.title = "无操作 " + idleMin + " 分钟后自动停止；还剩 " + rm + " 分 " + rs + " 秒";
      } else {
        od.textContent = "按需 · 空闲" + idleMin + "分钟停";
        od.title = "点开自动启动；页面无操作 " + idleMin + " 分钟后框架自动停止";
      }
      info.appendChild(od);
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

    // 自启滑块（复用，只更新勾选态）；on_demand 按需启动，不支持自启，隐藏
    var toggleEl = card.querySelector(".toggle");
    if (toggleEl) {
      toggleEl.style.display = (svc.mode === "on_demand") ? "none" : "";
    }
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

    // 事件按钮（复用，更新文案/激活态）——展开的是 my-service 启停登记簿，不是服务日志
    var logBtn = card.querySelector(".btn-log");
    var isOpen = openLogs.has(svc.name);
    logBtn.classList.toggle("active", isOpen);
    logBtn.textContent = isOpen ? "收起" : "事件";
  }

  // ===== 事件面板（登记簿视图；服务的业务日志由服务自管，面板不读）=====
  function closeLog(name) {
    if (!openLogs.has(name)) return;
    openLogs.delete(name);
    logAtBottom.delete(name);
    if (name === ALL_KEY) {
      els.auditDrawer.hidden = true;
      els.auditDrawer.classList.remove("open");
      els.auditDrawer.replaceChildren();
      if (els.auditBtn) els.auditBtn.classList.remove("active");
      return;
    }
    var card = cardByName(name);
    if (!card) return;
    var p = card.querySelector(".log-panel");
    if (p) { p.classList.remove("open"); setTimeout(function () { p.remove(); }, 260); }
    card.classList.remove("log-open");
    var btn = card.querySelector(".btn-log");
    if (btn) { btn.textContent = "事件"; btn.classList.remove("active"); }
  }

  function openLog(name, card) {
    if (name === ALL_KEY) { toggleAllAudit(); return; }
    if (openLogs.has(name)) { closeLog(name); return; }
    // 同一时刻只允许一个视图展开（含全局抽屉）
    Array.prototype.slice.call(openLogs).forEach(closeLog);

    openLogs.add(name);
    logAtBottom.set(name, true);
    var panel = document.createElement("div");
    panel.className = "log-panel";
    card.appendChild(panel);
    bindLogScroll(name, panel);
    bindLogDrag(panel);
    panel.addEventListener("click", function (e) { e.stopPropagation(); });
    requestAnimationFrame(function () { panel.classList.add("open"); });
    refreshEvents(name, panel);
    card.classList.add("log-open");
  }

  function toggleAllAudit() {
    if (!els.auditDrawer) return;
    if (openLogs.has(ALL_KEY)) { closeLog(ALL_KEY); return; }
    Array.prototype.slice.call(openLogs).forEach(closeLog);
    openLogs.add(ALL_KEY);
    logAtBottom.set(ALL_KEY, true);
    els.auditDrawer.hidden = false;
    if (!els.auditDrawer.dataset.bound) {   // 抽屉是复用节点，监听只装一次
      bindLogScroll(ALL_KEY, els.auditDrawer);
      bindLogDrag(els.auditDrawer);
      els.auditDrawer.dataset.bound = "1";
    }
    requestAnimationFrame(function () { els.auditDrawer.classList.add("open"); });
    if (els.auditBtn) els.auditBtn.classList.add("active");
    refreshEvents(ALL_KEY, els.auditDrawer);
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

  async function refreshEvents(name, panel) {
    if (!panel || !openLogs.has(name)) return;
    var wasBottom = logAtBottom.get(name) !== false;
    try {
      var events = await fetchAudit(name);
      panel.replaceChildren();
      if (!events.length) {
        var empty = document.createElement("div");
        empty.className = "log-empty";
        empty.textContent = "（暂无事件 —— 服务的运行日志写在它自己目录的 logs\\ 下，框架不代管）";
        panel.appendChild(empty);
      } else {
        events.forEach(function (ev) {
          var fail = ev.ok === false;
          var row = document.createElement("div");
          row.className = "ev" + (fail ? " fail" : "");
          var ts = document.createElement("span");
          ts.className = "ev-ts";
          ts.textContent = String(ev.ts || "").slice(11, 19);   // 只留时分秒
          var svc = document.createElement("span");
          svc.className = "ev-svc";
          svc.textContent = ev.svc || "(host)";
          var act = document.createElement("span");
          act.className = "ev-act";
          act.textContent = ACT_MAP[ev.act] || ev.act;
          var dur = document.createElement("span");
          dur.className = "ev-dur";
          dur.textContent = (typeof ev.dur_ms === "number") ? ev.dur_ms + "ms" : "";
          var note = document.createElement("span");
          note.className = "ev-note";
          note.textContent = (fail ? "✗ " : "") + (ev.note || "");
          row.appendChild(ts); row.appendChild(svc); row.appendChild(act);
          row.appendChild(dur); row.appendChild(note);
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
  async function render(data, host) {
    var names = data.map(function (s) { return s.name; });
    var running = data.filter(function (s) { return s.status === "running"; }).length;

    els.empty.hidden = data.length > 0;
    els.list.hidden = data.length === 0;
    els.text.textContent = running + "/" + data.length + " 运行中";
    els.text.classList.remove("off");

    // 宿主自身运行时长（区别于单个服务的 uptime）
    if (host && typeof host.uptime_sec === "number") {
      els.hostUptime.textContent = "宿主运行 " + fmtUptime(host.uptime_sec);
      els.hostUptime.title = "my-service 自身已运行 " + fmtUptime(host.uptime_sec)
        + "（pid " + host.pid + "，v" + host.version + "）";
    }
    var pending = data.some(function (s) {
      return s.status === "starting" || s.status === "stopping";
    });
    els.hostRestart.disabled = pending || !!hostAction;
    els.hostStop.disabled = pending || !!hostAction;

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

    // 刷新所有已展开的事件面板（跟随轮询频率）
    openLogs.forEach(function (name) {
      if (name === ALL_KEY) {
        refreshEvents(ALL_KEY, els.auditDrawer);
        return;
      }
      var card = cardByName(name);
      if (!card) return;
      var panel = card.querySelector(".log-panel");
      if (panel) refreshEvents(name, panel);
    });

    poll(false);
  }

  async function tick() {
    if (document.hidden) return;
    try {
      var pair = await Promise.all([fetchServices(), fetchHost()]);
      hostPid = pair[1].pid || hostPid;
      await render(pair[0], pair[1]);
      // 定时任务区块：拉取失败不连坐服务列表（旧宿主无此端点时静默隐藏）
      try { renderCrons(await fetchCrons()); } catch (e) { /* 保持现状 */ }
    } catch (e) {
      // 连接错误由 api() 处理徽标与断连；宿主自操作的恢复由专用监视循环处理
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

  // ===== 宿主自身操作（重启/停止）——专属通道 =====
  async function doHostAction(action) {
    if (hostAction) return;
    var tip = action === "restart"
      ? "重启 my-service 会短暂中断面板连接，被管服务不受影响、继续运行。\n确认重启？"
      : "停止 my-service 后宿主退出，被管服务不受影响、继续运行\n（需手动运行 entry\\start.ps1 才能恢复宿主）。\n确认停止？";
    if (!window.confirm(tip)) return;

    var oldPid = hostPid;
    hostAction = action;
    els.hostRestart.disabled = true;
    els.hostStop.disabled = true;
    els.refresh.disabled = true;
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    els.text.textContent = action === "restart" ? "正在重启宿主…" : "正在停止宿主…";
    els.text.classList.remove("off");

    try { await api("POST", "/api/host/" + action); } catch (e) { /* 预期连接会断 */ }
    watchHostRecovery(oldPid, action);
  }

  function watchHostRecovery(oldPid, action) {
    var tries = 0;
    var iv = setInterval(async function () {
      tries++;
      var meta = null;
      try {
        var res = await fetch(API_BASE + "/api/host", { cache: "no-store" });
        if (res.ok) meta = await res.json();
      } catch (e) { /* 宿主尚未就绪/已停止 */ }

      if (action === "restart") {
        if (meta && meta.pid && meta.pid !== oldPid) {
          clearInterval(iv); hostAction = null;
          toast("宿主已重启 (新 pid " + meta.pid + ")", "ok");
          setTimeout(function () { location.reload(); }, 700);
        } else if (tries > 40) {
          clearInterval(iv); hostAction = null;
          toast("重启超时，请查看 logs\\host.err.log", "err");
          els.refresh.disabled = false; poll(false);
        }
      } else { // stop：一旦宿主无响应即确认已停止
        if (!meta) {
          clearInterval(iv); hostAction = null;
          setConnected(false);
          els.text.textContent = "宿主已停止 — 运行 entry\\start.ps1 重新启动";
          els.text.classList.add("off");
        } else if (tries > 20) {
          clearInterval(iv); hostAction = null;
          toast("停止状态未知", "err"); els.refresh.disabled = false; poll(false);
        }
      }
    }, 1000);
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll(true);
  });
  els.refresh.addEventListener("click", function () { poll(true); });
  if (els.auditBtn) {
    els.auditBtn.addEventListener("click", function () { toggleAllAudit(); });
  }
  els.hostRestart.addEventListener("click", function () { doHostAction("restart"); });
  els.hostStop.addEventListener("click", function () { doHostAction("stop"); });

  // ===== 初始化 =====
  (async function init() {
    try {
      var pair = await Promise.all([fetchServices(), fetchHost()]);
      hostPid = pair[1].pid || 0;
      await render(pair[0], pair[1]);
      try { renderCrons(await fetchCrons()); } catch (e) { /* 保持现状 */ }
    } catch (e) {
      poll(false);
    }
  })();
})();
