/* LeadGen Studio — منطق داشبورد (بدون هیچ کتابخانه بیرونی)
 * همه داده‌ها از API همان سرور گرفته می‌شود؛ هیچ منبع خارج از دامنه بارگذاری نمی‌شود.
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const state = {
  catalog: null, health: null, runs: [], currentRun: null,
  leads: [], leadCount: 0, page: 0, pageSize: 25,
  filters: { q: "", segment: "", status: "", channel: "", gap: "", sort: "score" },
  pollTimer: null, niche: null, sender: null,
};

/* ── ابزارها ─────────────────────────────────────────────────────────────── */
async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
  }
  if (opts.body instanceof FormData) delete opts.headers["Content-Type"];
  const res = await fetch(path, opts);
  const type = res.headers.get("Content-Type") || "";
  const payload = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((payload && payload.error) || `HTTP ${res.status}`);
  return payload;
}
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fa = (n) => Number(n || 0).toLocaleString("fa-IR");
const num = (n) => Number(n || 0).toLocaleString("en-US");

function toast(message, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = ".3s"; }, 3200);
  setTimeout(() => el.remove(), 3600);
}
function empty(text = "داده‌ای نیست") {
  return `<div class="empty">${esc(text)}</div>`;
}
function download(url, filename) {
  const a = document.createElement("a");
  a.href = url; a.download = filename || "";
  document.body.appendChild(a); a.click(); a.remove();
  toast("دانلود شروع شد");
}

/* ── تب‌ها ─────────────────────────────────────────────────────────────────── */
$$(".tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
function switchView(view) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  if (view === "runs") loadRuns();
  if (view === "leads") loadLeads();
  if (view === "analysis") loadAnalysis();
  if (view === "settings") loadDoctor();
  if (view === "campaign") prepareCampaign();
}

/* ── شروع ─────────────────────────────────────────────────────────────────── */
(async function boot() {
  try {
    const [health, catalog] = await Promise.all([api("/api/health"), api("/api/catalog")]);
    state.health = health; state.catalog = catalog;
    renderEngine(health.engine);
    renderCatalog(catalog);
    renderGlobalStats(health.db);
  } catch (err) {
    toast(`خطا در اتصال به سرور: ${err.message}`, "err");
  }
})();

function renderEngine(engine) {
  const dot = $("#engineDot");
  dot.className = `dot ${engine.active === "docker" ? "up" : engine.active === "demo" ? "demo" : "down"}`;
  $("#engineName").textContent = engine.active === "docker" ? "موتور واقعی (داکر)"
    : engine.active === "demo" ? "موتور دمو (آفلاین)" : "موتور در دسترس نیست";
  $("#engineNote").textContent = engine.reason;
  $("#engineBox").title = `API موتور: ${engine.base_url}`;
}

function renderCatalog(catalog) {
  // اصناف
  const datalist = $("#nicheList");
  datalist.innerHTML = catalog.niches
    .map((n) => `<option value="${esc(n.fa)}">${esc(n.group)} — ${esc(n.en)}</option>`).join("");
  const groups = $("#nicheGroups");
  groups.innerHTML = catalog.groups
    .map((g) => `<button class="chip" data-group="${esc(g)}">${esc(g)}</button>`).join("");
  groups.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".chip");
    if (!btn) return;
    $$(".chip", groups).forEach((c) => c.classList.toggle("active", c === btn));
    const items = catalog.niches.filter((n) => n.group === btn.dataset.group).slice(0, 14);
    groups.innerHTML = items
      .map((n) => `<button class="chip" data-niche="${esc(n.fa)}">${esc(n.fa)}</button>`).join("");
    groups.addEventListener("click", (e2) => {
      const b2 = e2.target.closest(".chip");
      if (b2 && b2.dataset.niche) { $("#nicheInput").value = b2.dataset.niche; previewKeywords(); }
    });
  });

  // شهرها
  $("#cityList").innerHTML = catalog.cities
    .map((c) => `<option value="${esc(c.fa || c.label)}">${esc(c.country)} — ${esc(c.label)}</option>`).join("");

  // پیش‌تنظیم ستون‌های خروجی
  const presets = catalog.column_presets || [];
  $("#colsSelect").innerHTML = presets
    .map((p) => `<option value="${p.id}">${esc(p.fa)}</option>`).join("");
  $("#colsSelect").value = presets.some((p) => p.id === "contact") ? "contact" : (presets[0] || {}).id;
  updateColsHint();
  $("#colsSelect").addEventListener("change", updateColsHint);

  // دسته‌های قابل انتخاب برای «جست‌وجوی تمامی مشاغل»
  const sweepGroups = $("#sweepGroups");
  sweepGroups.innerHTML = catalog.groups
    .map((g) => `<button class="chip" data-sweep-group="${esc(g)}">${esc(g)}</button>`).join("");
  sweepGroups.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-sweep-group]");
    if (btn) btn.classList.toggle("active");
  });
  $("#allNichesChk").addEventListener("change", (ev) => {
    $("#sweepOptions").hidden = !ev.target.checked;
    $("#nicheInput").disabled = ev.target.checked;
    if (ev.target.checked) {
      $("#keywordsPreview").innerHTML =
        `<span class="kw">جاروی ${$("#maxNiches").value || 12} صنف در «${esc($("#cityInput").value || "شهر")}»</span>`;
      $("#nicheHint").textContent = "در حالت «تمامی مشاغل» صنف از فهرست جارو می‌آید.";
    } else {
      $("#nicheInput").disabled = false;
      previewKeywords();
    }
  });
  ["#maxNiches", "#kwPerNiche"].forEach((sel) =>
    $(sel).addEventListener("input", () => { if ($("#allNichesChk").checked) $("#allNichesChk").dispatchEvent(new Event("change")); }));

  // پروفایل‌ها
  $("#profileSelect").innerHTML = catalog.profiles
    .map((p) => `<option value="${p.id}">${esc(p.fa)}</option>`).join("");
  $("#profileSelect").value = "web_design";
  updateProfileHint();
  $("#profileSelect").addEventListener("change", updateProfileHint);

  // کانال‌ها و وضعیت‌ها
  $("#campChannel").innerHTML = catalog.channels.concat([{ id: "phone", fa: "سناریوی تماس تلفنی" }])
    .map((c) => `<option value="${c.id}">${esc(c.fa)}</option>`).join("");
  $("#statusFilter").innerHTML = `<option value="">همه وضعیت‌ها</option>` + catalog.lead_statuses
    .map((s) => `<option value="${s.id}">${esc(s.fa)}</option>`).join("");
  $("#channelFilter").innerHTML = `<option value="">همه کانال‌ها</option>` +
    Object.entries(catalog.channels.reduce((acc, c) => (acc[c.id] = c.fa, acc), {}))
      .map(([id, label]) => `<option value="${id}">${esc(label)}</option>`).join("");
  $("#gapFilter").innerHTML = `<option value="">همه شکاف‌ها</option>` +
    Object.entries(catalog.gap_labels).map(([id, label]) => `<option value="${id}">${esc(label)}</option>`).join("");

  // رویدادهای فرم
  $("#nicheInput").addEventListener("input", debounce(previewKeywords, 450));
  $("#cityInput").addEventListener("input", debounce(previewKeywords, 450));
  $("#langSelect").addEventListener("change", previewKeywords);
  $("#depthInput").addEventListener("input", updateLargeWarn);
  $("#gridSelect").addEventListener("change", () => { previewKeywords(); updateLargeWarn(); });
  $("#manualKeywords").addEventListener("input", previewKeywords);
  $("#startBtn").addEventListener("click", () => startRun(false));
  $("#demoBtn").addEventListener("click", () => startRun(true));
  $("#openRunBtn").addEventListener("click", () => {
    if (state.currentRun) { switchView("leads"); }
  });

  previewKeywords();
}

function updateProfileHint() {
  const profile = state.catalog.profiles.find((p) => p.id === $("#profileSelect").value);
  if (!profile) return;
  const parts = Object.entries(profile.weights)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${labelFa(k)}: ${fa(v)}`).join(" · ");
  $("#profileHint").textContent = `وزن عامل‌ها → ${parts} | آستانه «داغ»: ${fa(profile.hot_threshold)}`;
}
const FACTOR_FA = {
  digital_gap: "شکاف دیجیتال", reachability: "دسترس‌پذیری", reputation: "اعتبار",
  activity: "فعالیت", fit: "تناسب صنف",
};
const labelFa = (key) => FACTOR_FA[key] || key;

function updateColsHint() {
  const preset = (state.catalog.column_presets || []).find((p) => p.id === $("#colsSelect").value);
  if (!preset) return;
  const cols = preset.columns || [];
  $("#colsHint").textContent = `${preset.note} — ${cols.length} ستون`;
  $("#colsHint").title = cols.join(" · ");
}

function debounce(fn, ms) {
  let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

async function previewKeywords() {
  const nicheQuery = $("#nicheInput").value.trim();
  const city = $("#cityInput").value.trim();
  const manual = $("#manualKeywords").value.trim();
  const box = $("#keywordsPreview");
  if (!nicheQuery && !manual) { box.innerHTML = "—"; return; }
  if (manual) {
    box.innerHTML = manual.split("\n").filter(Boolean).slice(0, 12)
      .map((k) => `<span class="kw">${esc(k.trim())}${city ? ` <small>(${esc(city)})</small>` : ""}</span>`).join("");
    $("#nicheHint").textContent = "کلیدواژه دستی فعال است — صنف فقط برای امتیازدهی استفاده می‌شود.";
    updateLargeWarn();
    return;
  }
  try {
    const found = await api("/api/niches/find", { method: "POST", body: { query: nicheQuery, city } });
    state.niche = found.niche;
    box.innerHTML = found.sample_keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join("");
    $("#nicheHint").innerHTML = found.niche.custom
      ? `صنف سفارشی ساخته شد: <b>${esc(found.niche.fa)}</b> (هر عبارتی پذیرفته می‌شود)`
      : `صنف یافت شد: <b>${esc(found.niche.fa)}</b> — ${esc(found.niche.en)} | نیاز دیجیتال: ${esc(found.niche.digital_need)}`;
    $("#depthInput").value = found.niche.depth || 5;
  } catch {
    box.innerHTML = "—";
  }
  updateLargeWarn();
}

function updateLargeWarn() {
  const depth = Number($("#depthInput").value || 0);
  const grid = Number($("#gridSelect").value || 0);
  const tiles = grid > 1 ? grid * grid : 1;
  const keywords = $("#keywordsPreview").querySelectorAll(".kw").length;
  $("#largeWarn").hidden = !(depth >= 15 || keywords >= 10 || tiles >= 4);
}

function collectRequest(forceDemo) {
  const manual = $("#manualKeywords").value.trim();
  return {
    niche_query: $("#nicheInput").value.trim() || (manual ? manual.split("\n")[0] : ""),
    city_query: $("#cityInput").value.trim(),
    lat: $("#latInput").value.trim(),
    lon: $("#lonInput").value.trim(),
    keywords: manual ? manual.split("\n").map((s) => s.trim()).filter(Boolean) : [],
    lang: $("#langSelect").value,
    depth: Number($("#depthInput").value || 5),
    grid: Number($("#gridSelect").value || 0),
    emails: $("#emailsChk").checked,
    deep_emails: $("#deepChk").checked,
    socials: $("#socialsChk").checked,
    signals: $("#signalsChk").checked,
    fast_mode: $("#fastChk").checked,
    keep_full_columns: $("#fullChk").checked,
    profile: $("#profileSelect").value,
    export_fmt: $("#exportSelect").value,
    cols: $("#colsSelect").value,
    limit: Number($("#limitInput").value || 0),
    all_niches: $("#allNichesChk").checked,
    niche_groups: $$("#sweepGroups .chip.active").map((c) => c.dataset.sweepGroup),
    max_niches: Number($("#maxNiches").value || 12),
    keywords_per_niche: Number($("#kwPerNiche").value || 2),
    engine_mode: forceDemo ? "demo" : $("#engineSelect").value,
    sender: senderFromForm(),
    campaign_channel: "email",
  };
}

async function startRun(forceDemo) {
  const req = collectRequest(forceDemo);
  if (!req.all_niches && !req.niche_query && !req.keywords.length) {
    toast("صنف یا کلیدواژه را بنویسید (یا «جست‌وجوی تمامی مشاغل» را روشن کنید)", "err"); return;
  }
  if (!req.city_query && !(req.lat && req.lon)) { toast("شهر یا مختصات لازم است", "err"); return; }

  $("#startBtn").disabled = true; $("#demoBtn").disabled = true;
  $("#progressCard").hidden = false;
  $("#progressBar").className = "bar"; $("#progressBar").style.width = "3%";
  $("#progressLog").textContent = "";
  $("#progressActions").hidden = true;
  $("#progressText").textContent = "در حال ارسال درخواست…";

  try {
    const started = await api("/api/runs", { method: "POST", body: req });
    state.currentRun = started.run_id;
    pollRun(started.run_id);
  } catch (err) {
    toast(`اجرا شروع نشد: ${err.message}`, "err");
    $("#startBtn").disabled = false; $("#demoBtn").disabled = false;
  }
}

async function pollRun(runId) {
  clearInterval(state.pollTimer);
  const tick = async () => {
    try {
      const detail = await api(`/api/runs/${runId}`);
      const progress = detail.progress || {};
      const run = detail.run || {};
      const percent = Math.max(2, Math.min(100, Number(progress.percent || 0)));
      $("#progressBar").style.width = `${percent}%`;
      $("#progressText").textContent = `${fa(percent)}٪ — ${progress.message || run.status_fa || ""}`;
      if (progress.message) appendLog(progress.message);

      if (progress.status === "done" || run.status === "done") {
        clearInterval(state.pollTimer);
        $("#progressBar").style.width = "100%"; $("#progressBar").classList.add("done");
        $("#progressActions").hidden = false;
        $("#startBtn").disabled = false; $("#demoBtn").disabled = false;
        const stats = progress.stats || {};
        toast(`تکمیل شد: ${fa(stats.total || run.total)} لید، ${fa(stats.hot || run.hot)} داغ`);
        appendLog(`✔ ${fa(stats.total || run.total)} لید | داغ ${fa(stats.hot || run.hot)} | گرم ${fa(stats.warm || run.warm)} | سرد ${fa(stats.cold || run.cold)}`);
        (stats.keywords || run.keywords || []).forEach((k) => appendLog(`   • ${k}`));
        (progress.warnings || []).forEach((w) => appendLog(`   ⚠ ${w}`));
        (progress.export_paths || []).forEach((p) => appendLog(`   📄 ${p}`));
        state.currentRun = runId;
        loadRuns();
      } else if (progress.status === "failed" || run.status === "failed") {
        clearInterval(state.pollTimer);
        $("#progressBar").classList.add("failed");
        $("#startBtn").disabled = false; $("#demoBtn").disabled = false;
        appendLog(`✖ ${progress.error || run.error || "خطای نامشخص"}`);
        toast("اجرا ناموفق بود — جزئیات در گزارش", "err");
      }
    } catch (err) {
      appendLog(`(خطای شبکه در نظرسنجی: ${err.message})`);
    }
  };
  await tick();
  state.pollTimer = setInterval(tick, 1200);
}
function appendLog(line) {
  const box = $("#progressLog");
  const stamp = new Date().toLocaleTimeString("fa-IR");
  box.textContent += `[${stamp}] ${line}\n`;
  box.scrollTop = box.scrollHeight;
}

/* ── اجراها ───────────────────────────────────────────────────────────────── */
async function loadRuns() {
  try {
    const data = await api("/api/runs?limit=60");
    state.runs = data.runs;
    renderGlobalStats(data.stats);
    const body = $("#runsTable tbody");
    if (!data.runs.length) { body.innerHTML = `<tr><td colspan="10">${empty("هنوز اجرایی ندارید — از تب «اسکراپ جدید» شروع کنید")}</td></tr>`; return; }
    body.innerHTML = data.runs.map((run) => `
      <tr data-run="${esc(run.id)}">
        <td class="num">${esc(run.id)}</td>
        <td>${esc(run.niche_fa || "—")}</td>
        <td>${esc(run.city || "—")}</td>
        <td><span class="badge ${run.live ? "ok" : "muted"}">${esc(run.engine)}</span></td>
        <td class="num">${fa(run.total)}</td>
        <td class="num"><span class="badge hot">${fa(run.hot)}</span></td>
        <td class="num">${fa(run.avg_score)}</td>
        <td>${esc(run.status_fa)}</td>
        <td class="num">${new Date((run.created_at || 0) * 1000).toLocaleDateString("fa-IR")}</td>
        <td>
          <button class="ghost" data-act="open">باز کردن</button>
          <button class="ghost" data-act="xlsx">⬇</button>
          <button class="ghost" data-act="del">🗑</button>
        </td>
      </tr>`).join("");
    body.onclick = async (ev) => {
      const row = ev.target.closest("tr"); if (!row) return;
      const runId = row.dataset.run;
      const act = ev.target.dataset.act;
      if (!act) { state.currentRun = runId; switchView("leads"); return; }
      if (act === "open") { state.currentRun = runId; switchView("leads"); }
      if (act === "xlsx") download(`/api/runs/${runId}/export?fmt=xlsx`, `leads-${runId}.xlsx`);
      if (act === "del") {
        if (!confirm("این اجرا و همه لیدهایش حذف شود؟")) return;
        await api(`/api/runs/${runId}`, { method: "DELETE" });
        toast("حذف شد"); loadRuns();
      }
    };
  } catch (err) { toast(err.message, "err"); }
}
function renderGlobalStats(stats) {
  if (!stats) return;
  $("#globalStats").innerHTML = `
    <span>اجراها <b>${fa(stats.runs)}</b></span>
    <span>لیدها <b>${fa(stats.leads)}</b></span>
    <span>با ایمیل <b>${fa(stats.with_email)}</b></span>
    <span>تماس‌شده <b>${fa(stats.contacted)}</b></span>
    <span>مشتری‌شده <b>${fa(stats.won)}</b></span>`;
}

/* ── لیدها ────────────────────────────────────────────────────────────────── */
function fillRunSelect() {
  const select = $("#leadRunSelect");
  if (!state.runs.length) return;
  select.innerHTML = state.runs
    .map((r) => `<option value="${esc(r.id)}">${esc(r.niche_fa || "—")} · ${esc(r.city || "")} · ${fa(r.total)} لید</option>`)
    .join("");
  if (state.currentRun && state.runs.some((r) => r.id === state.currentRun)) select.value = state.currentRun;
  else state.currentRun = select.value;
}

async function loadLeads() {
  if (!state.runs.length) { try { state.runs = (await api("/api/runs?limit=60")).runs; } catch { } }
  if (!state.runs.length) { $("#leadsTable tbody").innerHTML = `<tr><td colspan="8">${empty("اجرایی وجود ندارد")}</td></tr>`; return; }
  fillRunSelect();
  const runId = $("#leadRunSelect").value || state.currentRun;
  state.currentRun = runId;
  $("#leadsRunLabel").textContent = `— اجرا: ${runId}`;
  const params = new URLSearchParams({
    limit: state.pageSize, offset: state.page * state.pageSize, sort: state.filters.sort,
  });
  Object.entries(state.filters).forEach(([k, v]) => { if (v && k !== "sort") params.set(k === "q" ? "q" : k, v); });
  try {
    const data = await api(`/api/runs/${runId}/leads?${params}`);
    state.leads = data.leads; state.leadCount = data.total;
    renderLeads(data.leads);
    $("#bulkInfo").textContent = `${fa(data.leadCount)} لید در این اجرا — ${fa(data.leads.length)} ردیف نمایش داده شد`;
  } catch (err) { toast(err.message, "err"); }
}

function renderLeads(rows) {
  const body = $("#leadsTable tbody");
  if (!rows.length) { body.innerHTML = `<tr><td colspan="8">${empty("لیدی با این فیلترها پیدا نشد")}</td></tr>`; return; }
  body.innerHTML = rows.map((r) => `
    <tr data-id="${esc(r.id)}">
      <td><span class="score ${esc(r.segment || "cold")}">${fa(r.score || 0)}</span></td>
      <td>
        <b>${esc(r.title || "—")}</b>
        <div class="camp-meta">${esc(r.category || "")} ${r.address ? "· " + esc(r.address) : ""}</div>
        <div class="tag-list">${(r.tags || []).slice(0, 3).map((t) => `<span class="badge muted">${esc(t)}</span>`).join("")}</div>
      </td>
      <td>
        ${r.phone ? `<div class="mono" dir="ltr">${esc(r.phone)}</div>` : `<span class="badge muted">بدون تلفن</span>`}
        ${r.email_primary ? `<div class="mono" dir="ltr">${esc(r.email_primary)}</div>` : ""}
      </td>
      <td>
        ${r.website ? `<a href="${esc(r.website)}" target="_blank" rel="noopener">سایت ↗</a>` : `<span class="badge muted">بدون سایت</span>`}
        <div class="camp-meta">
          ${r.instagram ? `<a href="https://instagram.com/${esc(r.instagram)}" target="_blank" rel="noopener">IG</a> ` : ""}
          ${r.facebook ? `<a href="${esc(r.facebook)}" target="_blank" rel="noopener">FB</a> ` : ""}
          ${r.linkedin ? `<a href="${esc(r.linkedin)}" target="_blank" rel="noopener">IN</a> ` : ""}
          ${r.maps_link ? `<a href="${esc(r.maps_link)}" target="_blank" rel="noopener" title="لوکیشن دقیق روی نقشه">📍 نقشه</a>` : ""}
        </div>
      </td>
      <td>${(r.gaps_fa || []).slice(0, 3).map((g) => `<span class="badge warm">${esc(g)}</span>`).join(" ")}</td>
      <td><span class="badge cold">${esc(r.channel_fa || r.channel || "—")}</span>
        <div class="camp-meta">⭐ ${fa(r.rating || 0)} · ${fa(r.reviews || 0)} نظر</div></td>
      <td><select class="status-select" data-id="${esc(r.id)}">
        ${Object.entries(state.catalog.lead_statuses.reduce((a, s) => (a[s.id] = s.fa, a), {}))
        .map(([id, label]) => `<option value="${id}" ${r.status === id ? "selected" : ""}>${esc(label)}</option>`).join("")}
      </select></td>
      <td><button class="ghost" data-open="${esc(r.id)}">جزئیات</button></td>
    </tr>`).join("");

  body.onclick = (ev) => {
    if (ev.target.classList.contains("status-select")) return;
    const btn = ev.target.closest("[data-open]");
    const row = ev.target.closest("tr");
    if (btn) openDrawer(btn.dataset.open);
    else if (row) openDrawer(row.dataset.id);
  };
  $$(".status-select", body).forEach((sel) => sel.addEventListener("change", async () => {
    try {
      await api(`/api/leads/${sel.dataset.id}`, { method: "PATCH", body: { status: sel.value } });
      toast("وضعیت به‌روزرسانی شد");
      const health = await api("/api/health"); renderGlobalStats(health.db);
    } catch (err) { toast(err.message, "err"); }
  }));

  $("#pageInfo").textContent = `صفحه ${fa(state.page + 1)} از ${fa(Math.max(1, Math.ceil(state.leadCount / state.pageSize)))}`;
}

$("#leadRunSelect").addEventListener("change", () => { state.page = 0; loadLeads(); });
$("#leadSearch").addEventListener("input", debounce((ev) => { state.filters.q = ev.target.value.trim(); state.page = 0; loadLeads(); }, 400));
$("#segmentFilter").addEventListener("change", (e) => { state.filters.segment = e.target.value; state.page = 0; loadLeads(); });
$("#statusFilter").addEventListener("change", (e) => { state.filters.status = e.target.value; state.page = 0; loadLeads(); });
$("#channelFilter").addEventListener("change", (e) => { state.filters.channel = e.target.value; state.page = 0; loadLeads(); });
$("#gapFilter").addEventListener("change", (e) => { state.filters.gap = e.target.value; state.page = 0; loadLeads(); });
$("#sortSelect").addEventListener("change", (e) => { state.filters.sort = e.target.value; state.page = 0; loadLeads(); });
$("#resetFilters").addEventListener("click", () => {
  state.filters = { q: "", segment: "", status: "", channel: "", gap: "", sort: "score" };
  $("#leadSearch").value = ""; $("#segmentFilter").value = ""; $("#statusFilter").value = "";
  $("#channelFilter").value = ""; $("#gapFilter").value = ""; $("#sortSelect").value = "score";
  state.page = 0; loadLeads();
});
$("#prevPage").addEventListener("click", () => { if (state.page > 0) { state.page--; loadLeads(); } });
$("#nextPage").addEventListener("click", () => { if ((state.page + 1) * state.pageSize < state.leadCount) { state.page++; loadLeads(); } });
$$(".bulk [data-export]").forEach((btn) => btn.addEventListener("click", () => {
  const kind = btn.dataset.export;
  if (!state.currentRun) return toast("اول یک اجرا را انتخاب کنید", "err");
  const cols = $("#colsSelect") ? $("#colsSelect").value : "contact";
  if (kind === "campaign") return download(`/api/runs/${state.currentRun}/export?what=campaign&channel=${$("#campChannel").value || "email"}`, `campaign-${state.currentRun}.csv`);
  download(`/api/runs/${state.currentRun}/export?fmt=${kind}&cols=${cols}`, `leads-${state.currentRun}.${kind === "xlsx" ? "xlsx" : kind}`);
}));

/* ── کشوی جزئیات ─────────────────────────────────────────────────────────── */
async function openDrawer(leadId) {
  if (!leadId) return;
  $("#drawer").hidden = false; $("#scrim").hidden = false;
  $("#drawerBody").innerHTML = `<div class="empty">در حال بارگذاری…</div>`;
  try {
    const { lead } = await api(`/api/leads/${leadId}`);
    $("#drawerTitle").textContent = lead.title || "—";
    const factors = lead.score_factors || [];
    $("#drawerBody").innerHTML = `
      <div style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap">
        <span class="score ${esc(lead.segment)}">${fa(lead.score)}</span>
        <span class="badge ${esc(lead.segment)}">${esc(lead.segment === "hot" ? "داغ 🔥" : lead.segment === "warm" ? "گرم ☀️" : "سرد ❄️")}</span>
        <span class="badge cold">${esc(lead.channel_fa || "")}</span>
        <span class="badge muted">کامل بودن اطلاعات: ${fa(lead.completeness)}٪</span>
      </div>

      <div class="section-title">اطلاعات تماس</div>
      <dl class="kv">
        <dt>تلفن</dt><dd class="mono" dir="ltr">${esc(lead.phone || "—")}</dd>
        <dt>ایمیل</dt><dd class="mono" dir="ltr">${esc(lead.emails || "—")}</dd>
        <dt>وب‌سایت</dt><dd>${lead.website ? `<a href="${esc(lead.website)}" target="_blank" rel="noopener" dir="ltr">${esc(lead.domain || lead.website)}</a>` : "—"}</dd>
        <dt>اینستاگرام</dt><dd>${lead.instagram ? `<a href="https://instagram.com/${esc(lead.instagram)}" target="_blank" rel="noopener" dir="ltr">${esc(lead.instagram)}</a>` : "—"}</dd>
        <dt>فیسبوک</dt><dd>${lead.facebook ? `<a href="${esc(lead.facebook)}" target="_blank" rel="noopener" dir="ltr">پروفایل ↗</a>` : "—"}</dd>
        <dt>لینکدین</dt><dd>${lead.linkedin ? `<a href="${esc(lead.linkedin)}" target="_blank" rel="noopener" dir="ltr">پروفایل ↗</a>` : "—"}</dd>
        <dt>نشانی</dt><dd>${esc(lead.address || "—")}</dd>
        <dt>لوکیشن دقیق</dt><dd>${lead.location ? `<span class="mono" dir="ltr">${esc(lead.location)}</span>` : "—"}
          ${lead.maps_link ? ` · <a href="${esc(lead.maps_link)}" target="_blank" rel="noopener">نمایش روی نقشه ↗</a>` : ""}
          ${lead.plus_code ? `<div class="camp-meta">پلاس کد: <span class="mono" dir="ltr">${esc(lead.plus_code)}</span></div>` : ""}</dd>
        <dt>صنف منبع</dt><dd>${esc(lead.niche_fa || lead.category || "—")}</dd>
        <dt>صنف</dt><dd>${esc(lead.category || "—")}</dd>
        <dt>امتیاز گوگل</dt><dd>⭐ ${fa(lead.rating || 0)} از ۵ · ${fa(lead.reviews || 0)} نظر</dd>
      </dl>

      <div class="section-title">چرا این امتیاز؟ (${esc(lead.profile || "")})</div>
      ${factors.map((f) => `
        <div class="factor">
          <div><b>${esc(f.fa)}</b> <small>وزن ${fa(f.weight)}</small>
            <div class="bar-track"><div class="bar-fill" style="width:${Math.round((f.value || 0) * 100)}%"></div></div>
            <div class="camp-meta">${esc(f.detail || "")}</div>
          </div>
          <div class="mono">${fa(f.points)}</div>
        </div>`).join("")}

      <div class="section-title">فرصت‌ها (شکاف دیجیتال)</div>
      <div class="tag-list">${(lead.gaps || []).map((g) => `<span class="badge warm">${esc(window.GAP_FA ? (GAP_FA[g] || g) : g)}</span>`).join("") || "—"}</div>
      <div class="section-title">برچسب‌ها</div>
      <div class="tag-list">${(lead.tags || []).map((t) => `<span class="badge muted">${esc(t)}</span>`).join("") || "—"}</div>
      <div class="section-title">زاویه پیشنهاد فروش</div>
      <p>${esc(lead.pitch || "—")}</p>

      <div class="section-title">پیگیری (مینی‌CRM)</div>
      <div class="row">
        <select id="drawerStatus" class="half">
          ${state.catalog.lead_statuses.map((s) => `<option value="${s.id}" ${lead.status === s.id ? "selected" : ""}>${esc(s.fa)}</option>`).join("")}
        </select>
        <button class="ghost half" id="drawerSaveStatus">ذخیره وضعیت</button>
      </div>
      <div class="field"><label>یادداشت</label><textarea id="drawerNote" rows="2">${esc(lead.note || "")}</textarea></div>

      <div class="section-title">متن آماده ارسال</div>
      ${Object.entries(lead.campaign).map(([channel, msg]) => `
        <div class="camp-item">
          <header><h4>${esc(msg.channel_fa)}</h4>
            <span class="camp-meta">${esc(msg.to || "")}</span></header>
          ${msg.subject ? `<div class="camp-meta">موضوع: ${esc(msg.subject)}</div>` : ""}
          <div class="camp-body">${esc(msg.text || msg.body)}</div>
          <div class="camp-actions">
            <button class="ghost" data-copy="${esc(channel)}">کپی متن</button>
            ${msg.mailto || channel === "email" && lead.email_primary ? "" : ""}
            ${channel === "whatsapp" && lead.phone ? `<a class="ghost" target="_blank" rel="noopener" href="https://wa.me/${esc(String(lead.phone).replace(/\\D/g, ""))}?text=${encodeURIComponent(msg.body)}" style="text-decoration:none">باز کردن واتس‌اپ ↗</a>` : ""}
            ${channel === "email" && lead.email_primary ? `<a class="ghost" href="mailto:${esc(lead.email_primary)}?subject=${encodeURIComponent(msg.subject || "")}&body=${encodeURIComponent(msg.body)}" style="text-decoration:none">باز کردن ایمیل ↗</a>` : ""}
          </div>
        </div>`).join("")}
    `;
    $("#drawerSaveStatus").onclick = async () => {
      await api(`/api/leads/${leadId}`, {
        method: "PATCH",
        body: { status: $("#drawerStatus").value, note: $("#drawerNote").value },
      });
      toast("ذخیره شد"); loadLeads();
    };
    $$("[data-copy]", $("#drawerBody")).forEach((btn) => btn.addEventListener("click", () => {
      const text = btn.closest(".camp-item").querySelector(".camp-body").textContent;
      navigator.clipboard.writeText(text).then(() => toast("متن کپی شد"));
    }));
  } catch (err) {
    $("#drawerBody").innerHTML = `<div class="empty">خطا: ${esc(err.message)}</div>`;
  }
}
function closeDrawer() { $("#drawer").hidden = true; $("#scrim").hidden = true; }
$("#drawerClose").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

/* ── تحلیل ────────────────────────────────────────────────────────────────── */
async function loadAnalysis() {
  if (!state.runs.length) { try { state.runs = (await api("/api/runs?limit=60")).runs; } catch { } }
  const runId = state.currentRun || $("#leadRunSelect").value || (state.runs[0] || {}).id;
  if (!runId) { $("#insightsList").innerHTML = empty("اجرایی برای تحلیل نیست"); return; }
  state.currentRun = runId;
  try {
    const detail = await api(`/api/runs/${runId}`);
    renderAnalysis(detail.analytics, detail.run);
  } catch (err) { toast(err.message, "err"); }
}

function renderAnalysis(a, run) {
  if (!a) return;
  $("#statTotal").textContent = fa(a.coverage.total);
  $("#statRun").textContent = `${run.niche_fa || ""} · ${run.city || ""} · موتور ${run.engine}`;
  $("#statScore").textContent = fa(a.avg_score);
  $("#statProfile").textContent = (state.catalog.profiles.find((p) => p.id === run.profile) || {}).fa || run.profile || "—";
  $("#statReachable").textContent = `${fa(a.coverage.reachable)}`;
  $("#statReachNote").textContent = `${fa(a.coverage_percent.reachable || 0)}٪ از کل لیدها`;

  $("#insightsList").innerHTML = (a.insights || []).length
    ? a.insights.map((i) => `<li class="${esc(i.kind)}">${esc(i.fa)}</li>`).join("")
    : empty("بینشی تولید نشد");

  $("#funnelChart").innerHTML = barChart(a.funnel.map((s) => ({ label: s.fa, value: s.count })), "ok");
  $("#coverageChart").innerHTML = barChart([
    { label: "وب‌سایت", value: a.coverage.website },
    { label: "ایمیل", value: a.coverage.email },
    { label: "تلفن", value: a.coverage.phone },
    { label: "اینستاگرام", value: a.coverage.instagram },
    { label: "فیسبوک", value: a.coverage.facebook },
    { label: "لینکدین", value: a.coverage.linkedin },
    { label: "قابل تماس", value: a.coverage.reachable },
  ]);
  $("#gapsChart").innerHTML = barChart(a.gaps.slice(0, 9).map((g) => ({ label: g.fa, value: g.count })), "warm");
  $("#scoreChart").innerHTML = columnChart(a.score_distribution.map((d) => ({ label: d.range, value: d.count })));
  $("#ratingChart").innerHTML = columnChart(a.rating_distribution.map((d) => ({ label: d.label, value: d.count })));
  $("#channelChart").innerHTML = barChart(a.channels.map((c) => ({ label: c.label, value: c.count })), "cold");
  $("#keywordChart").innerHTML = barChart(a.keywords.slice(0, 8).map((k) => ({ label: k.label, value: k.count })));

  const seg = a.segments;
  $("#segmentChart").innerHTML = donut([
    { label: "داغ", value: seg.hot.count, color: "#fb7185" },
    { label: "گرم", value: seg.warm.count, color: "#fbbf24" },
    { label: "سرد", value: seg.cold.count, color: "#60a5fa" },
  ]);
  $("#segmentLegend").innerHTML = ["hot|داغ|#fb7185", "warm|گرم|#fbbf24", "cold|سرد|#60a5fa"]
    .map(([k, label, color]) => `<span><i style="background:${color}"></i>${label}: ${fa(seg[k].count)} (میانگین ${fa(seg[k].avg_score)})</span>`)
    .join("");
  $("#mapChart").innerHTML = scatterMap(a.map);
}

function barChart(items, tone = "") {
  if (!items || !items.length) return empty();
  const max = Math.max(1, ...items.map((i) => i.value));
  return `<div class="chart">${items.map((i) => `
    <div class="bar-row">
      <span title="${esc(i.label)}">${esc(String(i.label).slice(0, 26))}</span>
      <div class="bar-track"><div class="bar-fill ${tone}" style="width:${Math.round(100 * i.value / max)}%"></div></div>
      <span class="bar-val">${fa(i.value)}</span>
    </div>`).join("")}</div>`;
}

function columnChart(items) {
  if (!items || !items.length) return empty();
  const max = Math.max(1, ...items.map((i) => i.value));
  const width = 100 / items.length;
  return `<svg viewBox="0 0 600 170" preserveAspectRatio="none" role="img">
    ${items.map((item, i) => {
      const height = Math.max(2, (item.value / max) * 120);
      const x = 600 - (i + 1) * (600 / items.length) + 4;
      const w = (600 / items.length) - 8;
      return `<g>
        <rect x="${x}" y="${140 - height}" width="${w}" height="${height}" rx="4" fill="url(#grad)"/>
        <text x="${x + w / 2}" y="158" font-size="11" fill="#9fb0d0" text-anchor="middle">${esc(item.label)}</text>
        <text x="${x + w / 2}" y="${134 - height}" font-size="11" fill="#e8edf9" text-anchor="middle">${item.value ? fa(item.value) : ""}</text>
      </g>`;
    }).join("")}
    <defs><linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#38bdf8"/><stop offset="100%" stop-color="#6366f1"/>
    </linearGradient></defs>
  </svg>`;
}

function donut(items) {
  const total = items.reduce((s, i) => s + i.value, 0) || 1;
  let angle = -Math.PI / 2;
  const paths = items.map((item) => {
    const sweep = (item.value / total) * Math.PI * 2;
    const large = sweep > Math.PI ? 1 : 0;
    const x1 = 100 + 70 * Math.cos(angle), y1 = 100 + 70 * Math.sin(angle);
    angle += sweep;
    const x2 = 100 + 70 * Math.cos(angle), y2 = 100 + 70 * Math.sin(angle);
    return `<path d="M${x1},${y1} A70,70 0 ${large} 1 ${x2},${y2}" stroke="${item.color}" stroke-width="26" fill="none"/>`;
  }).join("");
  return `<svg viewBox="0 0 200 200" style="max-width:230px;margin:0 auto">
    <circle cx="100" cy="100" r="70" fill="none" stroke="#1b2745" stroke-width="26"/>
    ${paths}
    <text x="100" y="96" text-anchor="middle" font-size="26" fill="#e8edf9" font-weight="700">${fa(total)}</text>
    <text x="100" y="118" text-anchor="middle" font-size="12" fill="#9fb0d0">کل لید</text>
  </svg>`;
}

function scatterMap(points) {
  if (!points || !points.length) return empty("نقطه‌ای برای نمایش نیست");
  const lats = points.map((p) => p.lat), lons = points.map((p) => p.lon);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const spanLat = (maxLat - minLat) || 0.01, spanLon = (maxLon - minLon) || 0.01;
  const colors = { hot: "#fb7185", warm: "#fbbf24", cold: "#60a5fa" };
  const dots = points.map((p) => {
    // راست‌به‌چپ: محور x معکوس نمی‌شود چون نقشه جغرافیایی است
    const x = 20 + ((p.lon - minLon) / spanLon) * 560;
    const y = 300 - ((p.lat - minLat) / spanLat) * 270;
    const r = 2.2 + Math.min(6, Math.sqrt(Number(p.score) || 1) / 4);
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}" fill="${colors[p.segment] || "#60a5fa"}" opacity=".72">
      <title>${esc(p.title)} — امتیاز ${fa(p.score)} (${esc(p.category)})</title></circle>`;
  }).join("");
  return `<svg viewBox="0 0 600 320" role="img">
    <rect width="600" height="320" rx="12" fill="#0b1224" stroke="#23304f"/>
    ${Array.from({ length: 7 }, (_, i) => `<line x1="${i * 100}" y1="0" x2="${i * 100}" y2="320" stroke="#16213c"/>`).join("")}
    ${Array.from({ length: 4 }, (_, i) => `<line x1="0" y1="${i * 100}" x2="600" y2="${i * 100}" stroke="#16213c"/>`).join("")}
    ${dots}
  </svg>`;
}

/* ── کمپین ────────────────────────────────────────────────────────────────── */
function senderFromForm() {
  return {
    name: $("#sndName").value.trim(), company: $("#sndCompany").value.trim(),
    role: $("#sndRole").value.trim(), phone: $("#sndPhone").value.trim(),
    email: $("#sndEmail").value.trim(), website: $("#sndWebsite").value.trim(),
    offer: $("#sndOffer").value.trim(), case_study: $("#sndCase").value.trim(),
  };
}
function prepareCampaign() {
  if (!state.runs.length) api("/api/runs?limit=60").then((d) => { state.runs = d.runs; });
}
$("#campGenerate").addEventListener("click", async () => {
  const runId = state.currentRun || $("#leadRunSelect").value || (state.runs[0] || {}).id;
  if (!runId) return toast("اول یک اجرا انتخاب کنید", "err");
  const params = new URLSearchParams({
    channel: $("#campChannel").value, limit: $("#campLimit").value,
    sender: JSON.stringify(senderFromForm()),
  });
  if ($("#campSegment").value) params.set("segment", $("#campSegment").value);
  $("#campList").innerHTML = `<div class="empty">در حال تولید…</div>`;
  try {
    const data = await api(`/api/runs/${runId}/campaign?${params}`);
    $("#campCompliance").textContent = data.compliance;
    if (!data.items.length) { $("#campList").innerHTML = empty("لیدی با این فیلترها نیست"); return; }
    $("#campList").innerHTML = data.items.map((item) => `
      <div class="camp-item">
        <header>
          <h4>${esc(item.title)}</h4>
          <span class="camp-meta">${esc(item.segment_fa || "")} · امتیاز ${fa(item.score)} · ${esc(item.status_fa || "")}</span>
        </header>
        <div class="camp-meta">گیرنده: ${esc(item.to || "—")} ${item.subject ? "· موضوع: " + esc(item.subject) : ""}</div>
        <div class="camp-body">${esc(item.text)}</div>
        <div class="camp-actions">
          <button class="ghost" data-copy>کپی متن</button>
          ${item.mailto ? `<a class="ghost" href="${esc(item.mailto)}" style="text-decoration:none">ایمیل ↗</a>` : ""}
          ${item.wa ? `<a class="ghost" href="${esc(item.wa)}" target="_blank" rel="noopener" style="text-decoration:none">واتس‌اپ ↗</a>` : ""}
          <select data-status="${esc(item.id)}">
            ${state.catalog.lead_statuses.map((s) => `<option value="${s.id}" ${item.status === s.id ? "selected" : ""}>${esc(s.fa)}</option>`).join("")}
          </select>
        </div>
      </div>`).join("");
    $$("[data-copy]", $("#campList")).forEach((btn) => btn.addEventListener("click", () => {
      navigator.clipboard.writeText(btn.closest(".camp-item").querySelector(".camp-body").textContent)
        .then(() => toast("کپی شد"));
    }));
    $$("[data-status]", $("#campList")).forEach((sel) => sel.addEventListener("change", async () => {
      await api(`/api/leads/${sel.dataset.status}`, { method: "PATCH", body: { status: sel.value } });
      toast("وضعیت ذخیره شد");
    }));
  } catch (err) { $("#campList").innerHTML = empty(err.message); }
});
$("#campExport").addEventListener("click", () => {
  const runId = state.currentRun || $("#leadRunSelect").value || (state.runs[0] || {}).id;
  if (!runId) return toast("اول یک اجرا انتخاب کنید", "err");
  const params = new URLSearchParams({ what: "campaign", channel: $("#campChannel").value, sender: JSON.stringify(senderFromForm()) });
  if ($("#campSegment").value) params.set("segment", $("#campSegment").value);
  download(`/api/runs/${runId}/export?${params}`, `campaign-${runId}.csv`);
});

/* ── ورود داده ────────────────────────────────────────────────────────────── */
const dz = $("#dropzone");
$("#browseBtn").addEventListener("click", () => $("#importFile").click());
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", async (e) => { if (e.dataTransfer.files[0]) await importFile(e.dataTransfer.files[0]); });
$("#importFile").addEventListener("change", async (e) => { if (e.target.files[0]) await importFile(e.target.files[0]); });
$("#importBtn").addEventListener("click", async () => {
  const text = $("#impText").value.trim();
  if (!text) return toast("فایل یا متن لازم است", "err");
  await postImport({ text, niche_query: $("#impNiche").value, city: $("#impCity").value });
});

async function importFile(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("niche_query", $("#impNiche").value);
  form.append("city", $("#impCity").value);
  $("#importResult").innerHTML = `<div class="empty">در حال پردازش ${esc(file.name)}…</div>`;
  try {
    const result = await api("/api/import", { method: "POST", body: form });
    renderImportResult(result);
  } catch (err) { $("#importResult").innerHTML = empty(err.message); }
}
async function postImport(body) {
  $("#importResult").innerHTML = `<div class="empty">در حال پردازش…</div>`;
  try {
    const result = await api("/api/import", { method: "POST", body });
    renderImportResult(result);
  } catch (err) { $("#importResult").innerHTML = empty(err.message); }
}
function renderImportResult(result) {
  state.currentRun = result.run_id;
  const a = result.analytics || {};
  $("#importResult").innerHTML = `
    <div class="warnbox" style="background:rgba(52,211,153,.1);border-color:rgba(52,211,153,.4);color:#c8f7e4">
      ✓ ${fa(result.total)} لید پردازش شد — ${fa(result.hot)} داغ. شناسه اجرا: <b>${esc(result.run_id)}</b>
    </div>
    <ul class="insights">${(a.insights || []).map((i) => `<li class="${esc(i.kind)}">${esc(i.fa)}</li>`).join("")}</ul>
    <div class="actions">
      <button class="primary" id="impOpen">مشاهده لیدها →</button>
      <button class="ghost" id="impXlsx">⬇ خروجی اکسل</button>
    </div>`;
  $("#impOpen").onclick = () => { loadRuns(); switchView("leads"); };
  $("#impXlsx").onclick = () => download(`/api/runs/${result.run_id}/export?fmt=xlsx`, `scored-${result.run_id}.xlsx`);
  loadRuns();
}

/* ── راهنما ───────────────────────────────────────────────────────────────── */
async function loadDoctor() {
  try {
    const health = await api("/api/health");
    $("#doctorBox").textContent = JSON.stringify({
      engine: health.engine, config: health.config, db: health.db, version: health.version,
    }, null, 2);
  } catch (err) { $("#doctorBox").textContent = `خطا: ${err.message}`; }
}

/* برچسب فارسی شکاف‌ها برای کشو */
api("/api/catalog").then((c) => { window.GAP_FA = c.gap_labels; }).catch(() => { });
