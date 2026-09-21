/* 博物馆展品光照剂量审计 —— 原生前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { cases: [], exhibits: [], audit: null };

/* ---------- 基础工具 ---------- */

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2600);
}

const fmtNum = (n) => n == null ? "—" : Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 1 });
const fmtTs = (s) => s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";
const fmtDate = (s) => s ? new Date(s).toLocaleDateString("zh-CN") : "—";

/* ---------- 数据加载与渲染 ---------- */

async function refresh() {
  [state.cases, state.exhibits] = await Promise.all([
    api("/api/cases"), api("/api/exhibits"),
  ]);
  renderCases();
  renderExhibits();
  fillSelects();
}

function renderCases() {
  const tb = $("cases-table").querySelector("tbody");
  tb.innerHTML = "";
  for (const c of state.cases) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${c.id}</td>
      <td>${esc(c.name)}</td>
      <td>${c.reading_count}</td>
      <td>${c.first_reading ? fmtDate(c.first_reading) + " ~ " + fmtDate(c.last_reading) : "—"}</td>
      <td>${fmtNum(c.avg_daily_dose_lux_hours)}</td>
      <td><input type="file" accept=".jsonl,.txt" data-case="${c.id}"></td>
      <td><button class="danger" data-del-case="${c.id}">删除</button></td>`;
    tb.appendChild(tr);
  }
  tb.querySelectorAll('input[type=file]').forEach((inp) =>
    inp.addEventListener("change", () => uploadReadings(inp)));
  tb.querySelectorAll("[data-del-case]").forEach((b) =>
    b.addEventListener("click", () => delCase(b.dataset.delCase)));
}

function renderExhibits() {
  const tb = $("exhibits-table").querySelector("tbody");
  tb.innerHTML = "";
  for (const e of state.exhibits) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${e.id}</td><td>${esc(e.name)}</td><td>${esc(e.material) || "—"}</td>
      <td>${fmtNum(e.annual_limit_lux_hours)}</td><td>${e.placement_count}</td>
      <td><button class="danger" data-del-ex="${e.id}">删除</button></td>`;
    tb.appendChild(tr);
  }
  tb.querySelectorAll("[data-del-ex]").forEach((b) =>
    b.addEventListener("click", () => delExhibit(b.dataset.delEx)));
}

function fillSelects() {
  const exOpts = state.exhibits.map((e) => `<option value="${e.id}">#${e.id} ${esc(e.name)}</option>`).join("");
  const caseOpts = state.cases.map((c) => `<option value="${c.id}">#${c.id} ${esc(c.name)}</option>`).join("");
  for (const id of ["pl-exhibit", "audit-exhibit"]) {
    const sel = $(id);
    const prev = sel.value;
    sel.innerHTML = exOpts || "<option value=''>（请先添加展品）</option>";
    sel.value = prev;
  }
  for (const id of ["pl-case", "sim-target"]) {
    const sel = $(id);
    const prev = sel.value;
    sel.innerHTML = caseOpts || "<option value=''>（请先添加展柜）</option>";
    sel.value = prev;
  }
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

/* ---------- 操作 ---------- */

async function uploadReadings(inp) {
  const file = inp.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api(`/api/cases/${inp.dataset.case}/readings`, { method: "POST", body: fd });
    toast(`导入 ${r.inserted} 条,跳过重复 ${r.duplicates_skipped} 条,错误 ${r.error_count} 条`);
    refresh();
  } catch (e) { toast("上传失败:" + e.message); }
  inp.value = "";
}

async function delCase(id) {
  if (!confirm(`删除展柜 #${id} 及其全部读数?`)) return;
  await api(`/api/cases/${id}`, { method: "DELETE" });
  refresh();
}

async function delExhibit(id) {
  if (!confirm(`删除展品 #${id} 及其在柜记录?`)) return;
  await api(`/api/exhibits/${id}`, { method: "DELETE" });
  refresh();
}

/* ---------- 审计 ---------- */

async function runAudit() {
  const id = $("audit-exhibit").value;
  if (!id) return toast("请先选择展品");
  const gap = $("gap-hours").value || 6;
  try {
    state.audit = await api(`/api/exhibits/${id}/audit?gap_hours=${gap}`);
  } catch (e) { return toast("审计失败:" + e.message); }
  renderAuditSummary();
  drawDoseChart();
  renderGaps();
  $("btn-export").disabled = false;
  $("btn-simulate").disabled = false;
}

function renderAuditSummary() {
  const a = state.audit;
  const over = a.total_dose_lux_hours >= a.annual_limit_lux_hours;
  $("audit-summary").innerHTML = `
    <div class="stat ${over ? "warn" : ""}">
      <div class="v">${fmtNum(a.total_dose_lux_hours)}</div>
      <div class="k">累计剂量 (lux·h)</div>
    </div>
    <div class="stat"><div class="v">${fmtNum(a.annual_limit_lux_hours)}</div>
      <div class="k">年度上限 (lux·h)</div></div>
    <div class="stat ${over ? "warn" : ""}">
      <div class="v">${fmtNum(a.limit_used_pct)}%</div>
      <div class="k">上限已用${over ? "（已超限!）" : ""}</div>
      <div class="pct-bar ${over ? "over" : ""}"><div style="width:${Math.min(100, a.limit_used_pct)}%"></div></div>
    </div>
    <div class="stat"><div class="v">${a.gap_evidence.length}</div>
      <div class="k">跨断点区间(阈值 ${a.gap_hours_threshold}h)</div></div>
    <div class="stat"><div class="v">${a.segments.length}</div>
      <div class="k">在柜时间段</div></div>`;
}

function renderGaps() {
  const tb = $("gaps-table").querySelector("tbody");
  tb.innerHTML = "";
  const gaps = state.audit.gap_evidence;
  if (!gaps.length) {
    tb.innerHTML = `<tr><td colspan="5" style="color:var(--muted)">无跨断点区间 ✓</td></tr>`;
    return;
  }
  for (const g of gaps) {
    const tr = document.createElement("tr");
    tr.className = "gap-row";
    tr.innerHTML = `<td>${esc(g.case_name)}</td><td>${fmtTs(g.start)}</td>
      <td>${fmtTs(g.end)}</td><td>${fmtNum(g.hours)}</td><td>${fmtNum(g.dose_lux_hours)}</td>`;
    tb.appendChild(tr);
  }
}

/* Canvas:累计剂量曲线,断点区间红色背景 */
function drawDoseChart() {
  const cv = $("dose-chart");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  const pad = { l: 70, r: 20, t: 20, b: 40 };
  ctx.clearRect(0, 0, W, H);

  const a = state.audit;
  const segs = a.segments.filter((s) => s.curve.length);
  if (!segs.length) {
    ctx.fillStyle = "#8a8175";
    ctx.font = "14px sans-serif";
    ctx.fillText("（无剂量数据 —— 请检查在柜时间段与读数覆盖范围）", pad.l, H / 2);
    return;
  }

  // 拼接各在柜段曲线(累计偏移)
  const pts = [];
  let offset = 0;
  const gapSpans = []; // {t1, t2} 用于红色背景
  for (const s of segs) {
    for (const p of s.curve) pts.push({ t: new Date(p.ts).getTime(), dose: p.dose + offset });
    for (const g of s.gaps) gapSpans.push({ t1: new Date(g.start).getTime(), t2: new Date(g.end).getTime() });
    offset += s.dose_lux_hours;
  }
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const yMax = Math.max(a.annual_limit_lux_hours, offset) * 1.08;
  const X = (t) => pad.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - pad.l - pad.r);
  const Y = (d) => H - pad.b - (d / yMax) * (H - pad.t - pad.b);

  // 断点背景
  ctx.fillStyle = "rgba(179,64,46,.14)";
  for (const g of gapSpans) {
    const x1 = X(g.t1), x2 = Math.max(X(g.t2), x1 + 2);
    ctx.fillRect(x1, pad.t, x2 - x1, H - pad.t - pad.b);
  }

  // 网格与 Y 轴刻度
  ctx.strokeStyle = "#e3ddd2";
  ctx.fillStyle = "#8a8175";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const d = (yMax / 4) * i, y = Y(d);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
    ctx.fillText(fmtNum(d), pad.l - 6, y + 4);
  }
  // X 轴日期刻度
  ctx.textAlign = "center";
  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) / 4) * i;
    ctx.fillText(new Date(t).toLocaleDateString("zh-CN"), X(t), H - pad.b + 18);
  }

  // 年度上限线
  ctx.strokeStyle = "#b3402e";
  ctx.setLineDash([6, 4]);
  ctx.beginPath();
  ctx.moveTo(pad.l, Y(a.annual_limit_lux_hours));
  ctx.lineTo(W - pad.r, Y(a.annual_limit_lux_hours));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#b3402e";
  ctx.textAlign = "left";
  ctx.fillText(`年度上限 ${fmtNum(a.annual_limit_lux_hours)} lux·h`, pad.l + 6, Y(a.annual_limit_lux_hours) - 6);

  // 剂量曲线
  ctx.strokeStyle = "#2b6f7a";
  ctx.lineWidth = 2;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(X(p.t), Y(p.dose)) : ctx.moveTo(X(p.t), Y(p.dose))));
  ctx.stroke();
  ctx.lineWidth = 1;
}

/* ---------- 轮换模拟 ---------- */

async function runSimulate() {
  const exhibitId = $("audit-exhibit").value;
  const targetId = $("sim-target").value;
  if (!exhibitId || !targetId) return toast("请先运行审计并选择目标展柜");
  let r;
  try {
    r = await api("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        exhibit_id: Number(exhibitId),
        target_case_id: Number(targetId),
        gap_hours: Number($("gap-hours").value || 6),
      }),
    });
  } catch (e) { return toast("模拟失败:" + e.message); }

  const box = (title, cls, p) => `
    <div class="box ${cls}">
      <h4>${title}</h4>
      <p>历史平均日剂量:<b>${fmtNum(p.daily_rate_lux_hours)}</b> lux·h/天</p>
      <p>预计达限日期:<b>${p.predicted_limit_date ? fmtDate(p.predicted_limit_date) : (r.already_exceeded ? "已超限" : "无法预测(无数据)")}</b></p>
      <p>距达限天数:<b>${p.days_to_limit == null ? "—" : fmtNum(p.days_to_limit)}</b> 天</p>
    </div>`;

  let verdict;
  if (r.already_exceeded) {
    verdict = `<div class="verdict bad">⚠️ 展品累计剂量 ${fmtNum(r.current_dose_lux_hours)} lux·h 已超过年度上限,应立即撤展或转入暗库。</div>`;
  } else if (r.days_gained_by_rotation != null) {
    const good = r.days_gained_by_rotation > 0;
    verdict = `<div class="verdict ${good ? "" : "bad"}">
      ${good ? "✅" : "⚠️"} 移入「${esc(r.target_case.name)}」后,达限时间${good ? "延后" : "提前"}
      <b>${fmtNum(Math.abs(r.days_gained_by_rotation))}</b> 天。</div>`;
  } else {
    verdict = "";
  }

  $("sim-result").innerHTML = `
    <p>当前累计剂量 <b>${fmtNum(r.current_dose_lux_hours)}</b> / ${fmtNum(r.annual_limit_lux_hours)} lux·h,
    剩余额度 <b>${fmtNum(r.remaining_lux_hours)}</b> lux·h</p>
    <div class="compare">
      ${r.current_case ? box(`现状:留在「${esc(r.current_case.name)}」`, "", r.current_case) : ""}
      ${box(`轮换:移入「${esc(r.target_case.name)}」`, "target", r.target_case)}
    </div>
    ${verdict}`;
}

/* ---------- 事件绑定 ---------- */

$("btn-add-case").addEventListener("click", async () => {
  const name = $("case-name").value.trim();
  if (!name) return;
  try {
    await api("/api/cases", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    $("case-name").value = "";
    refresh();
  } catch (e) { toast(e.message); }
});

$("btn-add-exhibit").addEventListener("click", async () => {
  const name = $("ex-name").value.trim();
  if (!name) return toast("请填写展品名称");
  try {
    await api("/api/exhibits", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        material: $("ex-material").value.trim(),
        annual_limit_lux_hours: Number($("ex-limit").value || 50000),
      }),
    });
    $("ex-name").value = "";
    refresh();
  } catch (e) { toast(e.message); }
});

$("btn-add-placement").addEventListener("click", async () => {
  const exhibitId = $("pl-exhibit").value, caseId = $("pl-case").value;
  const start = $("pl-start").value, end = $("pl-end").value;
  const ongoing = $("pl-ongoing").checked;
  if (!exhibitId || !caseId || !start) return toast("请选择展品、展柜并填写开始时间");
  if (!ongoing && !end) return toast("请填写结束时间或勾选「至今在柜」");
  try {
    await api(`/api/exhibits/${exhibitId}/placements`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        case_id: Number(caseId),
        start: new Date(start).toISOString(),
        end: ongoing ? null : new Date(end).toISOString(),
      }),
    });
    refresh();
    toast("已添加在柜时间段");
  } catch (e) { toast(e.message); }
});

$("btn-audit").addEventListener("click", runAudit);
$("btn-simulate").addEventListener("click", runSimulate);

$("btn-export").addEventListener("click", () => {
  const id = $("audit-exhibit").value;
  const gap = $("gap-hours").value || 6;
  window.location = `/api/exhibits/${id}/export?gap_hours=${gap}`;
});

$("btn-demo").addEventListener("click", async () => {
  try {
    const r = await api("/api/demo", { method: "POST" });
    toast(`演示数据已载入(展品 #${r.exhibit_id}),请点击「运行审计」`);
    await refresh();
    $("audit-exhibit").value = String(r.exhibit_id);
    await runAudit();
  } catch (e) { toast(e.message); }
});

refresh().catch((e) => toast("初始化失败:" + e.message));
