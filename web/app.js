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
    adopt_external: "实例归位", health_flip: "健康翻转",
    idle_reclaim: "空闲回收", crash: "意外退出",
    spawn_fail: "拉起失败", reject_double: "拒绝双开",
    spec_invalid: "注册条无效",
    identity_mismatch: "端口被他人占用", identity_unknown: "身份不确认",
    pidfile_stale: "清理残留 pid 凭证",
    cron_run: "定时执行", cron_skip: "定时跳过"
  };

  // 中文字义映射表（状态 -> 样式类 + 文案）
  var STATUS_MAP = {
    running:  { cls: "running",  label: "运行中" },
    stopped:  { cls: "stopped",  label: "已停止" },
    starting: { cls: "starting", label: "启动中" },
    stopping: { cls: "stopping", label: "停止中" },
    failed:   { cls: "failed",   label: "失败" },
    // 端口被别的程序占用：不是"我启动失败"，处置方式也不同（改端口 or 找回占用方）
    conflict: { cls: "conflict", label: "端口被占" },
    // 有应答但无法确认身份：保守呈现，绝不显示成"运行中"
    unknown:  { cls: "unknown",  label: "身份不确认" },
    invalid:  { cls: "invalid",  label: "清单无效" }
  };

  var els = {
    list: document.getElementById("service-list"),
    listOd: document.getElementById("service-list-ondemand"),
    secPd: document.getElementById("svc-persistent"),
    secOd: document.getElementById("svc-ondemand"),
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
    cronList: document.getElementById("cron-list"),
    verTag: document.getElementById("ver-tag")
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

  /* 服务分组：mode 为 on_demand 归"即用即走"，其余（managed/detached）归"常驻"。
     纯前端推导，注册条不加字段；后端 snapshot 已带 mode 字段。 */
  function isOnDemand(svc) { return svc && svc.mode === "on_demand"; }
  function listFor(svc) { return isOnDemand(svc) ? els.listOd : els.list; }

  function eachSvcList(fn) {
    [els.list, els.listOd].forEach(function (l) { if (l) fn(l); });
  }

  function cardByName(name) {
    var sel = '.card[data-name="' + cssEscape(name) + '"]';
    var hit = null;
    eachSvcList(function (l) {
      if (!hit) hit = l.querySelector(sel);
    });
    if (hit) return hit;
    return els.cronList
      ? els.cronList.querySelector('.cron-card[data-name="' + cssEscape(name) + '"]')
      : null;
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

  // 上次结果：只显示时间（耗时挪进悬停提示，避免与右侧开关挤压重叠）；
  // 失败原因（rc / 超时）不是时长，保留在标签上
  function lastResultChip(c) {
    if (typeof c.last_run !== "number") return cronChip("未运行过");
    var t = fmtClock(c.last_run);
    var dur = fmtDur(c.last_dur_ms);
    var durTip = dur ? " · 耗时 " + dur : "";
    if (c.last_ok === true) return cronChip("上次 ✓ " + t, "上次执行成功" + durTip);
    if (c.last_rc === null || c.last_rc === undefined)
      return cronChip("上次 ✗ " + t + " · 超时",
        "上次执行失败（超时被终止，或进程没能起来）" + durTip);
    return cronChip("上次 ✗ " + t + " · rc=" + c.last_rc,
      "上次执行失败（退出码 " + c.last_rc + "）" + durTip);
  }

  // ===== 定时任务配置行（行内编辑：时间 / 超时 / 补跑 / 启用）=====
  function cfgEl(tag, cls, role, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (role) el.dataset.role = role;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }

  async function saveCronConfig(name, patch, rollback, onok) {
    try {
      var res = await api("POST", "/api/crons/" + encodeURIComponent(name) + "/config", patch);
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.error || data.message || ("HTTP " + res.status));
      toast("已保存 " + Object.keys(patch).join("、"), "ok");
      if (onok) onok();
    } catch (e) {
      toast("保存失败：" + (e && e.message ? e.message : "未知错误"), "err");
      if (rollback) rollback();
    }
    poll(true);
  }

  // 间隔秒数 → 最友好的「数值 + 单位」（3600→1 时、900→15 分、45→45 秒）
  function everyToDisplay(sec) {
    var units = [86400, 3600, 60, 1];
    for (var i = 0; i < units.length; i++) {
      if (sec % units[i] === 0) return { mult: units[i], val: sec / units[i] };
    }
    return { mult: 1, val: sec };
  }

  // ---- 定时执行时间列表：构建 / 收集 / 重建（buildCronConfig 与 syncCronConfig 共用）----
  // 新时刻默认 09:30；删除到最后一项时回填 09:30（列表不允许为空）
  function cronAtBuild(listEl, saveFn, value) {
    var row = cfgEl("div", "cfg-at-row");
    var inp = cfgEl("input", "cfg-time");
    inp.type = "time";
    inp.value = value || "09:30";
    inp.title = "每日执行时刻（本地时间）";
    var del = cfgEl("button", "cfg-at-del");
    del.type = "button";
    del.textContent = "✕";
    del.title = "删除该时刻（仅剩一项时回填 09:30）";
    row.appendChild(inp);
    row.appendChild(del);
    listEl.appendChild(row);
    if (saveFn) {
      inp.addEventListener("change", saveFn);
      del.addEventListener("click", function (e) {
        e.stopPropagation();
        if (listEl.querySelectorAll(".cfg-at-row").length <= 1) {
          inp.value = "09:30";   // 最后一项不允许删，回填默认再保存
          saveFn();
          return;
        }
        row.remove();
        saveFn();
      });
    }
    return row;
  }

  function cronAtValues(listEl) {
    var out = [];
    Array.prototype.slice.call(listEl.querySelectorAll("input")).forEach(function (i) {
      if (i.value) out.push(i.value);
    });
    return out.filter(function (v, i) { return out.indexOf(v) === i; }).sort();
  }

  function cronAtRebuild(listEl, saveFn, values) {
    listEl.innerHTML = "";
    var init = (values && values.length) ? values : ["09:30"];
    init.forEach(function (v) { cronAtBuild(listEl, saveFn, v); });
  }

  // ---- 设置面板草稿：面板打开后默认与服务端同步；一旦有编辑即建立草稿，
  //      进入本地编辑模式（轮询不再覆盖控件值），只有按「保存」才提交。
  //      保存 / 取消 / 收起后草稿清除，回到同步态。----
  function cronDraft(bar, cur) {
    if (!bar._draft) {
      var memo = cronModeMemo.get(cur.name || "") || {};
      bar._draft = {
        schedule_type: cur.schedule_type || "daily",
        at: (cur.schedule_type === "daily" && cur.at && cur.at.length)
          ? cur.at.slice() : (memo.at && memo.at.length ? memo.at.slice() : ["09:30"]),
        every_sec: (cur.schedule_type === "interval" && cur.every_sec)
          ? cur.every_sec : (memo.every_sec || 3600),
        timeout_sec: cur.timeout_sec != null ? cur.timeout_sec : 300,
        catch_up: !!cur.catch_up,
      };
    }
    return bar._draft;
  }

  function markDirty(bar, dirty) {
    var dot = bar.querySelector(".cfg-dirty");
    if (!dot) return;
    dot.hidden = !dirty;
    dot.textContent = dirty ? "● 有未保存的修改" : "";
  }

  function buildCronSwitch(name, label, field, bar) {
    var toggle = cfgEl("label", "toggle cron-switch");
    var cb = cfgEl("input", "toggle-input", field);
    cb.type = "checkbox";
    var track = cfgEl("span", "toggle-track");
    track.appendChild(cfgEl("span", "toggle-knob"));
    var tl = cfgEl("span", "toggle-label");
    tl.textContent = label;
    toggle.appendChild(cb); toggle.appendChild(track); toggle.appendChild(tl);
    toggle.title = field === "catch_up"
      ? "错过计划时刻是否补跑（跨日补跑可能误签次日额度）"
      : "是否参与调度（停用只暂停下一次触发，不打断正在跑的一轮）";
    toggle.addEventListener("click", function (e) { e.stopPropagation(); });
    cb.addEventListener("change", function () {
      if (!bar) {
        // 主行开关（无配置面板）：即点即生效，直接提交服务端。
        // 修复：此前这里直接 return——拨动既不写草稿也不调 API，
        // 轮询重建又把勾选态拉回服务端原值，表现为「开关关不掉」。
        var patch = {};
        patch[field] = cb.checked;
        saveCronConfig(name, patch,
          function () { cb.checked = !cb.checked; });   // 失败：回拨视觉状态
        return;
      }
      var cur = cronData.get(name) || {};
      cronDraft(bar, cur)[field] = cb.checked;   // 本地草稿，保存按钮统一提交
      syncCronConfig(bar, cur);                  // 刷新未保存指示
    });
    return toggle;
  }

  var cronCfgOpen = new Set();   // 配置面板展开态（轮询重建时保持）
  var cronData = new Map();      // name -> 最新 snapshot（控件回调取最新值，不用建骨架时的旧快照）
  var cronModeMemo = new Map();  // name -> {at, every_sec} 两种模式各自的最后已知值
                                 // 注册条只存当前类型的参数，切回来若用硬编码默认会丢原时间

  function toggleCronConfig(name, row) {
    if (cronCfgOpen.has(name)) cronCfgOpen.delete(name);
    else cronCfgOpen.add(name);
    var bar = row.querySelector(".cron-config");
    if (bar) {
      bar.hidden = !cronCfgOpen.has(name);
      if (bar.hidden) bar._draft = null;   // 收起即丢弃未保存草稿（保存按钮是唯一提交途径）
    }
    var btn = row.querySelector(".btn-cfg");
    if (btn) btn.classList.toggle("active", cronCfgOpen.has(name));
  }

  // 定时/间隔二选一：本地切换（草稿），保存时一并生效；
  // 另一模式的参数由 cronModeMemo 记忆，切回不丢
  function buildCronConfig(c) {
    var bar = cfgEl("div", "cron-config");
    bar.hidden = true;                       // 默认收起，点「⚙ 设置」展开

    // ---- 分区一：执行时机（什么时候跑）----
    var grp1 = cfgEl("div", "cfg-group");
    grp1.appendChild(cfgEl("span", "cfg-group-title", null, "执行时机"));

    var seg = cfgEl("div", "seg");
    [["daily", "定时执行"], ["interval", "间隔执行"]].forEach(function (p) {
      var b = cfgEl("button", "seg-btn", "mode-" + p[0]);
      b.type = "button";
      b.textContent = p[1];
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        var cur = cronData.get(c.name) || c;
        var d = cronDraft(bar, cur);
        if (d.schedule_type !== p[0]) {
          d.schedule_type = p[0];     // 本地切换模式，保存时才生效
          syncCronConfig(bar, cur);   // 立即刷新控件显隐 / 高亮 / 未保存指示
        }
      });
      seg.appendChild(b);
    });
    grp1.appendChild(seg);

    var atList = cfgEl("div", "cfg-atlist", "at");
    // 时间列表变更只进草稿（保存按钮统一提交），避免编辑中被轮询/自动保存撤销
    var touchAt = function () {
      var cur = cronData.get(c.name) || c;
      var d = cronDraft(bar, cur);
      d.schedule_type = "daily";
      d.at = cronAtValues(atList);
      syncCronConfig(bar, cur);
    };
    atList._touch = touchAt;   // sync 重建列表时复用（新 input 绑同一回调）
    cronAtRebuild(atList, touchAt, (c.at && c.at.length) ? c.at : ["09:30"]);
    var addAt = cfgEl("button", "cfg-add-at");
    addAt.type = "button";
    addAt.textContent = "＋ 添加时间";
    addAt.title = "新增一个每日执行时刻（默认 09:30）";
    addAt.addEventListener("click", function (e) {
      e.stopPropagation();
      cronAtBuild(atList, touchAt, "09:30");
      touchAt();   // 立即建立草稿：否则轮询 sync 会因数量不一致重建列表而撤销本次添加
    });
    grp1.appendChild(atList);
    grp1.appendChild(addAt);

    var iv = cfgEl("span", "cfg-iv");
    var n = cfgEl("input", "cfg-num", "every");
    n.type = "number"; n.min = "1"; n.title = "执行间隔";
    var u = cfgEl("select", "cfg-unit", "unit");
    [[1, "秒"], [60, "分"], [3600, "时"], [86400, "天"]].forEach(function (p) {
      var o = document.createElement("option");
      o.value = String(p[0]); o.textContent = p[1];
      u.appendChild(o);
    });
    var push = function () {
      var cur = cronData.get(c.name) || c;
      var d = cronDraft(bar, cur);
      d.schedule_type = "interval";
      d.every_sec = Math.round((parseFloat(n.value) || 0) * (parseFloat(u.value) || 60));
      syncCronConfig(bar, cur);
    };
    n.addEventListener("change", push);
    u.addEventListener("change", push);
    iv.appendChild(n); iv.appendChild(u);
    grp1.appendChild(iv);
    bar.appendChild(grp1);

    // ---- 分区二：异常保护（出意外时怎么办）----
    // 与「执行时机」是两个维度：这里配置的是跑起来之后的兜底行为，
    // 不改变多久跑一次。此前两者平铺，超时保护易被误读为第三种调度方式。
    var grp2 = cfgEl("div", "cfg-group");
    grp2.appendChild(cfgEl("span", "cfg-group-title", null, "异常保护"));

    var rowTo = cfgEl("div", "cfg-row");
    var lab2 = cfgEl("span", "cfg-label");
    lab2.textContent = "⏲ 超时保护";
    lab2.title = "脚本卡死时强制结束（下限 5 秒）";
    rowTo.appendChild(lab2);
    var to = cfgEl("input", "cfg-num", "timeout");
    to.type = "number"; to.min = "5";
    to.title = "脚本运行超过此秒数即整树强杀并记失败。与「多久跑一次」无关，"
             + "执行频率请用上方「间隔执行」模式";
    to.addEventListener("change", function () {
      var cur = cronData.get(c.name) || c;
      cronDraft(bar, cur).timeout_sec = parseFloat(to.value);
      syncCronConfig(bar, cur);
    });
    rowTo.appendChild(to);
    rowTo.appendChild(cfgEl("span", "cfg-unit-txt", null, "秒"));
    grp2.appendChild(rowTo);

    grp2.appendChild(buildCronSwitch(c.name, "补跑", "catch_up", bar));
    grp2.appendChild(cfgEl("div", "cfg-hint", null,
      "超时保护＝脚本卡死时强制结束；补跑＝错过计划时刻后是否立即补一次。"
      + "两者都不改变执行频率。"));
    bar.appendChild(grp2);

    // ---- 保存 / 取消：手动保存（草稿统一提交，杜绝自动保存竞态撤销输入）----
    var foot = cfgEl("div", "cfg-foot");
    foot.appendChild(cfgEl("span", "cfg-dirty", null, ""));
    var cancelBtn = cfgEl("button", "cfg-cancel", null);
    cancelBtn.type = "button";
    cancelBtn.textContent = "取消";
    var saveBtn = cfgEl("button", "cfg-save", null);
    saveBtn.type = "button";
    saveBtn.textContent = "保存设置";
    saveBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var cur = cronData.get(c.name) || c;
      var d = cronDraft(bar, cur);
      var patch = { schedule: { type: d.schedule_type } };
      if (d.schedule_type === "daily") {
        var vals = cronAtValues(atList);
        if (!vals.length) { toast("定时执行至少保留一个时刻", "warn"); return; }
        patch.schedule.at = vals;
      } else {
        patch.schedule.every_sec = d.every_sec;
      }
      patch.timeout_sec = d.timeout_sec;
      patch.catch_up = d.catch_up;
      saveCronConfig(c.name, patch,
        function () { syncCronConfig(bar, cur); },   // 失败：保留草稿供重试，仅刷新指示
        function () {                                 // 成功：清草稿回同步态 + 记忆模式参数
          var m = cronModeMemo.get(c.name) || {};
          if (d.schedule_type === "daily") m.at = cronAtValues(atList);
          else m.every_sec = d.every_sec;
          cronModeMemo.set(c.name, m);
          bar._draft = null;
          syncCronConfig(bar, cronData.get(c.name) || c);
        });
    });
    cancelBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var cur = cronData.get(c.name) || c;
      bar._draft = null;
      syncCronConfig(bar, cur);   // 控件回填服务端值
    });
    foot.appendChild(cancelBtn);
    foot.appendChild(saveBtn);
    bar.appendChild(foot);
    return bar;
  }

  // 每轮同步：模式高亮、参数控件显隐、控件值、展开态；正在输入的不打断
  function syncCronConfig(bar, c) {
    if (!bar) return;
    bar.hidden = !cronCfgOpen.has(c.name);
    Array.prototype.slice.call(bar.querySelectorAll("input,select,button.seg-btn")).forEach(function (el) {
      el.disabled = !c.valid;   // 坏注册条不可面板编辑
    });
    var setVal = function (el, v) {
      if (el && document.activeElement !== el && el.value !== String(v)) el.value = String(v);
    };
    var mode = (bar._draft && bar._draft.schedule_type) ? bar._draft.schedule_type : c.schedule_type;
    var dBtn = bar.querySelector('[data-role="mode-daily"]');
    var iBtn = bar.querySelector('[data-role="mode-interval"]');
    if (dBtn) dBtn.classList.toggle("active", mode === "daily");
    if (iBtn) iBtn.classList.toggle("active", mode === "interval");
    var atList = bar.querySelector('[data-role="at"]');
    var ivEl = bar.querySelector('[data-role="every"]');
    if (atList) atList.hidden = (mode !== "daily");
    if (ivEl && ivEl.parentNode) ivEl.parentNode.hidden = (mode !== "interval");
    if (bar._draft) {
      // 编辑态：值由草稿驱动，轮询绝不覆盖（防止自动保存/回填竞态撤销输入）
      markDirty(bar, true);
      return;
    }
    markDirty(bar, false);
    if (atList && mode === "daily" && Array.isArray(c.at)) {
      var rows = atList.querySelectorAll(".cfg-at-row");
      if (rows.length !== c.at.length) {
        // 服务端数量变了 → 重建列表（未编辑状态，无输入可打断）
        cronAtRebuild(atList, atList._touch || null, c.at);
      } else {
        Array.prototype.slice.call(rows).forEach(function (row, i) {
          var inp = row.querySelector("input");
          if (inp && document.activeElement !== inp && inp.value !== c.at[i]) inp.value = c.at[i];
        });
      }
    }
    var every = bar.querySelector('[data-role="every"]');
    var unit = bar.querySelector('[data-role="unit"]');
    if (every && unit && typeof c.every_sec === "number" && c.every_sec) {
      var d = everyToDisplay(c.every_sec);
      setVal(unit, d.mult);
      setVal(every, d.val);
    }
    setVal(bar.querySelector('[data-role="timeout"]'), c.timeout_sec);
    var cu = bar.querySelector('[data-role="catch_up"]');
    if (cu) cu.checked = !!c.catch_up;
    Array.prototype.slice.call(bar.querySelectorAll("input,select,button.seg-btn")).forEach(function (el) {
      el.disabled = !c.valid;   // 坏注册条不可面板编辑（改不了根因）
    });
  }

  // ===== 拖拽排序（服务列表与定时任务共用一套把手与保存逻辑）=====
  var dragRow = null;        // 正在拖的行
  var sortBusy = false;      // 拖动期间冻结两个列表的重绘，防手势被打断
  var suppressClick = false; // 拖完浏览器可能补一次 click，抑制掉（服务卡片整行可点）

  function buildDragHandle(row) {
    var h = cfgEl("span", "drag-handle");
    h.textContent = "⠿";
    h.title = "按住拖动排序";
    // 只在把手按下时开启 draggable：整行常驻可拖会干扰选文字与点按钮
    h.addEventListener("mousedown", function () { row.draggable = true; });
    h.addEventListener("mouseup", function () { row.draggable = false; });
    h.addEventListener("click", function (e) { e.stopPropagation(); });
    return h;
  }

  function listOrderNames(listEl) {
    return Array.prototype.slice.call(listEl.querySelectorAll(".card[data-name]"))
      .map(function (r) { return r.dataset.name; });
  }

  /* 保存服务顺序必须提交**两个组合并后的完整名单**：后端 set_order 会把未提及
     的服务按字母序追加末尾，只交本组名单会把另一组刚排好的顺序冲回字母序。
     组间相对位置无意义（渲染按 mode 强制分组），拼接即可。 */
  function orderNamesFor(kind, listEl) {
    if (kind === "services" && els.listOd) {
      return listOrderNames(els.list).concat(listOrderNames(els.listOd));
    }
    return listOrderNames(listEl);
  }

  async function saveOrder(kind, names) {
    try {
      var res = await api("POST", "/api/" + kind + "/order", { names: names });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.error || data.message || ("HTTP " + res.status));
      toast("顺序已保存", "ok");
    } catch (e) {
      toast("排序保存失败：" + (e && e.message ? e.message : "未知错误"), "err");
    }
    poll(true);
  }

  function bindRowDrag(row, kind) {
    // 容器在运行时取 row.parentNode：分组后卡片可能落在任一列表里，
    // 而 dragRow.parentNode !== 目标容器 的判断天然禁止跨组拖动。
    row.addEventListener("dragstart", function (e) {
      dragRow = row;
      sortBusy = true;
      row.classList.add("dragging");
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = "move";
        try { e.dataTransfer.setData("text/plain", row.dataset.name || ""); } catch (_e) {}
      }
    });
    row.addEventListener("dragover", function (e) {
      e.preventDefault();
      var listEl = row.parentNode;
      if (!dragRow || dragRow === row || dragRow.parentNode !== listEl) return;
      var rect = row.getBoundingClientRect();
      var before = (e.clientY - rect.top) < rect.height / 2;   // 上半插前，下半插后
      listEl.insertBefore(dragRow, before ? row : row.nextSibling);
    });
    row.addEventListener("drop", function (e) { e.preventDefault(); });
    row.addEventListener("dragend", function () {
      row.classList.remove("dragging");
      row.draggable = false;
      var listEl = row.parentNode;
      dragRow = null;
      sortBusy = false;
      suppressClick = true;
      setTimeout(function () { suppressClick = false; }, 250);
      if (listEl) saveOrder(kind, orderNamesFor(kind, listEl));
    });
  }

  function renderCrons(crons) {
    if (!els.cronSection || sortBusy) return;   // 拖动中不重绘
    els.cronSection.hidden = !crons.length;
    var names = crons.map(function (c) { return c.name; });

    // 移除已消失的任务行（连带清理其数据快照与展开态）
    Array.prototype.slice.call(els.cronList.children).forEach(function (row) {
      if (row.dataset.name && names.indexOf(row.dataset.name) === -1) {
        cronData.delete(row.dataset.name);
        cronCfgOpen.delete(row.dataset.name);
        cronModeMemo.delete(row.dataset.name);
        row.remove();
      }
    });

    crons.forEach(function (c) {
      var row = els.cronList.querySelector('.cron-card[data-name="' + cssEscape(c.name) + '"]');
      if (!row) {
        row = document.createElement("div");
        row.className = "card cron-card";
        row.dataset.name = c.name;
        var inner = document.createElement("div");
        inner.className = "card-inner";
        inner.appendChild(buildDragHandle(row));
        var info = document.createElement("div");
        info.className = "info";
        var actions = document.createElement("div");
        actions.className = "actions";
        actions.appendChild(buildCronSwitch(c.name, "启用", "enabled"));
        var cfgBtn = document.createElement("button");
        cfgBtn.className = "btn btn-cfg";
        cfgBtn.textContent = "⚙ 设置";
        cfgBtn.title = "展开/收起调度配置（模式、时间、超时、补跑）";
        cfgBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          toggleCronConfig(c.name, row);
        });
        var logBtn = document.createElement("button");
        logBtn.className = "btn btn-log";
        logBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          openLog(c.name, row);
        });
        var runBtn = document.createElement("button");
        runBtn.className = "btn btn-run";
        runBtn.textContent = "▶ 立即运行";
        runBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          runCron(c.name, runBtn);
        });
        // 与服务卡片同序：操作按钮在前（启动 / 立即运行），⚙ 设置与事件在后
        actions.appendChild(runBtn);
        actions.appendChild(cfgBtn);
        actions.appendChild(logBtn);
        inner.appendChild(info);
        inner.appendChild(actions);
        row.appendChild(inner);
        row.appendChild(buildCronConfig(c));   // 配置面板（默认收起）
        bindRowDrag(row, "crons");
        els.cronList.appendChild(row);
      }

      var st = CRON_STATUS_MAP[cronStatus(c)];
      row.classList.toggle("log-open", openLogs.has(c.name));
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
      // 描述挂到任务名悬停：主行空间留给状态与调度信息，避免溢出压到右侧开关
      if (c.description) nm.title = c.description;
      info.appendChild(nm);
      info.appendChild(cronChip(c.schedule, "调度规则（超时 " + c.timeout_sec + "s" +
        (c.catch_up ? "，错过补跑" : "") + "）——点「⚙ 设置」修改"));
      if (c.running) {
        info.appendChild(cronChip("本轮运行中…"));
      } else if (c.enabled && c.valid && typeof c.next_run === "number") {
        info.appendChild(cronChip("下次 " + fmtClock(c.next_run)));
      }
      if (c.valid) info.appendChild(lastResultChip(c));
      if (c.error) {
        var er = document.createElement("span");
        er.className = "svc-error";
        er.textContent = c.error;
        er.title = c.error;
        info.appendChild(er);
      }

      cronData.set(c.name, c);   // 控件回调取最新值用
      // 记录两种模式各自的最后已知值（供模式切换时恢复，不丢原参数）
      var memo = cronModeMemo.get(c.name) || {};
      if (c.at) memo.at = c.at;
      if (typeof c.every_sec === "number" && c.every_sec) memo.every_sec = c.every_sec;
      cronModeMemo.set(c.name, memo);
      // 主行的「启用」开关同步勾选态
      var enCb = row.querySelector('.actions [data-role="enabled"]');
      if (enCb) enCb.checked = !!c.enabled;
      // 设置按钮：展开态高亮；坏注册条禁用（面板改不了根因）
      var cfgBtnEl = row.querySelector(".btn-cfg");
      if (cfgBtnEl) {
        cfgBtnEl.classList.toggle("active", cronCfgOpen.has(c.name));
        cfgBtnEl.disabled = !c.valid;
      }
      // 配置面板同步（模式高亮、参数显隐、控件值、展开态）
      syncCronConfig(row.querySelector(".cron-config"), c);

      // 事件按钮（与服务卡片同一套展开体系）
      var logBtn = row.querySelector(".btn-log");
      var isOpen = openLogs.has(c.name);
      logBtn.classList.toggle("active", isOpen);
      logBtn.textContent = isOpen ? "收起" : "事件";

      var runBtn = row.querySelector(".btn-run");
      runBtn.disabled = c.running || !c.valid || !c.enabled;

      // 跟随后端顺序：appendChild 移动已有节点（不重建、事件与焦点保留）
      els.cronList.appendChild(row);
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

  // 事件窗工具条：清空按钮（卡片窗清该服务/任务，全局抽屉清整本登记簿）
  function buildLogToolbar(name) {
    var bar = document.createElement("div");
    bar.className = "log-toolbar";
    var btn = document.createElement("button");
    btn.className = "btn btn-log-clear";
    btn.textContent = "✕ 清空";
    btn.addEventListener("click", async function (e) {
      e.stopPropagation();
      var all = (name === ALL_KEY);
      var tip = all
        ? "清空整个登记簿？（轮转归档 .1/.2/.3 不受影响，此操作不可恢复）"
        : "清空 " + name + " 的全部事件记录？（此操作不可恢复）";
      if (!window.confirm(tip)) return;
      try {
        var body = all ? {} : { svc: name };
        var res = await api("POST", "/api/audit/clear", body);
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
        toast("已清空 " + (data.removed || 0) + " 条事件", "ok");
        var card = all ? els.auditDrawer : cardByName(name);
        var panel = card ? (all ? card : card.querySelector(".log-panel")) : null;
        if (panel) refreshEvents(name, panel);
      } catch (err) {
        toast("清空失败：" + (err && err.message ? err.message : "未知错误"), "err");
      }
    });
    bar.appendChild(btn);
    return bar;
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
      // 端口被外人占：允许重试启动（占用方可能已撤）、也允许"清除"告警；
      // 不给"重启"（重复拉起无意义）
      case "conflict":
        return [{ action: "start", label: "重试启动", busy: false },
                { action: "stop", label: "清除", busy: false }];
      case "unknown":
        return [{ action: "start", label: "启动", busy: false },
                { action: "stop", label: "停止", busy: false }];
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
  // ===== 服务设置面板（⚙）：回收时长 + 关联的定时任务 =====
  var svcCfgOpen = new Set();   // 展开态跨轮询保持
  var svcData = new Map();      // name -> 最新快照（点 ⚙ 时立刻取到真值，不等下轮）

  async function saveSvcConfig(name, patch, rollback) {
    try {
      var res = await api("POST", "/api/services/" + encodeURIComponent(name) + "/config", patch);
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.error || data.message || ("HTTP " + res.status));
      toast("已保存", "ok");
    } catch (e) {
      toast("保存失败：" + (e && e.message ? e.message : "未知错误"), "err");
      if (rollback) rollback();
    }
    poll(true);
  }

  function buildSvcConfig(card) {
    var bar = cfgEl("div", "cron-config svc-config");
    bar.hidden = true;

    // ---- 分区一：空闲回收（仅 on_demand 显示；常驻服务无此项）----
    var gIdle = cfgEl("div", "cfg-group cfg-idle");
    gIdle.appendChild(cfgEl("span", "cfg-group-title", null, "空闲回收"));
    var nIn = cfgEl("input", "cfg-num", "idle_num");
    nIn.type = "number"; nIn.min = "1";
    nIn.title = "无业务请求多久后自动回收进程（60 秒 ~ 24 小时）";
    var uSel = cfgEl("select", "cfg-unit", "idle_unit");
    [[60, "分钟"], [3600, "小时"]].forEach(function (p) {
      var o = document.createElement("option");
      o.value = String(p[0]); o.textContent = p[1];
      uSel.appendChild(o);
    });
    var pushIdle = function () {
      var mult = parseFloat(uSel.value) || 60;
      var secs = Math.round((parseFloat(nIn.value) || 0) * mult);
      var prev = card.dataset.idleSec;
      saveSvcConfig(card.dataset.name, { idle_timeout_sec: secs }, function () {
        if (prev) nIn.value = String(Math.round(parseFloat(prev) / mult));
      });
    };
    nIn.addEventListener("change", pushIdle);
    uSel.addEventListener("change", pushIdle);
    gIdle.appendChild(nIn); gIdle.appendChild(uSel);
    gIdle.appendChild(cfgEl("span", "cfg-idle-left", "idle-left"));
    bar.appendChild(gIdle);

    // ---- 分区二：关联的定时任务（按路径归属，见 core/linkage.py）----
    var gCron = cfgEl("div", "cfg-group cfg-linked");
    gCron.appendChild(cfgEl("span", "cfg-group-title", null, "关联的定时任务"));
    gCron.appendChild(cfgEl("span", "linked-body", null, "—"));
    bar.appendChild(gCron);

    bar.appendChild(cfgEl("div", "cfg-hint", null,
      "回收时长＝无操作多久后释放进程（改完立即生效）；关联任务按脚本所在目录自动判定，"
      + "无需配置。此处不含启停——启停请在主行操作。"));
    return bar;
  }

  function syncSvcConfig(bar, svc) {
    if (!bar) return;
    bar.hidden = !svcCfgOpen.has(svc.name);
    var onDemand = isOnDemand(svc);
    var gIdle = bar.querySelector(".cfg-idle");
    if (gIdle) gIdle.hidden = !onDemand;

    if (onDemand) {
      var secs = svc.idle_timeout || 600;
      var nIn = bar.querySelector('[data-role="idle_num"]');
      var uSel = bar.querySelector('[data-role="idle_unit"]');
      // 默认单位：能被 3600 整除用小时，否则分钟
      var unit = (secs % 3600 === 0) ? 3600 : 60;
      var setVal = function (el, v) {
        if (el && document.activeElement !== el && el.value !== String(v)) el.value = String(v);
      };
      setVal(uSel, unit);
      setVal(nIn, Math.round(secs / unit));
      cardSetIdleSec(bar, secs);
      // 距离自动回收还剩多久（有 last_active 才有意义）
      var left = bar.querySelector(".cfg-idle-left");
      if (left) {
        if (svc.status === "running" && typeof svc.last_active === "number" && svc.last_active > 0) {
          var remain = Math.max(0, Math.round(svc.last_active + secs - Date.now() / 1000));
          left.textContent = "≈ 距回收 " + fmtUptime(remain);
          left.title = "自上次业务请求起算，空闲满 " + fmtUptime(secs) + " 即回收";
        } else if (svc.status === "running") {
          left.textContent = "";
        } else {
          left.textContent = "（未运行）";
        }
      }
    }
    renderLinkedCrons(bar, svc);
  }

  function cardSetIdleSec(bar, secs) {
    var card = bar.parentNode;
    if (card && card.dataset) card.dataset.idleSec = String(secs);
  }

  function renderLinkedCrons(bar, svc) {
    var box = bar.querySelector(".linked-body");
    if (!box) return;
    var names = svc.cron_names || [];
    if (!names.length) {
      box.textContent = "无（脚本不在本服务目录下，或尚无定时任务）";
      box.classList.add("none");
      return;
    }
    box.classList.remove("none");
    box.replaceChildren();
    names.forEach(function (cn) {
      var c = cronData.get(cn);
      var row = document.createElement("span");
      row.className = "linked-cron";
      var lab = document.createElement("span");
      lab.className = "linked-name";
      lab.textContent = c ? (c.display || c.name) : cn;
      if (c) {
        lab.title = (c.description || "") + " / " + (c.schedule || "");
        var meta = document.createElement("span");
        meta.className = "linked-meta";
        meta.textContent = c.enabled ? ("下次 " + fmtClock(c.next_run)) : "已停用";
        row.appendChild(lab); row.appendChild(meta);
        var run = document.createElement("button");
        run.className = "btn btn-mini btn-cron-run";
        run.textContent = "▶";
        run.title = "立即运行「" + lab.textContent + "」";
        run.addEventListener("click", function (e) {
          e.stopPropagation();
          runCron(cn, run);
        });
        row.appendChild(run);
      } else {
        row.appendChild(lab);   // 快照里还没有（首轮未到），先占位显示名字
      }
      box.appendChild(row);
    });
  }

  function buildCardSkeleton(card) {
    card.replaceChildren();

    var inner = document.createElement("div");
    inner.className = "card-inner";

    inner.appendChild(buildDragHandle(card));   // 行首把手：按住才能拖动排序

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

    var cfgBtn = document.createElement("button");
    cfgBtn.className = "btn btn-cfg";
    cfgBtn.textContent = "⚙ 设置";
    cfgBtn.title = "展开/收起设置（空闲回收时长、关联的定时任务）";
    cfgBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var nm = card.dataset.name;
      var bar = card.querySelector(".svc-config");
      if (svcCfgOpen.has(nm)) { svcCfgOpen.delete(nm); if (bar) bar.hidden = true; }
      else {
        svcCfgOpen.add(nm);
        if (bar) syncSvcConfig(bar, svcData.get(nm) || { name: nm });
      }
    });

    actions.appendChild(toggle);
    actions.appendChild(opGroup);
    actions.appendChild(cfgBtn);
    actions.appendChild(logBtn);
    inner.appendChild(info);
    inner.appendChild(actions);
    card.appendChild(inner);
    card.appendChild(buildSvcConfig(card));   // 设置面板（默认收起）

    // 整行点击跳转（懒打开：先弹中转页，就绪后跳转）
    card.addEventListener("click", function () {
      if (suppressClick) return;   // 刚拖完的补发 click 不算点击
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

    bindRowDrag(card, "services");
    card.dataset.built = "1";
  }

  function renderCard(svc, card) {
    svcData.set(svc.name, svc);            // 供 ⚙ 点击时立刻取到最新值
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
    if (status === "stopped" || status === "failed" || status === "running"
        || status === "conflict" || status === "unknown") {
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

    // 设置面板：⚙ 激活态 + 每轮同步值（展开态跨轮询保持）
    var cfgBtn2 = card.querySelector(".btn-cfg");
    var cfgOpen = svcCfgOpen.has(svc.name);
    if (cfgBtn2) {
      cfgBtn2.classList.toggle("active", cfgOpen);
      cfgBtn2.textContent = cfgOpen ? "收起" : "⚙ 设置";
    }
    syncSvcConfig(card.querySelector(".svc-config"), svc);
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
      var toolbar = panel.querySelector(".log-toolbar");
      panel.replaceChildren();
      if (!toolbar) toolbar = buildLogToolbar(name);   // 首次注入；重建行时保留
      panel.appendChild(toolbar);
      if (!events.length) {
        var empty = document.createElement("div");
        empty.className = "log-empty";
        empty.textContent = "（暂无事件 —— 任务/服务的业务输出由其自己记录，框架只记编排动作）";
        panel.appendChild(empty);
      } else {
        events.forEach(function (ev) {
          var fail = ev.ok === false;
          var row = document.createElement("div");
          row.className = "ev" + (fail ? " fail" : "");
          var ts = document.createElement("span");
          ts.className = "ev-ts";
          ts.textContent = String(ev.ts || "").slice(5, 19);   // MM-DD HH:MM:SS（跨天可区分）
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

  /* 渲染一个服务分组：卡片移动到本组容器、组与标题一起显隐、标题带数量。
     appendChild 移动已有节点（不重建，事件与焦点保留）——沿用原实现的抗抖动策略。 */
  function renderSvcGroup(listEl, items, secEl, cntId) {
    if (!listEl) return;
    items.forEach(function (svc) {
      var card = cardByName(svc.name);
      if (!card) {
        card = document.createElement("div");
        card.className = "card";
        card.dataset.name = svc.name;
      }
      renderCard(svc, card);
      listEl.appendChild(card);
    });
    if (secEl) secEl.hidden = items.length === 0;
    var cnt = cntId ? document.getElementById(cntId) : null;
    if (cnt) cnt.textContent = String(items.length);
  }

  // ===== 渲染 + 轮询 =====
  async function render(data, host) {
    if (sortBusy) { poll(false); return; }   // 拖动中冻结重绘，但轮询继续
    var names = data.map(function (s) { return s.name; });
    var running = data.filter(function (s) { return s.status === "running"; }).length;

    els.empty.hidden = data.length > 0;
    els.text.textContent = running + "/" + data.length + " 运行中";
    els.text.classList.remove("off");

    // 宿主自身运行时长（区别于单个服务的 uptime）+ 版本号钉在标题旁
    if (host && typeof host.uptime_sec === "number") {
      els.hostUptime.textContent = "宿主运行 " + fmtUptime(host.uptime_sec);
      els.hostUptime.title = "my-service 自身已运行 " + fmtUptime(host.uptime_sec)
        + "（pid " + host.pid + "，v" + host.version + "）";
    }
    if (host && host.version && els.verTag) {
      els.verTag.hidden = false;
      els.verTag.textContent = "v" + host.version;
    }
    var pending = data.some(function (s) {
      return s.status === "starting" || s.status === "stopping";
    });
    els.hostRestart.disabled = pending || !!hostAction;
    els.hostStop.disabled = pending || !!hostAction;

    // 清理已注销的卡片（两个容器都要查），再按 mode 分派到各自分组
    eachSvcList(function (l) {
      Array.prototype.slice.call(l.children).forEach(function (card) {
        var nm = card.dataset.name;
        if (nm && names.indexOf(nm) === -1) {
          if (openLogs.has(nm)) { openLogs.delete(nm); logAtBottom.delete(nm); }
          stableLayout.delete(nm);
          svcCfgOpen.delete(nm);
          svcData.delete(nm);
          card.remove();
        }
      });
    });

    var grpPd = [], grpOd = [];
    data.forEach(function (svc) { (isOnDemand(svc) ? grpOd : grpPd).push(svc); });
    renderSvcGroup(els.list, grpPd, els.secPd, "cntPd");
    renderSvcGroup(els.listOd, grpOd, els.secOd, "cntOd");

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
    if (!fast) {
      // 两组都要查：starting/stopping 可能出现在任一容器里
      eachSvcList(function (l) {
        if (!fast && l.querySelector(".status-badge.starting, .status-badge.stopping")) {
          fast = true;
        }
      });
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
