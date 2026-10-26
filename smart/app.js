/* LeadGen Studio — رابط ساده (Smart Mode)
 *
 * یک جمله → اجرای کامل. این فایل دو حالت دارد:
 *   • live : همه‌چیز از API سرور پایتون (/api/…)
 *   • demo : بدون سرور، از demo-data.json (همان دادهٔ سایت GitHub Pages) + پیشرفت شبیه‌سازی‌شده
 * هر دو حالت یک رابط کاربری دارند؛ کاربر فقط می‌بیند «کار می‌کند».
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const FA = "۰۱۲۳۴۵۶۷۸۹";
  const fa = (v) => String(v ?? "").replace(/\d/g, (d) => FA[+d]);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // گزینه‌های پایه برای وقتی سرور در دسترس نیست (آینهٔ leadgen/smart.py)
  const FALLBACK_OPTIONS = {
    goals: [
      { id: "site", icon: "🖥️", fa: "سایت یا فروشگاه می‌فروشم", profile: "web_design",
        hint: "کسب‌وکارهای بدون سایت داغ‌ترین لید شما هستند." },
      { id: "ads", icon: "📣", fa: "تبلیغات، سئو یا اینستاگرام", profile: "marketing",
        hint: "تمرکز روی دیده‌شدن و نظرات کم." },
      { id: "supply", icon: "📦", fa: "کالا یا تجهیزات به کسب‌وکارها", profile: "b2b_supply",
        hint: "اولویت با دسترسی آسان و اعتبار بالا." },
      { id: "hire", icon: "🧑‍💼", fa: "نیرو یا همکار جذب می‌کنم", profile: "recruitment",
        hint: "کسب‌وکارهای پرحجم و فعال." },
      { id: "list", icon: "📋", fa: "فقط یک فهرست تمیز می‌خواهم", profile: "generic",
        hint: "امتیازدهی متوازن." },
    ],
    sizes: [
      { id: "quick", icon: "⚡", fa: "سریع", hint: "چند دقیقه، نمونهٔ کوچک بازار", depth: 2, grid: 0, limit: 60, minutes: [1, 3] },
      { id: "standard", icon: "🎯", fa: "استاندارد", hint: "پوشش خوب یک صنف در شهر", depth: 4, grid: 0, limit: 200, minutes: [3, 8] },
      { id: "deep", icon: "🛰️", fa: "کامل (کل شهر)", hint: "کاشی‌بندی شهر + حجم بالا", depth: 5, grid: 3, limit: 0, minutes: [10, 35] },
    ],
    examples: ["دندانپزشکی در تهران", "همه مشاغل در مشهد", "کافه و رستوران در اصفهان", "plumbers in Denver CO"],
    presets: [{ id: "contact", fa: "کارت تماس کامل + لوکیشن", columns: 28 },
              { id: "lead", fa: "لید (پیش‌فرض)", columns: 21 },
              { id: "location", fa: "فقط مکان و تماس", columns: 15 },
              { id: "all", fa: "همه ستون‌ها", columns: 33 }],
    formats: [{ id: "xlsx", fa: "اکسل (پیشنهادی)" }, { id: "csv", fa: "CSV" }, { id: "json", fa: "JSON" }],
  };

  const STAGES = [
    [1, "در صف اجرا…"], [8, "ساخت کلیدواژه‌ها با مکان…"], [22, "اجرای job روی نقشه…"],
    [46, "دانلود دادهٔ خام (۳۴ ستون)…"], [60, "یکتاسازی و پاک‌سازی…"],
    [72, "غنی‌سازی: ایمیل و شبکه‌های اجتماعی…"], [85, "امتیازدهی و بخش‌بندی لیدها…"],
    [94, "ساخت فایل خروجی…"],
  ];

  const state = {
    mode: "live", options: FALLBACK_OPTIONS, parsed: null,
    goal: "site", size: "standard", cols: "contact", fmt: "xlsx",
    runId: null, run: null, analytics: null, leads: [], view: [],
    page: 1, perPage: 24, segment: "", query: "", sort: "score", viewMode: "cards",
    active: null, channel: "email", demoData: null, poller: null, busy: false,
    sender: { name: "", company: "", role: "مشاور رشد کسب‌وکارهای محلی", phone: "",
              email: "", website: "", offer: "۳۰ دقیقه مشاوره رایگان" },
  };

  // ── ابزارهای نمایشی ──────────────────────────────────────────────────
  function toast(text, kind = "ok") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = text;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  async function copyText(text, label = "کپی شد") {
    try { await navigator.clipboard.writeText(text); toast(label); }
    catch {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); toast(label); } catch { toast("کپی ناموفق بود", "err"); }
      ta.remove();
    }
  }

  function downloadBlob(name, content, mime) {
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  const SEG_FA = { hot: "داغ 🔥", warm: "گرم 🌤", cold: "سرد ❄️" };
  const STATUS_FA = { new: "جدید", contacted: "تماس گرفته", replied: "پاسخ داده",
                      qualified: "واجد شرایط", won: "مشتری شد", lost: "از دست رفت",
                      unsubscribed: "لغو دریافت" };

  // ── لایهٔ HTTP ───────────────────────────────────────────────────────
  async function jget(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
  async function jpost(url, body, method = "POST") {
    const res = await fetch(url, { method, headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  const Live = {
    kind: "live",
    options: () => jget("/api/smart/options"),
    health: () => jget("/api/health"),
    parse: (payload) => jpost("/api/smart/parse", payload),
    suggest: (q) => jget(`/api/smart/suggest?q=${encodeURIComponent(q)}`),
    start: (request) => jpost("/api/runs", request),
    progress: async (id) => (await jget(`/api/runs/${id}/progress`)).progress || {},
    detail: (id) => jget(`/api/runs/${id}`),
    leads: async (id) => (await jget(`/api/runs/${id}/leads?limit=2000`)).leads || [],
    lead: async (id) => (await jget(`/api/leads/${id}`)).lead,
    setStatus: (id, status, note) => jpost(`/api/leads/${id}`, { status, note }, "PATCH"),
    runs: async () => (await jget("/api/runs")).runs || [],
    campaign: async (id, channel, sender) => {
      const data = await jpost(`/api/leads/${id}/campaign`, { channel, sender, language: "fa" });
      return data;
    },
  };

  // ── حالت دمو (بدون سرور) ─────────────────────────────────────────────
  const normalizeDemo = (l, i) => ({
    ...l, id: l.id || `demo-${i}`, email_primary: l.email_primary || l.email || "",
    score_factors: l.score_factors || l.factors || [], gaps: l.gaps || [],
    status: l.status || "new", status_fa: STATUS_FA[l.status || "new"],
    note: l.note || "",
  });

  const Demo = {
    kind: "demo",
    async load() {
      if (state.demoData) return state.demoData;
      // سرور پایتون «demo-data.json» را می‌دهد؛ در نسخهٔ ایستا (GitHub Pages) همان
      // داده زیر «data/sample.json» است → چند مسیر را به ترتیب امتحان می‌کنیم.
      const candidates = ["demo-data.json", "data/sample.json", "../data/sample.json",
                          "../demo-data.json"];
      let data = null, lastErr = "";
      for (const url of candidates) {
        try {
          const res = await fetch(url, { cache: "no-cache" });
          if (res.ok) { data = await res.json(); break; }
          lastErr = `HTTP ${res.status} (${url})`;
        } catch (err) { lastErr = `${err.message} (${url})`; }
      }
      if (!data) throw new Error(lastErr || "دادهٔ دمو پیدا نشد");
      state.demoData = data;
      state.demoData.leads = state.demoData.leads.map(normalizeDemo);
      return state.demoData;
    },
    async options() { return FALLBACK_OPTIONS; },
    async health() {
      return { ok: true, engine: { active: "demo", up: true, note: "حالت دمو — دادهٔ نمونهٔ مصنوعی" },
               db: { runs: 1, leads: (state.demoData || { leads: [] }).leads.length } };
    },
    async parse(payload) {
      const d = await this.load();
      const text = String(payload.text || "").trim();
      const allTrades = /(همه|تمام|تمامی)\s*(مشاغل|کسب)/.test(text) || /all (businesses|trades)/i.test(text);
      const niches = d.meta.niches || [];
      const nicheFa = allTrades ? `همهٔ مشاغل (${fa(niches.length)} صنف نمونه)` : (niches[0] || "نمونه");
      const city = (payload.city || d.meta.city || "تهران");
      const size = FALLBACK_OPTIONS.sizes.find((s) => s.id === payload.size) || FALLBACK_OPTIONS.sizes[1];
      const keywords = niches.slice(0, 5).map((n) => `${n} در ${city}`);
      return {
        query: text, mode: text ? (allTrades ? "sweep" : "single") : "empty", ready: Boolean(text),
        missing: text ? [] : ["niche"],
        detected: { niche_fa: text ? nicheFa : "", city, all_trades: allTrades, groups: [],
                    lang: "fa", keyword_count: keywords.length,
                    goal: FALLBACK_OPTIONS.goals.find((g) => g.id === payload.goal) || FALLBACK_OPTIONS.goals[0],
                    size },
        keywords, eta_minutes: [1, 2],
        summary: text
          ? `${size.limit ? `${fa(size.limit)} لید` : "هرچه پیدا شود"} «${nicheFa}» در ${city} — حالت دمو (دادهٔ نمونهٔ مصنوعی، فوری)`
          : "چه شغلی را در کدام شهر می‌خواهید؟ (در حالت دمو هم چیزی بنویسید تا نمونه را ببینید)",
        warnings: ["حالت دمو: داده‌ها مصنوعی‌اند و موتور واقعی اجرا نمی‌شود — برای دادهٔ واقعی، داکر را بالا بیاورید."],
        request: { all_niches: allTrades, city_query: city, limit: size.limit },
      };
    },
    async suggest(q) {
      const d = await this.load();
      const needle = String(q || "").trim().toLowerCase();
      const niches = (d.meta.niches || []).filter((n) => !needle || n.toLowerCase().includes(needle))
        .slice(0, 6).map((n) => ({ id: n, fa: n, group: "", hint: "نمونهٔ دمو" }));
      const cities = [{ id: "tehran", fa: "تهران", label: "Tehran", country: "IR" },
                      { id: "mashhad", fa: "مشهد", label: "Mashhad", country: "IR" },
                      { id: "dubai", fa: "دبی", label: "Dubai", country: "AE" }]
        .filter((c) => !needle || c.fa.includes(needle) || c.label.toLowerCase().includes(needle));
      return { query: q, niches, cities, groups: [], all_trades: /(همه|تمام)\s*(مشاغل|کسب)/.test(q || "") };
    },
    async start(_request, onProgress) {
      await this.load();
      for (const [percent, message] of STAGES) {
        onProgress({ percent, message, stage: "working", status: "working" });
        await sleep(260);
      }
      const hot = state.demoData.analytics.segments.hot.count;
      onProgress({ percent: 100, stage: "done", status: "done",
                   message: `تکمیل شد — ${fa(state.demoData.leads.length)} لید (${fa(hot)} داغ)` });
      return { run_id: "demo" };
    },
    async progress() { return {}; },
    async detail() {
      const d = await this.load();
      return { run: { id: "demo", niche_fa: "نمونهٔ چندصنفی", city: d.meta.city, engine: "demo",
                      status: "done", total: state.demoData.leads.length,
                      hot: d.analytics.segments.hot.count, warm: d.analytics.segments.warm.count,
                      cold: d.analytics.segments.cold.count, avg_score: d.analytics.avg_score,
                      keywords: (d.meta.niches || []).map((n) => `${n} در ${d.meta.city}`) },
               analytics: d.analytics, lead_count: d.leads.length, progress: {} };
    },
    async leads() { await this.load(); return state.demoData.leads; },
    async lead(id) {
      await this.load();
      const lead = state.demoData.leads.find((l) => l.id === id) || state.demoData.leads[0];
      const camp = {};
      syncSender();
      for (const channel of Object.keys(window.LeadCampaign.CHANNEL_FA)) {
        const msg = window.LeadCampaign.messageFor(lead, channel);
        camp[channel] = { channel, channel_fa: window.LeadCampaign.CHANNEL_FA[channel],
                          to: msg.to, subject: msg.subject, text: msg.text, body: msg.text };
      }
      return { ...lead, campaign: camp };
    },
    async setStatus(id, status) {
      await this.load();
      const lead = state.demoData.leads.find((l) => l.id === id);
      if (lead) { lead.status = status; lead.status_fa = STATUS_FA[status] || status; }
      return { ok: true, lead };
    },
    async runs() {
      await this.load();
      return [{ id: "demo", niche_fa: "نمونهٔ دمو (چندصنفی)", city: state.demoData.meta.city,
                engine: "demo", total: state.demoData.leads.length,
                hot: state.demoData.analytics.segments.hot.count, status: "done",
                created_at: Date.now() / 1000 }];
    },
  };

  let backend = Live;

  // ── بارگذاری اولیه ───────────────────────────────────────────────────
  async function boot() {
    applyTheme(localStorage.getItem("leadgen-theme") || "dark");
    bindEvents();
    renderOptions();

    const wantsDemo = document.body.dataset.mode === "demo" || /[?&]demo=1/.test(location.search);
    if (!wantsDemo) {
      try {
        const health = await Live.health();
        state.serverOk = true;
        renderEngine(health.engine || {});
        state.options = { ...FALLBACK_OPTIONS, ...(await Live.options()) };
        renderOptions();
        adaptStaticLinks();
        await loadHistory();
      } catch (err) {
        toast(`سرور در دسترس نیست → حالت دمو فعال شد (${err.message})`, "warn");
        $("demoMode").checked = true;
        await switchToDemo();
        return;
      }
    } else {
      $("demoMode").checked = true;
      await switchToDemo();
    }
    $("q").focus();
    onQueryChange();
  }

  async function switchToDemo() {
    backend = Demo;
    state.mode = "demo";
    try {
      await Demo.load();
      renderEngine({ active: "demo", up: true, note: "حالت دمو — دادهٔ نمونهٔ مصنوعی" });
      state.options = FALLBACK_OPTIONS;
      renderOptions();
      adaptStaticLinks();
      await loadHistory();
    } catch (err) {
      toast(`دادهٔ دمو بارگذاری نشد: ${err.message}`, "err");
    }
  }

  function adaptStaticLinks() {
    // در نسخهٔ ایستا (بدون سرور)، «داشبورد حرفه‌ای» وجود ندارد → به ویترین امکانات می‌بریم
    const link = $("studioLink");
    if (!link) return;
    if (state.mode === "demo" && !state.serverOk) {
      link.href = "../";
      link.textContent = "ویترین امکانات";
      link.title = "صفحهٔ معرفی و نمونهٔ خروجی‌ها";
    }
  }

  function renderEngine(engine) {
    const pill = $("enginePill"), dot = pill.querySelector(".dot");
    dot.className = "dot " + (engine.active === "docker" ? "up" : engine.active === "demo" ? "demo" : "down");
    $("engineText").textContent = engine.active === "docker"
      ? "موتور واقعی (داکر)"
      : engine.active === "demo" ? "موتور دمو (داده نمونه)" : "موتور در دسترس نیست";
    pill.title = engine.note || engine.reason || "";
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("themeBtn").textContent = theme === "dark" ? "🌙" : "☀️";
  }

  // ── گزینه‌ها (هدف / اندازه / ستون‌ها) ────────────────────────────────
  function renderOptions() {
    const o = state.options;
    $("goals").innerHTML = o.goals.map((g) => `
      <button class="chip ${g.id === state.goal ? "active" : ""}" data-goal="${g.id}" title="${esc(g.hint || "")}">
        <span>${g.icon || "•"}</span><span>${esc(g.fa)}</span>
      </button>`).join("");
    $("sizes").innerHTML = o.sizes.map((s) => `
      <button class="chip ${s.id === state.size ? "active" : ""}" data-size="${s.id}" title="${esc(s.hint || "")}">
        <span>${s.icon || "•"}</span><span>${esc(s.fa)}<small>${esc(s.hint || "")}</small></span>
      </button>`).join("");
    $("cols").innerHTML = o.presets.map((p) =>
      `<option value="${p.id}" ${p.id === state.cols ? "selected" : ""}>${esc(p.fa)} (${fa(p.columns)} ستون)</option>`).join("");
    $("fmt").innerHTML = o.formats.map((f) =>
      `<option value="${f.id}" ${f.id === state.fmt ? "selected" : ""}>${esc(f.fa)}</option>`).join("");
    $("examples").innerHTML = (o.examples || []).map((e) =>
      `<button data-example="${esc(e)}">${esc(e)}</button>`).join("");

    $("goals").querySelectorAll("[data-goal]").forEach((b) => b.onclick = () => {
      state.goal = b.dataset.goal; renderOptions(); onQueryChange();
      const g = o.goals.find((x) => x.id === state.goal);
      if (g && g.hint) toast(g.hint);
    });
    $("sizes").querySelectorAll("[data-size]").forEach((b) => b.onclick = () => {
      state.size = b.dataset.size; renderOptions(); syncAdvanced(); onQueryChange();
    });
    $("examples").querySelectorAll("[data-example]").forEach((b) => b.onclick = () => {
      $("q").value = b.dataset.example; onQueryChange(); $("q").focus();
    });
  }

  function syncAdvanced() {
    const size = (state.options.sizes || []).find((s) => s.id === state.size);
    if (!size) return;
    $("depth").value = size.depth; $("grid").value = size.grid; $("limit").value = size.limit;
  }

  // ── «مغز»: تجزیهٔ جملهٔ کاربر ───────────────────────────────────────
  function payloadForParse() {
    return {
      text: $("q").value, goal: state.goal, size: state.size,
      socials: $("socials").checked, emails: $("emails").checked,
      engine_mode: $("demoMode").checked ? "demo" : $("engineMode").value,
      cols: $("cols").value, export_fmt: $("fmt").value,
    };
  }

  function requestForRun() {
    const base = { ...(state.parsed ? state.parsed.request : {}) };
    return {
      ...base,
      profile: (state.options.goals.find((g) => g.id === state.goal) || {}).profile || base.profile || "generic",
      depth: +$("depth").value || base.depth || 4,
      grid: +$("grid").value || 0,
      limit: Math.max(0, +$("limit").value || 0),
      cols: $("cols").value, export_fmt: $("fmt").value,
      socials: $("socials").checked, emails: $("emails").checked,
      engine_mode: $("demoMode").checked ? "demo" : $("engineMode").value,
      sender: { ...state.sender },
    };
  }

  function renderBrain(parsed) {
    const box = $("brain");
    if (!parsed) { box.hidden = true; return; }
    const d = parsed.detected || {};
    box.hidden = false;
    const chips = [
      d.niche_fa ? ["صنف", d.niche_fa] : null,
      d.city ? ["شهر", d.city] : null,
      d.all_trades ? ["حالت", "جاروی تمامی مشاغل"] : null,
      (d.groups || []).length ? ["گروه‌ها", d.groups.join("، ")] : null,
      d.keyword_count ? ["کلیدواژه", `${fa(d.keyword_count)} عبارت`] : null,
      (parsed.eta_minutes || []).length === 2
        ? ["زمان تقریبی", `${fa(parsed.eta_minutes[0])}–${fa(parsed.eta_minutes[1])} دقیقه`] : null,
      d.size ? ["حجم", d.size.fa] : null,
      d.goal ? ["هدف", d.goal.fa] : null,
    ].filter(Boolean);

    box.innerHTML = `
      <p class="summary">${esc(parsed.summary || "")}</p>
      <div class="detected">${chips.map(([k, v]) =>
        `<span class="chip-mini">${esc(k)}: <b>${esc(v)}</b></span>`).join("")}</div>
      ${(parsed.keywords || []).length ? `<div class="kw-row">${parsed.keywords.slice(0, 8)
        .map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : ""}
      ${(parsed.warnings || []).length ? `<div class="warns">${parsed.warnings
        .map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}</div>` : ""}`;
    $("go").disabled = !parsed.ready;
    $("go").title = parsed.ready ? "اجرا کن" : "اول بنویس چه شغلی و در کدام شهر";
  }

  async function onQueryChange() {
    const text = $("q").value;
    try {
      state.parsed = await backend.parse(payloadForParse());
      renderBrain(state.parsed);
    } catch (err) {
      renderBrain({ summary: `تجزیهٔ جمله ناموفق بود: ${err.message}`, ready: false, warnings: [] });
    }
    if (text.trim().length >= 2) showSuggest(text); else hideSuggest();
  }

  async function showSuggest(q) {
    try {
      const data = await backend.suggest(q);
      const items = [];
      if (data.all_trades) items.push(`<button class="all" data-fill="همه مشاغل">🧭 جاروی تمامی مشاغل <small>همهٔ اصناف یک شهر</small></button>`);
      if (data.niches && data.niches.length) {
        items.push('<div class="grp">اصناف</div>');
        data.niches.forEach((n) => items.push(
          `<button data-fill="${esc(n.fa)}">${esc(n.fa)} <small>${esc(n.group || n.en || "")}</small></button>`));
      }
      if (data.cities && data.cities.length) {
        items.push('<div class="grp">شهرها</div>');
        data.cities.forEach((c) => items.push(
          `<button data-city="${esc(c.fa)}">${esc(c.fa)} <small>${esc(c.label || "")}</small></button>`));
      }
      if (data.groups && data.groups.length) {
        items.push('<div class="grp">گروه‌های شغلی</div>');
        data.groups.forEach((g) => items.push(
          `<button data-fill="${esc(g)}">${esc(g)} <small>جاروی گروه</small></button>`));
      }
      const box = $("suggest");
      if (items.length <= (data.all_trades ? 1 : 0)) { hideSuggest(); return; }
      box.innerHTML = items.join("");
      box.hidden = false;
      box.querySelectorAll("button").forEach((b) => b.onclick = () => {
        if (b.dataset.city) {
          $("q").value = `${$("q").value.trim()} در ${b.dataset.city}`.trim();
        } else {
          $("q").value = b.dataset.fill;
        }
        hideSuggest(); onQueryChange(); $("q").focus();
      });
    } catch { hideSuggest(); }
  }
  function hideSuggest() { $("suggest").hidden = true; }

  // ── اجرا ────────────────────────────────────────────────────────────
  async function startRun() {
    if (state.busy) return;
    if (!state.parsed || !state.parsed.ready) {
      toast("اول بنویسید چه شغلی را در کدام شهر می‌خواهید", "warn"); $("q").focus(); return;
    }
    if ($("demoMode").checked && state.mode !== "demo") { await switchToDemo(); }
    state.busy = true;
    $("go").disabled = true;
    $("go").lastElementChild.textContent = "در حال اجرا…";
    $("results").hidden = true;
    $("progressPanel").hidden = false;
    $("cancelBtn").hidden = false;
    $("bar").className = "fill"; $("bar").style.width = "2%";
    $("log").innerHTML = ""; $("stageText").textContent = "آماده‌سازی…"; $("percentText").textContent = "۰٪";
    $("progressPanel").scrollIntoView({ behavior: "smooth", block: "center" });

    const request = requestForRun();
    const seen = new Set();
    const onProgress = (evt) => {
      if (!evt) return;
      const percent = Math.max(0, Math.min(100, +evt.percent || 0));
      $("bar").style.width = `${Math.max(2, percent)}%`;
      $("percentText").textContent = fa(percent) + "٪";
      if (evt.message) $("stageText").textContent = evt.message;
      const key = `${evt.stage}|${evt.message}`;
      if (evt.message && !seen.has(key)) {
        seen.add(key);
        const line = document.createElement("div");
        line.textContent = `[${fa(percent)}٪] ${evt.message}`;
        $("log").appendChild(line);
        $("log").scrollTop = $("log").scrollHeight;
      }
    };

    try {
      let runId;
      if (state.mode === "demo") {
        ({ run_id: runId } = await Demo.start(request, onProgress));
      } else {
        ({ run_id: runId } = await Live.start(request));
        runId = await pollProgress(runId, onProgress);
      }
      state.runId = runId;
      await loadRun(runId);
      $("bar").classList.add("done");
      toast("اجرا تمام شد — نتایج پایین صفحه است");
      $("results").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      $("bar").classList.add("failed");
      $("stageText").textContent = `خطا: ${err.message}`;
      toast(`اجرا ناموفق: ${err.message}`, "err");
    } finally {
      state.busy = false;
      $("go").disabled = false;
      $("go").lastElementChild.textContent = "شروع کن";
      if (state.poller) { clearInterval(state.poller); state.poller = null; }
      $("cancelBtn").hidden = true;
      loadHistory();
    }
  }

  function pollProgress(runId, onProgress) {
    return new Promise((resolve, reject) => {
      let misses = 0;
      const tick = async () => {
        try {
          const evt = await Live.progress(runId);
          onProgress(evt);
          const status = evt.status || evt.stage;
          if (status === "done" || evt.percent >= 100) { clearInterval(state.poller); state.poller = null; resolve(runId); }
          else if (status === "failed") { clearInterval(state.poller); state.poller = null; reject(new Error(evt.error || evt.message || "اجرا ناموفق بود")); }
          misses = 0;
        } catch (err) {
          misses++;
          if (misses > 12) { clearInterval(state.poller); state.poller = null; reject(err); }
        }
      };
      tick();
      state.poller = setInterval(tick, 1400);
    });
  }

  async function loadRun(runId) {
    const detail = await backend.detail(runId);
    state.run = detail.run || {};
    state.analytics = detail.analytics || {};
    state.leads = (await backend.leads(runId)) || [];
    state.page = 1;
    renderResults();
    $("progressPanel").hidden = true;
    $("results").hidden = false;
  }

  // ── رندر نتایج ──────────────────────────────────────────────────────
  function renderResults() {
    const a = state.analytics || {}, cov = a.coverage || {}, seg = a.segments || {};
    const total = cov.total || state.leads.length;
    $("kpis").innerHTML = [
      ["لید پیدا شد", total, ""],
      ["لید داغ 🔥", (seg.hot || {}).count || 0, `میانگین امتیاز ${fa(Math.round((seg.hot || {}).avg_score || 0))}`, "hot"],
      ["قابل تماس", cov.reachable || 0, `${fa(pct(cov.reachable, total))}٪`, "ok"],
      ["ایمیل‌دار", cov.email || 0, `${fa(pct(cov.email, total))}٪`],
      ["شبکه اجتماعی", cov.any_social || 0, `${fa(pct(cov.any_social, total))}٪`],
      ["لوکیشن دقیق", state.leads.filter((l) => l.lat || l.lon).length, "lat/lon + نقشه", "warm"],
    ].map(([label, value, sub, kind]) => `
      <div class="kpi ${kind || ""}"><b>${fa(value)}</b><span>${esc(label)}${sub ? ` · ${esc(sub)}` : ""}</span></div>`).join("");

    $("runMeta").textContent = state.run
      ? `${state.run.niche_fa || ""} · ${state.run.city || ""} · موتور ${state.run.engine || ""}`
      : "";
    $("insights").innerHTML = (a.insights || []).slice(0, 6).map((i) =>
      `<div class="insight ${esc(i.kind || "")}"><span>◈</span><span>${esc(i.fa)}</span></div>`).join("")
      || '<p class="empty">بینشی تولید نشد.</p>';

    bars($("covChart"), [
      ["وب‌سایت", cov.website, total, ""], ["ایمیل", cov.email, total, "ok"],
      ["تلفن", cov.phone, total, ""], ["اینستاگرام", cov.instagram, total, "warm"],
      ["فیسبوک", cov.facebook, total, "warm"], ["لینکدین", cov.linkedin, total, "warm"],
      ["لوکیشن", state.leads.filter((l) => l.lat || l.lon).length, total, "ok"],
    ]);
    bars($("segChart"), Object.entries(seg).map(([k, v]) => [SEG_FA[k] || k, v.count, total, k]));
    bars($("gapChart"), (a.gaps || []).slice(0, 6).map((g) => [g.fa, g.count, total, "hot"]), true);

    renderSegChips();
    applyFilters();
    $("exportNote").textContent = state.mode === "demo"
      ? "در حالت دمو، فایل در همان مرورگر ساخته می‌شود."
      : "فایل‌ها سمت سرور ساخته و دانلود می‌شوند.";
  }

  const pct = (n, total) => (total ? Math.round((100 * (n || 0)) / total) : 0);

  function bars(el, rows, usePercent = false) {
    const max = Math.max(1, ...rows.map((r) => r[1] || 0));
    el.innerHTML = rows.map(([label, value, total, kind]) => `
      <div class="bar-row">
        <span class="lbl" title="${esc(label)}">${esc(label)}</span>
        <span class="t"><span class="f ${kind || ""}" style="width:${Math.max(2, (100 * (value || 0)) / max)}%"></span></span>
        <span class="v">${fa(value || 0)}${usePercent ? ` · ${fa(pct(value, total))}٪` : ""}</span>
      </div>`).join("");
  }

  function renderSegChips() {
    const counts = { all: state.leads.length, hot: 0, warm: 0, cold: 0 };
    state.leads.forEach((l) => { counts[l.segment] = (counts[l.segment] || 0) + 1; });
    const items = [["all", "همه"], ["hot", "داغ 🔥"], ["warm", "گرم 🌤"], ["cold", "سرد ❄️"]];
    $("segChips").innerHTML = items.map(([id, label]) =>
      `<button data-seg="${id}" class="${state.segment === id || (id === "all" && !state.segment) ? "active" : ""}">
        ${label} <b>${fa(counts[id] || 0)}</b></button>`).join("");
    $("segChips").querySelectorAll("button").forEach((b) => b.onclick = () => {
      state.segment = b.dataset.seg === "all" ? "" : b.dataset.seg;
      state.page = 1; renderSegChips(); applyFilters();
    });
  }

  function applyFilters() {
    const q = (state.query || "").trim().toLowerCase();
    state.view = state.leads.filter((l) => {
      if (state.segment && l.segment !== state.segment) return false;
      if (q) {
        const hay = [l.title, l.niche_fa, l.address, l.city, l.phone, l.email_primary, l.email,
                     l.domain, l.plus_code, l.location, (l.tags || []).join(" ")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const by = state.sort;
    state.view.sort((a, b) => by === "title"
      ? String(a.title).localeCompare(String(b.title), "fa")
      : (+b[by] || 0) - (+a[by] || 0));
    renderLeads();
  }

  function renderLeads() {
    const pages = Math.max(1, Math.ceil(state.view.length / state.perPage));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * state.perPage;
    const rows = state.view.slice(start, start + state.perPage);
    $("leadsEmpty").hidden = rows.length > 0;
    $("cards").hidden = state.viewMode !== "cards";
    $("tableWrap").hidden = state.viewMode !== "table";

    if (state.viewMode === "cards") {
      $("cards").innerHTML = rows.map(cardHtml).join("");
      $("cards").querySelectorAll("[data-lead]").forEach((el) => el.onclick = (e) => {
        if (e.target.closest("a,button")) return;
        openLead(el.dataset.lead);
      });
    } else {
      $("tableBody").innerHTML = rows.map(rowHtml).join("");
      $("tableBody").querySelectorAll("tr[data-lead]").forEach((tr) => tr.onclick = (e) => {
        if (e.target.closest("a,button")) return;
        openLead(tr.dataset.lead);
      });
    }
    bindLeadActions();

    $("pager").innerHTML = pages > 1 ? `
      <button class="ghost-btn" data-p="prev" ${state.page <= 1 ? "disabled" : ""}>› قبلی</button>
      <span>صفحهٔ ${fa(state.page)} از ${fa(pages)} · ${fa(state.view.length)} لید</span>
      <button class="ghost-btn" data-p="next" ${state.page >= pages ? "disabled" : ""}>بعدی ‹</button>` :
      `<span>${fa(state.view.length)} لید</span>`;
    $("pager").querySelectorAll("[data-p]").forEach((b) => b.onclick = () => {
      state.page += b.dataset.p === "next" ? 1 : -1; renderLeads();
      $("results").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  const linkOrDash = (href, label, cls = "") => href
    ? `<a class="${cls}" href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}</a>`
    : '<span class="badge">—</span>';

  function cardHtml(l) {
    const socials = [
      l.instagram ? `<a href="https://instagram.com/${encodeURIComponent(l.instagram)}" target="_blank" rel="noopener" title="اینستاگرام">📸</a>` : "",
      l.facebook ? `<a href="https://facebook.com/${encodeURIComponent(l.facebook)}" target="_blank" rel="noopener" title="فیسبوک">👥</a>` : "",
      l.linkedin ? `<a href="https://linkedin.com/in/${encodeURIComponent(l.linkedin)}" target="_blank" rel="noopener" title="لینکدین">💼</a>` : "",
    ].join(" ");
    return `
    <article class="lead" data-lead="${esc(l.id)}">
      <div class="lead-top">
        <div class="ring ${esc(l.segment)}" style="--p:${Math.max(3, Math.min(100, +l.score || 0))}">
          <i>${fa(l.score)}</i></div>
        <div style="min-width:0">
          <h3 class="lead-name">${esc(l.title)}</h3>
          <p class="lead-sub">${esc(l.niche_fa || l.category || "")} · ${esc(l.city || "")}</p>
          <div class="badges" style="margin-top:.3rem">
            <span class="badge ${esc(l.segment)}">${esc(l.segment_fa || SEG_FA[l.segment] || "")}</span>
            ${l.website ? "" : '<span class="badge nosite">بدون وب‌سایت</span>'}
            ${(l.gaps_fa || []).slice(0, 1).map((g) => `<span class="badge gap">${esc(g)}</span>`).join("")}
            ${l.reviews ? `<span class="badge">⭐ ${fa(l.rating)} · ${fa(l.reviews)} نظر</span>` : ""}
          </div>
        </div>
      </div>
      <div class="facts">
        <div><span class="k">تلفن</span><span class="v">${l.phone ? linkOrDash(`tel:${l.phone}`, l.phone, "mono") : '<span class="badge">—</span>'}</span></div>
        <div><span class="k">ایمیل</span><span class="v">${l.email_primary ? linkOrDash(`mailto:${l.email_primary}`, l.email_primary, "mono") : '<span class="badge">—</span>'}</span></div>
        <div><span class="k">سایت</span><span class="v">${l.website ? linkOrDash(l.website, l.domain || l.website, "mono") : '<span class="badge nosite">ندارد</span>'}</span></div>
        <div><span class="k">شبکه</span><span class="v">${socials || '<span class="badge">—</span>'}</span></div>
        <div><span class="k">لوکیشن</span><span class="v">${l.maps_link ? linkOrDash(l.maps_link, l.location || "📍 نقشه", "mono") : '<span class="badge">—</span>'}</span></div>
      </div>
      <div class="lead-actions">
        <button class="act primary" data-act="msg" data-lead="${esc(l.id)}">💬 متن تماس</button>
        <button class="act" data-act="open" data-lead="${esc(l.id)}">پرونده</button>
        <button class="act ${l.status && l.status !== "new" ? "done" : ""}" data-act="status" data-lead="${esc(l.id)}"
          title="وضعیت فعلی: ${esc(l.status_fa || STATUS_FA[l.status] || "جدید")}">✓ ${esc(l.status_fa || STATUS_FA[l.status] || "جدید")}</button>
      </div>
    </article>`;
  }

  function rowHtml(l) {
    return `
    <tr data-lead="${esc(l.id)}">
      <td><b>${esc(l.title)}</b><br><small>${esc(l.niche_fa || "")}</small></td>
      <td>${esc(l.category || l.niche_fa || "")}</td>
      <td class="num">${l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : "—"}</td>
      <td class="num">${l.email_primary ? `<a href="mailto:${esc(l.email_primary)}">${esc(l.email_primary)}</a>` : "—"}</td>
      <td class="num">${l.website ? `<a href="${esc(l.website)}" target="_blank" rel="noopener">${esc(l.domain || "سایت")}</a>` : '<span class="badge nosite">ندارد</span>'}</td>
      <td class="num">${[l.instagram && "IG", l.facebook && "FB", l.linkedin && "IN"].filter(Boolean).join(" · ") || "—"}</td>
      <td class="num">${l.maps_link ? `<a href="${esc(l.maps_link)}" target="_blank" rel="noopener">📍</a>` : "—"}<br><small class="mono">${esc(l.plus_code || "")}</small></td>
      <td class="num">${fa(l.rating || "—")}</td>
      <td><span class="score-pill ${esc(l.segment)}">${fa(l.score)}</span></td>
      <td>${esc(l.status_fa || STATUS_FA[l.status] || "جدید")}</td>
    </tr>`;
  }

  function bindLeadActions() {
    document.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        const id = btn.dataset.lead;
        if (btn.dataset.act === "msg") { state.channel = "email"; await openLead(id, true); }
        else if (btn.dataset.act === "open") await openLead(id);
        else if (btn.dataset.act === "status") await cycleStatus(id, btn);
      };
    });
  }

  const STATUS_ORDER = ["new", "contacted", "replied", "qualified", "won", "lost"];

  async function cycleStatus(id, btn) {
    const lead = state.leads.find((l) => l.id === id);
    if (!lead) return;
    const next = STATUS_ORDER[(STATUS_ORDER.indexOf(lead.status || "new") + 1) % STATUS_ORDER.length];
    try {
      await backend.setStatus(id, next);
      lead.status = next; lead.status_fa = STATUS_FA[next];
      btn.textContent = `✓ ${lead.status_fa}`;
      btn.classList.toggle("done", next !== "new");
      toast(`وضعیت «${lead.title}» → ${lead.status_fa}`);
    } catch (err) { toast(`تغییر وضعیت ناموفق: ${err.message}`, "err"); }
  }

  // ── پروندهٔ لید + کمپین ──────────────────────────────────────────────
  async function openLead(id, focusCampaign = false) {
    let lead;
    try { lead = await backend.lead(id); }
    catch (err) { toast(`پروندهٔ لید باز نشد: ${err.message}`, "err"); return; }
    state.active = lead;
    if (lead.channel) state.channel = lead.channel;
    $("modalTitle").textContent = lead.title || "لید";
    $("modalBody").innerHTML = `
      <div class="badges">
        <span class="badge ${esc(lead.segment)}">${esc(lead.segment_fa || SEG_FA[lead.segment] || "")}</span>
        <span class="badge">امتیاز ${fa(lead.score)}</span>
        <span class="badge">کانال پیشنهادی: ${esc(lead.channel_fa || "")}</span>
        <span class="badge">اطلاعات ${fa(lead.completeness || 0)}٪ کامل</span>
      </div>
      <p class="summary" style="margin:.6rem 0 0;color:var(--muted)">زاویهٔ پیشنهادی مغز برنامه:
        <b style="color:var(--text)">${esc(lead.pitch || "")}</b></p>

      <h4 class="section-title">اطلاعات</h4>
      <dl class="kv">
        <dt>صنف</dt><dd>${esc(lead.niche_fa || lead.category || "—")}</dd>
        <dt>نشانی</dt><dd>${esc(lead.address || "—")}</dd>
        <dt>لوکیشن دقیق</dt><dd class="mono">${esc(lead.location || "—")}</dd>
        <dt>پلاس‌کد</dt><dd class="mono">${esc(lead.plus_code || "—")}</dd>
        <dt>نقشه</dt><dd>${lead.maps_link ? `<a href="${esc(lead.maps_link)}" target="_blank" rel="noopener">📍 باز کردن در گوگل‌مپ ↗</a>` : "—"}</dd>
        <dt>تلفن</dt><dd>${lead.phone ? `<a class="mono" href="tel:${esc(lead.phone)}">${esc(lead.phone)}</a>` : "—"}
          ${lead.phone ? `· <a href="https://wa.me/${esc(String(lead.phone).replace(/[^0-9]/g, ""))}" target="_blank" rel="noopener">واتس‌اپ</a>` : ""}</dd>
        <dt>ایمیل</dt><dd>${lead.email_primary ? `<a class="mono" href="mailto:${esc(lead.email_primary)}">${esc(lead.email_primary)}</a>` : "—"}
          ${lead.emails && lead.emails !== lead.email_primary ? `<br><small class="mono">${esc(lead.emails)}</small>` : ""}</dd>
        <dt>وب‌سایت</dt><dd>${lead.website ? `<a class="mono" href="${esc(lead.website)}" target="_blank" rel="noopener">${esc(lead.website)}</a>` : '<span class="badge nosite">ندارد — فرصت فروش سایت</span>'}</dd>
        <dt>شبکه اجتماعی</dt><dd>${[
          lead.instagram && `<a href="https://instagram.com/${esc(lead.instagram)}" target="_blank" rel="noopener">اینستاگرام</a>`,
          lead.facebook && `<a href="https://facebook.com/${esc(lead.facebook)}" target="_blank" rel="noopener">فیسبوک</a>`,
          lead.linkedin && `<a href="https://linkedin.com/in/${esc(lead.linkedin)}" target="_blank" rel="noopener">لینکدین</a>`,
        ].filter(Boolean).join(" · ") || "—"}</dd>
        <dt>امتیاز گوگل</dt><dd>${fa(lead.rating || "—")} از ۵ · ${fa(lead.reviews || 0)} نظر</dd>
      </dl>

      <h4 class="section-title">چرا این امتیاز؟</h4>
      ${(lead.score_factors || []).map((f) => `
        <div class="fx">${esc(f.fa)} — <b>${fa(Math.round(f.points ?? 0))}</b> از ${fa(f.weight ?? 0)}
          <span class="t"><span class="f" style="width:${Math.min(100, (100 * (f.points || 0)) / (f.weight || 1))}%"></span></span>
          <small>${esc(f.detail || "")}</small></div>`).join("") || "<p class='empty'>جزئیاتی نیست.</p>"}

      <h4 class="section-title">شکاف‌ها و برچسب‌ها</h4>
      <div class="badges">${(lead.gaps_fa || []).map((g) => `<span class="badge gap">${esc(g)}</span>`).join("")}
        ${(lead.tags || []).filter((t) => !(lead.gaps_fa || []).includes(t)).map((t) => `<span class="badge">${esc(t)}</span>`).join("")}</div>

      <h4 class="section-title">وضعیت پیگیری</h4>
      <div class="status-row" id="statusRow"></div>

      <h4 class="section-title">متن آمادهٔ ارسال</h4>
      <div class="tabs" id="campTabs"></div>
      <div class="sender">
        <input id="sName" placeholder="نام شما" value="${esc(state.sender.name)}">
        <input id="sCompany" placeholder="نام شرکت/برند" value="${esc(state.sender.company)}">
        <input id="sPhone" placeholder="تلفن شما" value="${esc(state.sender.phone)}">
        <input id="sEmail" placeholder="ایمیل شما" value="${esc(state.sender.email)}">
      </div>
      <div class="msg-head" id="msgHead"></div>
      <div class="msg" id="msgBox"></div>
      <div class="actions" id="msgActions"></div>`;

    renderStatusRow(lead);
    bindSenderInputs();
    await renderCampaign();
    $("modal").hidden = false; $("scrim").hidden = false;
    if (focusCampaign) $("campTabs").scrollIntoView({ block: "center" });
  }

  function renderStatusRow(lead) {
    const row = $("statusRow");
    if (!row) return;
    row.innerHTML = STATUS_ORDER.map((s) =>
      `<button class="tab ${(lead.status || "new") === s ? "active" : ""}" data-status="${s}">${STATUS_FA[s]}</button>`).join("");
    row.querySelectorAll("[data-status]").forEach((b) => b.onclick = async () => {
      try {
        await backend.setStatus(lead.id, b.dataset.status);
        lead.status = b.dataset.status; lead.status_fa = STATUS_FA[b.dataset.status];
        renderStatusRow(lead);
        const card = document.querySelector(`.lead[data-lead="${CSS.escape(lead.id)}"] [data-act=status]`);
        if (card) { card.textContent = `✓ ${lead.status_fa}`; card.classList.toggle("done", lead.status !== "new"); }
        const target = state.leads.find((l) => l.id === lead.id);
        if (target) { target.status = lead.status; target.status_fa = lead.status_fa; }
        toast(`وضعیت → ${lead.status_fa}`);
      } catch (err) { toast(`ناموفق: ${err.message}`, "err"); }
    });
  }

  function bindSenderInputs() {
    [["sName", "name"], ["sCompany", "company"], ["sPhone", "phone"], ["sEmail", "email"]].forEach(([id, key]) => {
      const el = $(id);
      if (el) el.oninput = (e) => { state.sender[key] = e.target.value; renderCampaign(); };
    });
  }

  async function renderCampaign() {
    const lead = state.active;
    if (!lead) return;
    const channels = ["email", "whatsapp", "sms", "instagram", "phone"];
    $("campTabs").innerHTML = channels.map((c) =>
      `<button class="tab ${c === state.channel ? "active" : ""}" data-c="${c}">${esc(channelFa(c))}</button>`).join("");
    $("campTabs").querySelectorAll("[data-c]").forEach((b) => b.onclick = () => {
      state.channel = b.dataset.c; renderCampaign();
    });

    let msg = { to: "", subject: "", text: "" };
    lead.campaign = lead.campaign || {};
    let cached = lead.campaign[state.channel];
    if (cached && senderEdited() && state.mode === "live") {
      // مشخصات فرستنده را کاربر پر کرده → متن معتبر را از خود سرور (مغز برنامه) بگیر
      try {
        cached = await Live.campaign(lead.id, state.channel, state.sender);
        lead.campaign[state.channel] = cached;
      } catch { /* در صورت خطا، همان متن قبلی نشان داده می‌شود */ }
    }
    if (cached && (!senderEdited() || state.mode === "live")) {
      msg = { to: cached.to || "", subject: cached.subject || "", text: cached.text || cached.body || "" };
    } else if (window.LeadCampaign) {
      syncSender();
      const local = window.LeadCampaign.messageFor(
        { ...lead, email: lead.email_primary || lead.email, gaps_fa: lead.gaps_fa },
        state.channel);
      msg = { ...local, subject: local.subject || (cached && cached.subject) || "" };
      if (state.channel === "email" && cached && cached.sequence) {
        msg.text = cached.sequence.map((t2) => `[لمس ${t2.step} — روز ${t2.day}]\nموضوع: ${t2.subject}\n\n${t2.body}`).join("\n\n---\n\n");
      }
    }

    $("msgHead").innerHTML = `<span>گیرنده: <b class="mono">${esc(msg.to || "—")}</b></span>` +
      (msg.subject ? `<span>موضوع: <b>${esc(msg.subject)}</b></span>` : "") +
      `<span>${fa((msg.text || "").length)} نویسه</span>`;
    $("msgBox").textContent = msg.text || "متنی تولید نشد.";

    const actions = [];
    if (state.channel === "email" && msg.to) {
      actions.push(`<a class="act primary" href="mailto:${encodeURIComponent(msg.to)}?subject=${encodeURIComponent(msg.subject)}&body=${encodeURIComponent(msg.text.split("\n\n---\n\n")[0] || msg.text)}">✉ باز کردن در ایمیل</a>`);
    }
    if (state.channel === "whatsapp" && lead.phone) {
      const digits = String(lead.phone).replace(/\D/g, "");
      actions.push(`<a class="act primary" target="_blank" rel="noopener" href="https://wa.me/${digits}?text=${encodeURIComponent(msg.text)}">💬 واتس‌اپ</a>`);
    }
    if (state.channel === "phone" && lead.phone) actions.push(`<a class="act primary" href="tel:${esc(lead.phone)}">📞 تماس</a>`);
    if (state.channel === "instagram" && lead.instagram) {
      actions.push(`<a class="act" target="_blank" rel="noopener" href="https://instagram.com/${esc(lead.instagram)}">📸 پروفایل</a>`);
    }
    actions.push(`<button class="act" id="copyMsg">📋 کپی متن</button>`);
    $("msgActions").innerHTML = actions.join("");
    $("copyMsg").onclick = () => copyText(msg.text, "متن کپی شد");
  }

  const senderEdited = () => Object.values(state.sender).some((v) => String(v || "").trim() !== "");

  // مشخصات فرستنده را به ماژول قالب‌ها (حالت دمو) منتقل می‌کند
  function syncSender() {
    if (window.LeadCampaign && window.LeadCampaign.sender) Object.assign(window.LeadCampaign.sender, state.sender);
  }

  function channelFa(c) {
    return (window.LeadCampaign && window.LeadCampaign.CHANNEL_FA[c]) ||
      { email: "ایمیل", whatsapp: "واتس‌اپ", sms: "پیامک", instagram: "اینستاگرام", phone: "تماس تلفنی" }[c] || c;
  }

  function closeModal() {
    $("modal").hidden = true; $("scrim").hidden = true; state.active = null;
  }

  // ── خروجی‌ها ────────────────────────────────────────────────────────
  function doExport(kind) {
    if (!state.runId) { toast("اول یک اجرا انجام دهید", "warn"); return; }
    if (state.mode === "demo") return demoExport(kind);
    const cols = $("cols").value;
    const href = kind === "campaign"
      ? `/api/runs/${state.runId}/export?what=campaign&fmt=csv&channel=email&sender=${encodeURIComponent(JSON.stringify(state.sender))}`
      : kind === "hot"
        ? `/api/runs/${state.runId}/export?fmt=xlsx&cols=${cols}&segment=hot`
        : `/api/runs/${state.runId}/export?fmt=${kind}&cols=${cols}`;
    window.location.href = href;
    toast("دانلود شروع شد");
  }

  function demoExport(kind) {
    const cols = (state.demoData.columns || {})[state.cols || $("cols").value] || [];
    const rows = kind === "hot" ? state.view.filter((l) => l.segment === "hot") : state.view;
    if (kind === "json") {
      downloadBlob("leadgen-demo-leads.json", JSON.stringify(rows, null, 2), "application/json;charset=utf-8");
    } else {
      const value = (l, key) => {
        if (key === "email") return l.email || l.email_primary || "";
        if (key === "gaps") return (l.gaps_fa || []).join(" | ");
        if (key === "tags") return (l.tags || []).join(" | ");
        if (key === "status") return l.status || "new";
        if (key === "score_factors") return (l.score_factors || []).map((f) => `${f.fa}:${f.points}`).join(" | ");
        return l[key] ?? "";
      };
      const cell = (v) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
      if (kind === "campaign") {
        const head = ["نام کسب‌وکار", "کانال", "گیرنده", "موضوع", "متن"];
        syncSender();
        const body = rows.map((l) => {
          const m = window.LeadCampaign.messageFor({ ...l, email: l.email_primary || l.email }, l.channel || "email");
          return [l.title, channelFa(l.channel || "email"), m.to, m.subject, m.text];
        });
        downloadBlob("leadgen-demo-campaign.csv",
          "\ufeff" + [head, ...body].map((r) => r.map(cell).join(",")).join("\n"), "text/csv;charset=utf-8");
      } else {
        const csv = [cols.map((c) => cell(c[1])).join(","),
                     ...rows.map((l) => cols.map((c) => cell(value(l, c[0]))).join(","))].join("\n");
        downloadBlob(`leadgen-demo-${state.cols || "contact"}.${kind === "xlsx" ? "csv" : kind}`,
          "\ufeff" + csv, "text/csv;charset=utf-8");
        if (kind === "xlsx") toast("در حالت دمو، اکسل به CSV تبدیل می‌شود (بدون سرور)", "warn");
      }
    }
    toast(`${fa(rows.length)} ردیف دانلود شد`);
  }

  // ── تاریخچه ─────────────────────────────────────────────────────────
  async function loadHistory() {
    try {
      const runs = await backend.runs();
      const box = $("history");
      if (!runs.length) { $("historyPanel").hidden = true; return; }
      $("historyPanel").hidden = false;
      box.innerHTML = runs.slice(0, 8).map((r) => `
        <div class="hist" data-run="${esc(r.id)}">
          <b>${esc(r.niche_fa || "—")}</b>
          <span>${esc(r.city || "")} · ${esc(r.engine || "")}</span>
          <div class="nums"><span>${fa(r.total || 0)} لید</span><span>🔥 ${fa(r.hot || 0)}</span>
            <span>${esc(r.status_fa || r.status || "")}</span></div>
        </div>`).join("");
      box.querySelectorAll("[data-run]").forEach((el) => el.onclick = async () => {
        state.runId = el.dataset.run;
        try { await loadRun(state.runId); $("results").scrollIntoView({ behavior: "smooth" }); }
        catch (err) { toast(`باز کردن اجرا ناموفق: ${err.message}`, "err"); }
      });
      // اولین بازدید خالی نماند: تازه‌ترین اجرا خودش بارگذاری می‌شود
      if (!state.runId && runs.length && $("results").hidden) {
        try {
          state.runId = runs[0].id;
          await loadRun(state.runId);
          $("results").scrollIntoView({ behavior: "smooth", block: "start" });
          toast("آخرین اجرا بارگذاری شد — می‌توانید جست‌وجوی تازه شروع کنید");
        } catch { state.runId = null; $("results").hidden = true; }
      }
    } catch { $("historyPanel").hidden = true; }
  }

  // ── رویدادها ────────────────────────────────────────────────────────
  function bindEvents() {
    const onQuery = debounce(onQueryChange, 420);
    $("q").addEventListener("input", onQuery);
    $("q").addEventListener("keydown", (e) => { if (e.key === "Enter") startRun(); });
    $("go").onclick = startRun;
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-card")) hideSuggest();
    });

    $("themeBtn").onclick = () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem("leadgen-theme", next); applyTheme(next);
    };

    $("demoMode").onchange = async (e) => {
      if (e.target.checked) { await switchToDemo(); toast("حالت دمو فعال شد — دادهٔ نمونه، فوری"); }
      else { backend = Live; state.mode = "live";
             try { renderEngine((await Live.health()).engine); state.options = { ...FALLBACK_OPTIONS, ...(await Live.options()) }; renderOptions(); await loadHistory(); toast("حالت زنده فعال شد"); }
             catch (err) { toast(`سرور در دسترس نیست: ${err.message}`, "err"); e.target.checked = true; await switchToDemo(); } }
      adaptStaticLinks();
      onQueryChange();
    };
    ["cols", "fmt", "engineMode", "depth", "grid", "limit", "socials", "emails"].forEach((id) => {
      $(id).addEventListener("change", () => { state.cols = $("cols").value; state.fmt = $("fmt").value; onQueryChange(); });
    });

    $("leadSearch").addEventListener("input", debounce((e) => {
      state.query = e.target.value; state.page = 1; applyFilters();
    }, 220));
    $("leadSort").onchange = (e) => { state.sort = e.target.value; applyFilters(); };
    document.querySelectorAll(".view-toggle button").forEach((b) => b.onclick = () => {
      document.querySelectorAll(".view-toggle button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); state.viewMode = b.dataset.view; renderLeads();
    });
    document.querySelectorAll("[data-export]").forEach((b) => b.onclick = () => doExport(b.dataset.export));
    $("cancelBtn").onclick = () => { $("progressPanel").hidden = true; };
    $("modalClose").onclick = closeModal;
    $("scrim").onclick = closeModal;
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); hideSuggest(); } });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
