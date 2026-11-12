/* نُوا — اپ اصلی (وب + موبایل، بدون هیچ کتابخانهٔ بیرونی)
   لایه‌ها: حالت برنامه ← رابط زنده/نمایشی (Live/Demo) ← نماها ← رویدادها ← پولینگ.
   همهٔ متن‌ها عامیانه و فارسی؛ اعداد فارسی؛ راست‌به‌چپ. */
(function () {
  "use strict";
  var N = globalThis.Nova;
  var $ = N.$, esc = N.esc, fa = N.fa, num = N.num, pct = N.pct, ago = N.ago, clock = N.clock;

  /* ────────────────────────── حالت برنامه ────────────────────────── */
  var S = {
    mode: "live",            // live | demo
    options: null,
    health: null,
    demo: null,              // {leads, meta, analytics}
    demoProg: null,          // شبیه‌سازی پیشرفت اجرای نمایشی
    parse: null,
    goal: "site",
    size: "standard",
    area: { label: "تهران", province: "tehran-province", city: "tehran", district: "", street: "" },
    niche: { label: "همهٔ کسب‌وکارها", query: "" },
    run: null,               // {run_id, title}
    pollTimer: null,
    filters: { q: "", segment: "", status: "", run_id: "", has: {} },
    sort: "score",
    view: "cards",
    offset: 0,
    total: 0,
    facets: null,
    sugHi: -1,
    ticking: false,          // tick صف در جریان است
    tickAt: 0,               // آخرین tick
    codeOpen: false          // شیت کد دسترسی باز است
  };
  var PAGE = 24;

  var SEG_FA = { hot: "🔥 داغ", warm: "🌤 گرم", cold: "🧊 سرد" };
  var STATUS_FA = { "new": "جدید", contacted: "تماس گرفته‌شده", won: "مشتری شد", lost: "نشده" };
  var HAS_FA = { has_phone: "📞 تلفن", has_email: "✉️ ایمیل", has_website: "🌐 سایت", has_instagram: "📸 اینستاگرام" };

  var FALLBACK_OPTIONS = {
    goals: [
      { id: "site", icon: "🖥️", fa: "سایت یا فروشگاه می‌فروشم", profile: "web_design", hint: "بدون‌سایت‌ها داغ‌ترین لید توان." },
      { id: "ads", icon: "📣", fa: "تبلیغات، سئو یا اینستاگرام", profile: "marketing", hint: "روی دیده‌شدن و نظر کم کار می‌کنه." },
      { id: "supply", icon: "📦", fa: "کالا یا تجهیزات می‌فروشم", profile: "supply", hint: "پخش و عمده‌فروشی." },
      { id: "service", icon: "🛠️", fa: "خدمت یا پشتیبانی می‌دم", profile: "services", hint: "تعمیر، نگهداری، مشاوره." },
      { id: "any", icon: "🎲", fa: "فرقی نداره، فقط لید خوب", profile: "balanced", hint: "امتیاز متعادل." }
    ],
    sizes: [
      { id: "quick", icon: "⚡", fa: "سریع", hint: "چند دقیقه", depth: 2, grid: 0, limit: 60 },
      { id: "standard", icon: "🎯", fa: "استاندارد", hint: "یه صنف، یه شهر", depth: 4, grid: 0, limit: 200 },
      { id: "deep", icon: "🛰", fa: "کل شهر", hint: "کامل، کندتر", depth: 5, grid: 3, limit: 0 }
    ],
    examples: ["دندانپزشکی در تهران", "همه مشاغل در مشهد", "کافه در اصفهان"],
    groups: ["سلامت و پزشکی", "خوراک و کافه", "خدمات فنی"]
  };

  var ENGINE_FA = {
    docker: "واقعی (گوگل‌مپ) 🟢", osm: "واقعی (OpenStreetMap) 🟢",
    places: "واقعی (گوگل Places) 🟢", demo: "نمایشی 🎬", unavailable: "در دسترس نیست"
  };
  var ENGINE_CHIP = {
    docker: "موتور واقعی", osm: "دادهٔ واقعی · OSM", places: "گوگل Places",
    demo: "حالت نمایشی", unavailable: "بدون موتور"
  };

  /* ────────────────────────── شبکه ────────────────────────── */
  function code() { try { return localStorage.getItem("nova.code") || ""; } catch (e) { return ""; } }
  function authHeaders(extra) {
    var h = { accept: "application/json" };
    if (extra) for (var k in extra) h[k] = extra[k];
    var c = code();
    if (c) h["x-leadgen-code"] = c;
    return h;
  }
  function ok(r) {
    if (!r.ok) {
      if (r.status === 401) { askCode(); }
      return r.json().then(function (j) { throw new Error(j.error || j.message || ("HTTP " + r.status)); },
        function () { throw new Error("HTTP " + r.status); });
    }
    return r.json();
  }
  function jget(url) { return fetch(url, { headers: authHeaders() }).then(ok); }
  function jsend(url, method, body) {
    return fetch(url, { method: method, headers: authHeaders({ "content-type": "application/json" }), body: JSON.stringify(body || {}) }).then(ok);
  }
  function askCode() {
    if (S.codeOpen) return;
    S.codeOpen = true;
    N.sheet("🔒 کد دسترسی", '<p class="muted">این نسخه با کد قفله. کدی که گرفتی رو بزن تا باز بشه.</p>' +
      '<form id="codeForm" class="code-form"><input id="codeInput" type="password" inputmode="text" ' +
      'autocomplete="off" placeholder="کد دسترسی" value="' + esc(code()) + '">' +
      '<button class="btn btn-primary" type="submit">باز کن</button></form>' +
      '<p class="muted" id="codeErr" hidden>کد درست نبود. دوباره امتحان کن.</p>', function () {
        var f = $("codeForm");
        if (f) f.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var v = ($("codeInput").value || "").trim();
          if (!v) return;
          try { localStorage.setItem("nova.code", v); } catch (e) {}
          S.codeOpen = false;
          N.sheetHide();
          boot(true);
        });
      });
  }
  /* اجرای خودکار صف: وقتی کاری در صف مانده و ورکر بیرونی نیست، خودمان بیدارش می‌کنیم */
  function maybeTick(p) {
    if (!p || p.action !== "tick" || S.ticking) return;
    if (Date.now() - (S.tickAt || 0) < 2500) return;
    S.tickAt = Date.now(); S.ticking = true;
    jsend("api/worker/tick?budget=42", "POST", {}).catch(function () { /* دور بعد */ })
      .then(function () { S.ticking = false; });
  }

  /* ────────────────────────── دادهٔ نمایشی ────────────────────────── */
  var DEMO_CANDIDATES = ["demo-data.json", "data/sample.json", "../data/sample.json", "../site/data/sample.json"];
  function loadDemo() {
    var i = 0;
    function next() {
      if (i >= DEMO_CANDIDATES.length) return Promise.resolve(null);
      var url = DEMO_CANDIDATES[i++];
      return fetch(url).then(function (r) { return r.ok ? r.json() : next(); }, next);
    }
    return next().then(function (d) {
      if (d && d.leads) S.demo = d;
      return S.demo;
    });
  }
  function demoNormCity(l) { return String(l.city || "") + " " + String(l.address || ""); }

  /* ────────────────────────── رابط زنده/نمایشی ────────────────────────── */
  var API = {
    health: function (fresh) {
      if (S.mode === "demo") return Promise.resolve(demoHealth());
      return jget("api/health" + (fresh ? "?fresh=1" : ""));
    },
    /* سنجش زندهٔ موتور روی یک شهر/صنف؛ بدون نوشتن در پایگاه‌داده. */
    diag: function (city, niche, all) {
      if (S.mode === "demo") return Promise.resolve(demoDiag(city, niche));
      var qs = "api/diag/osm?city=" + encodeURIComponent(city || "") +
        "&niche=" + encodeURIComponent(all ? "" : (niche || "")) + (all ? "&all=1" : "");
      return jget(qs);
    },
    options: function () {
      if (S.mode === "demo") return Promise.resolve(demoOptions());
      return jget("api/smart/options");
    },
    suggest: function (q) {
      if (S.mode === "demo") return Promise.resolve(demoSuggest(q));
      return jget("api/smart/suggest?q=" + encodeURIComponent(q) + "&limit=6");
    },
    parse: function (q) {
      if (S.mode === "demo") return Promise.resolve(demoParse(q));
      return jsend("api/smart/parse", "POST", { query: q });
    },
    start: function (req) {
      if (S.mode === "demo") return demoStart(req);
      return jsend("api/runs", "POST", req);
    },
    progress: function (runId) {
      if (S.mode === "demo") return Promise.resolve({ progress: demoTick() });
      return jget("api/runs/" + encodeURIComponent(runId) + "/progress").then(function (j) { return j.progress ? j : { progress: j }; });
    },
    runs: function () {
      if (S.mode === "demo") return Promise.resolve({ runs: demoRuns(), stats: demoStats() });
      return jget("api/runs?limit=30");
    },
    queue: function () {
      if (S.mode === "demo") return Promise.resolve({ mode: "never", active: false, stats: { pending: 0, running: 0, done: 0, failed: 0, oldest_wait_s: 0 }, jobs: [] });
      return jget("api/queue");
    },
    leads: function (params) {
      if (S.mode === "demo") return Promise.resolve(demoSearch(params));
      var base = params.run_id ? "api/runs/" + encodeURIComponent(params.run_id) + "/leads?" : "api/search?";
      var q = [];
      Object.keys(params).forEach(function (k) {
        if (k === "run_id") return;
        var v = params[k];
        if (v === "" || v == null || v === false) return;
        q.push(encodeURIComponent(k) + "=" + encodeURIComponent(v === true ? 1 : v));
      });
      return jget(base + q.join("&"));
    },
    lead: function (id) {
      if (S.mode === "demo") {
        var l = (S.demo ? S.demo.leads : []).filter(function (x) { return x.id === id || x.place_id === id; })[0];
        return l ? Promise.resolve(l) : Promise.reject(new Error("پیدا نشد"));
      }
      return jget("api/leads/" + encodeURIComponent(id)).then(function (j) { return j.lead || j; });
    },
    setStatus: function (id, status) {
      if (S.mode === "demo") {
        var l = (S.demo ? S.demo.leads : []).filter(function (x) { return x.id === id || x.place_id === id; })[0];
        if (l) { l.status = status; l.status_fa = STATUS_FA[status] || status; }
        return Promise.resolve(l || {});
      }
      return jsend("api/leads/" + encodeURIComponent(id), "PATCH", { status: status });
    },
    delRun: function (id) {
      if (S.mode === "demo") return Promise.resolve({ ok: true });
      return jsend("api/runs/" + encodeURIComponent(id), "DELETE", {});
    },
    exportHref: function (runId, fmt) {
      var href = "api/runs/" + encodeURIComponent(runId) + "/export?fmt=" + (fmt || "xlsx") + "&cols=contact";
      return code() ? href + "&code=" + encodeURIComponent(code()) : href;
    }
  };

  /* ── توابع حالت نمایشی ── */
  function demoHealth() {
    var leads = S.demo ? S.demo.leads : [];
    var hot = leads.filter(function (l) { return l.segment === "hot"; }).length;
    return {
      ok: true, version: "nova-demo",
      engine: { active: "demo", live: false, reason: "حالت نمایشی — دادهٔ نمونهٔ داخل خود برنامه", base_url: "" },
      config: { db_backend: "demo", queue_mode: "never" },
      db: { runs: 1, leads: leads.length, hot: hot, contacted: 0, won: 0, with_email: leads.filter(function (l) { return l.email; }).length },
      queue_mode: "never", email_transport: "none", digest_to: []
    };
  }
  /* پاسخ سنجش در حالت نمایشی: صادقانه می‌گوید دادهٔ واقعی نیست. */
  function demoDiag(city, niche) {
    var leads = (S.demo ? S.demo.leads : []).filter(function (l) {
      var okCity = !city || String(l.city || "").indexOf(city) >= 0 || String(l.address || "").indexOf(city) >= 0;
      var okNiche = !niche || String(l.niche_fa || l.category || "").indexOf(niche) >= 0;
      return okCity && okNiche;
    });
    return {
      ok: true, demo: true, city: city || "تهران", niche: niche || "همهٔ کسب‌وکارها",
      rows: leads.length, seconds: 0,
      with_phone: leads.filter(function (l) { return l.phone; }).length,
      with_website: leads.filter(function (l) { return l.website; }).length,
      with_address: leads.filter(function (l) { return l.address; }).length,
      sample: leads.slice(0, 3).map(function (l) {
        return { title: l.title, category: l.niche_fa || l.category, link: "" };
      })
    };
  }
  function demoCatalog() {
    /* فهرست صنف‌های حالت نمایشی از خود لیدهای نمونه ساخته می‌شود
       (meta.catalog فقط شمارنده است، نه فهرست). */
    if (S._catalog) return S._catalog;
    var seen = {}, out = [];
    (S.demo ? S.demo.leads : []).forEach(function (l) {
      var faName = l.niche_fa || l.category || "";
      if (!faName || seen[faName]) return;
      seen[faName] = 1;
      out.push({ id: faName, fa: faName, group: l.niche_group || "سایر", hint: "" });
    });
    S._catalog = out;
    return out;
  }
  function demoOptions() {
    var o = JSON.parse(JSON.stringify(FALLBACK_OPTIONS));
    var gs = {};
    demoCatalog().forEach(function (n) { if (n.group) gs[n.group] = 1; });
    if (Object.keys(gs).length) o.groups = Object.keys(gs);
    return o;
  }
  function demoSuggest(q) {
    q = (q || "").trim();
    var out = { query: q, niches: [], cities: [], groups: [], all_trades: /همه|تمام/.test(q) };
    if (!q) return out;
    demoCatalog().forEach(function (n) {
      if (out.niches.length < 4 && (n.fa || "").indexOf(q) >= 0) out.niches.push({ id: n.id, fa: n.fa, group: n.group, hint: n.hint || "" });
    });
    ["تهران", "مشهد", "اصفهان", "شیراز", "تبریز"].forEach(function (c) {
      if (out.cities.length < 3 && c.indexOf(q) >= 0) out.cities.push({ fa: c, key: c });
    });
    return out;
  }
  function demoParse(q) {
    q = (q || "").trim();
    var all = /همه|تمام/.test(q);
    var niche = null;
    demoCatalog().forEach(function (n) { if (!niche && (q.indexOf(n.fa) >= 0 || (n.fa || "").indexOf(q) >= 0)) niche = n; });
    var city = null;
    ["تهران", "مشهد", "اصفهان", "شیراز", "تبریز", "کرج", "قم"].forEach(function (c) { if (!city && q.indexOf(c) >= 0) city = c; });
    var detected = {
      niche_id: niche ? niche.id : "", niche_fa: niche ? niche.fa : "", all_trades: all,
      city: city || S.area.label, city_key: "", group: niche ? niche.group : ""
    };
    var req = {
      niche_query: all ? "همهٔ کسب‌وکارها" : (niche ? niche.fa : q), city_query: city || S.area.label,
      engine_mode: "demo", depth: +$("depth").value || 4, grid: +$("grid").value || 0,
      limit: +$("limit").value || 0, socials: $("socials").checked, emails: $("emails").checked,
      profile: goalProfile(), lang: "fa"
    };
    return {
      query: q, mode: all ? "city" : "single", ready: true, missing: [], detected: detected,
      keywords: [req.niche_query], eta_minutes: [0, 1], warnings: [],
      summary: (all ? "همهٔ کسب‌وکارها" : req.niche_query) + " در " + req.city_query + " — حالت نمایشی، چند ثانیه",
      request: req, needs_confirmation: false
    };
  }
  function demoFiltered(req) {
    var leads = (S.demo ? S.demo.leads : []).slice();
    var nq = (req && req.niche_query) || "";
    if (nq && nq.indexOf("همه") < 0) leads = leads.filter(function (l) { return (l.niche_fa || l.category || "").indexOf(nq) >= 0 || (l.title || "").indexOf(nq) >= 0; });
    var cq = (req && req.city_query) || "";
    if (cq) leads = leads.filter(function (l) { return demoNormCity(l).indexOf(cq) >= 0; });
    return leads;
  }
  function demoStart(req) {
    var leads = demoFiltered(req);
    var id = "demo-" + Date.now().toString(36);
    S.demoProg = { pct: 0, leads: leads, req: req, id: id, stage: "working" };
    try {
      var saved = JSON.parse(localStorage.getItem("nova-demo-runs") || "[]");
      saved.unshift({ id: id, created_at: Date.now() / 1000, niche_fa: req.niche_query, city: req.city_query, status: "done", total: leads.length, hot: leads.filter(function (l) { return l.segment === "hot"; }).length, engine: "demo" });
      localStorage.setItem("nova-demo-runs", JSON.stringify(saved.slice(0, 12)));
    } catch (e) { /* بی‌خیال */ }
    return new Promise(function (res) { setTimeout(function () { res({ run_id: id, status: "pending", poll: "demo", request: req }); }, 250); });
  }
  function demoTick() {
    var p = S.demoProg;
    if (!p) return { stage: "done", status: "done", percent: 100, message: "تمام شد" };
    p.pct = Math.min(100, p.pct + 7 + Math.random() * 12);
    if (p.pct >= 100) {
      p.stage = "done";
      return {
        stage: "done", status: "done", percent: 100, run_id: p.id,
        message: "تکمیل شد — " + fa(p.leads.length) + " لید (" + fa(p.leads.filter(function (l) { return l.segment === "hot"; }).length) + " داغ)",
        stats: { total: p.leads.length, hot: p.leads.filter(function (l) { return l.segment === "hot"; }).length }
      };
    }
    var stages = [["scrape", "دارم نقشه رو جارو می‌کنم…"], ["clean", "دارم تکراری‌ها رو حذف می‌کنم…"], ["enrich", "دارم تلفن و ایمیل و اینستاگرام رو دربیارم…"], ["score", "دارم داغ‌ها رو جدا می‌کنم…"]];
    var st = stages[Math.min(stages.length - 1, Math.floor(p.pct / 26))];
    return { stage: st[0], status: "working", percent: Math.round(p.pct), message: st[1], run_id: p.id };
  }
  function demoRuns() {
    var saved = [];
    try { saved = JSON.parse(localStorage.getItem("nova-demo-runs") || "[]"); } catch (e) { /* بی‌خیال */ }
    if (!saved.length && S.demo) {
      saved = [{ id: "demo-sample", created_at: Date.now() / 1000 - 5400, niche_fa: "نمونهٔ همه اصناف", city: "تهران", status: "done", total: S.demo.leads.length, hot: S.demo.leads.filter(function (l) { return l.segment === "hot"; }).length, engine: "demo" }];
    }
    return saved;
  }
  function demoStats() {
    var leads = S.demo ? S.demo.leads : [];
    return { runs: demoRuns().length, leads: leads.length, contacted: 0, won: 0, with_email: leads.filter(function (l) { return l.email; }).length };
  }
  function demoSearch(p) {
    var leads = (S.demo ? S.demo.leads : []).slice();
    if (p.run_id && S.demoProg && S.demoProg.id === p.run_id) leads = S.demoProg.leads.slice();
    if (p.q) {
      var toks = String(p.q).toLowerCase().split(/\s+/).filter(Boolean);
      leads = leads.filter(function (l) {
        var hay = (l.title + " " + l.niche_fa + " " + l.category + " " + demoNormCity(l) + " " + (l.address || "")).toLowerCase();
        return toks.every(function (t) { return hay.indexOf(t) >= 0; });
      });
    }
    if (p.segment) leads = leads.filter(function (l) { return l.segment === p.segment; });
    if (p.status) leads = leads.filter(function (l) { return l.status === p.status; });
    ["has_phone", "has_email", "has_website", "has_instagram"].forEach(function (k) {
      if (!p[k]) return;
      leads = leads.filter(function (l) {
        if (k === "has_phone") return !!l.phone;
        if (k === "has_email") return !!l.email;
        if (k === "has_website") return !!l.website;
        return !!l.instagram;
      });
    });
    var sort = p.sort || "score";
    leads.sort(function (a, b) {
      if (sort === "reviews") return (b.reviews || 0) - (a.reviews || 0);
      if (sort === "rating") return (b.rating || 0) - (a.rating || 0);
      if (sort === "completeness") return (b.completeness || 0) - (a.completeness || 0);
      if (sort === "created_at") return 0;
      return (b.score || 0) - (a.score || 0);
    });
    var off = p.offset || 0, lim = p.limit || PAGE;
    var facets = { segment: {}, status: {}, total: leads.length };
    leads.forEach(function (l) { facets.segment[l.segment] = (facets.segment[l.segment] || 0) + 1; });
    return { leads: leads.slice(off, off + lim), total: leads.length, returned: Math.min(lim, leads.length - off), facets: facets };
  }

  /* ────────────────────────── ابزارهای رندر ────────────────────────── */
  function goalProfile() {
    var g = (S.options ? S.options.goals : FALLBACK_OPTIONS.goals).filter(function (x) { return x.id === S.goal; })[0];
    return g ? g.profile : "balanced";
  }
  function emojiFor(lead) {
    var g = lead.niche_group || lead.group || "";
    var n = (lead.niche_fa || lead.category || "") + g;
    var map = [
      [/دندان|پزشک|سلامت|درمان|دارو|کلینیک|باشگاه|ورزش/, "🩺"], [/کافه|رستوران|فست|قنادی|کترینگ|پیتزا|بستنی|خوراک/, "🍽️"],
      [/آرایش|زیبایی|سالن|مو/, "💇"], [/برق|لوله|تعمیر|فنی|سقف|ایزوگام|ساختمان|جوش/, "🛠️"],
      [/پوشاک|لباس|کیف|کفش|boutique/, "👗"], [/گل|گیاه/, "🌸"], [/آموزش|مدرسه|کنکور|زبان/, "🎓"],
      [/حقوق|وکیل|مالی|حساب/, "⚖️"], [/خودرو|ماشین|یدک/, "🚗"], [/طلا|جواهر/, "💍"],
      [/سوپر|هایپر|بقالی|محله/, "🛒"], [/عکاس|آتلیه/, "📷"], [/hotel|هتل|اقامت/, "🏨"]
    ];
    for (var i = 0; i < map.length; i++) if (map[i][0].test(n)) return map[i][1];
    return "🏪";
  }
  function mapsHref(l) {
    if (l.maps_link) return l.maps_link;
    if (l.lat != null && l.lon != null && l.lat !== "") return "https://maps.google.com/?q=" + l.lat + "," + l.lon;
    return "";
  }

  /* ────────────────────────── نما: شکار ────────────────────────── */
  function renderGoals() {
    var goals = (S.options ? S.options.goals : FALLBACK_OPTIONS.goals);
    $("goalChips").innerHTML = goals.map(function (g) {
      return '<button class="chip" data-goal="' + esc(g.id) + '" data-on="' + (S.goal === g.id ? 1 : 0) + '" title="' + esc(g.hint || "") + '">' +
        esc(g.icon || "") + " " + esc(g.fa) + "</button>";
    }).join("");
  }
  function renderSizes() {
    document.querySelectorAll("#sizeSeg .seg-btn").forEach(function (b) {
      b.classList.toggle("is-on", b.dataset.size === S.size);
    });
  }
  function applySize(id) {
    S.size = id;
    var sizes = (S.options ? S.options.sizes : FALLBACK_OPTIONS.sizes);
    var s = sizes.filter(function (x) { return x.id === id; })[0] || FALLBACK_OPTIONS.sizes[1];
    $("depth").value = s.depth; $("grid").value = s.grid; $("limit").value = s.limit;
    renderSizes();
    N.buzz(6);
  }
  function renderParseNote() {
    var el = $("parseNote");
    if (!S.parse || !$("q").value.trim()) { el.hidden = true; return; }
    var d = S.parse.detected || {};
    var bits = [];
    if (d.all_trades) bits.push("<b>همهٔ کسب‌وکارها</b>");
    else if (d.niche_fa) bits.push("<b>" + esc(d.niche_fa) + "</b>");
    if (d.city) bits.push("📍 " + esc(d.city));
    if (S.parse.eta_minutes && S.parse.eta_minutes[1]) bits.push("⏳ حدود " + fa(S.parse.eta_minutes[0]) + " تا " + fa(S.parse.eta_minutes[1]) + " دقیقه");
    else if (S.mode === "demo") bits.push("⏡ چند ثانیه");
    el.innerHTML = "فهمیدم: " + bits.join(" · ");
    el.hidden = false;
    if (d.city) { S.area.label = d.city; $("areaLabel").textContent = d.city; }
    if (d.all_trades) { S.niche = { label: "همهٔ کسب‌وکارها", query: "" }; $("nicheLabel").textContent = "همهٔ کسب‌وکارها"; }
    else if (d.niche_fa) { S.niche = { label: d.niche_fa, query: d.niche_fa }; $("nicheLabel").textContent = d.niche_fa; }
  }
  function renderSuggest(s) {
    var box = $("suggest");
    if (!s || (!s.niches.length && !s.cities.length && !s.groups.length)) { box.hidden = true; box.innerHTML = ""; S.sugHi = -1; return; }
    var html = "";
    s.niches.forEach(function (n) { html += '<button data-fill="' + esc(n.fa) + '"><span>🏪</span><span>' + esc(n.fa) + "</span><small>" + esc(n.group || "") + "</small></button>"; });
    s.cities.forEach(function (c) { html += '<button data-fill="' + esc(c.fa || c) + '"><span>📍</span><span>' + esc(c.fa || c) + "</span><small>شهر</small></button>"; });
    s.groups.forEach(function (g) { html += '<button data-fill="' + esc(g.fa || g) + '"><span>🗂</span><span>' + esc(g.fa || g) + "</span><small>گروه صنف</small></button>"; });
    box.innerHTML = html;
    box.hidden = false;
    S.sugHi = -1;
  }

  /* ────────────────────────── نما: لیدها ────────────────────────── */
  function normLead(l) {
    /* سرور زنده و دادهٔ نمونه کمی نام فیلدها را فرق می‌گذارند؛ اینجا یکی می‌کنیم. */
    if (!l.email && l.email_primary) l.email = l.email_primary;
    if (!l.email && l.emails) l.email = String(l.emails).split(/[;,]/)[0];
    if (!l.factors && l.score_factors) l.factors = l.score_factors;
    if (!l.niche_fa && l.category) l.niche_fa = l.category;
    return l;
  }
  function leadCard(l, listView) {
    var acts = [];
    if (l.phone) acts.push('<a class="act act-primary" href="tel:' + esc(l.phone) + '" data-stop>📞 زنگ بزن</a>');
    if (l.email) acts.push('<a class="act" href="mailto:' + esc(l.email) + '" data-stop>✉️ ایمیل</a>');
    if (l.website) acts.push('<a class="act" href="' + esc(/^https?:/.test(l.website) ? l.website : "https://" + l.website) + '" target="_blank" rel="noopener" data-stop>🌐 سایت</a>');
    if (l.instagram) acts.push('<a class="act" href="' + esc(l.instagram) + '" target="_blank" rel="noopener" data-stop>📸</a>');
    var mh = mapsHref(l);
    if (mh) acts.push('<a class="act" href="' + esc(mh) + '" target="_blank" rel="noopener" data-stop>🗺 نقشه</a>');
    acts.push('<button class="act" data-act="copy" data-val="' + esc(l.phone || l.email || l.title) + '" data-stop>📋 کپی</button>');
    var segTag = l.segment === "hot" ? '<span class="tag tag-hot">🔥 داغ</span>' : l.segment === "warm" ? '<span class="tag tag-warm">گرم</span>' : "";
    return '<article class="lead" data-act="lead-open" data-id="' + esc(l.id || l.place_id) + '" tabindex="0" role="button" aria-label="' + esc(l.title) + '">' +
      '<div class="lead-head"><span class="lead-emoji">' + emojiFor(l) + "</span>" +
      '<span class="lead-title"><b>' + esc(l.title) + "</b><small>" + esc(l.niche_fa || l.category || "") + " · " + esc(l.city || "") + "</small></span>" +
      N.ringHtml(l.score) + "</div>" +
      '<div class="lead-meta">' + segTag +
      '<span class="tag">' + esc(l.status_fa || STATUS_FA[l.status] || "جدید") + "</span>" +
      (l.rating ? '<span class="tag">⭐ ' + num(l.rating, 1) + " (" + fa(l.reviews || 0) + ")</span>" : "") +
      (l.phone ? '<span class="tag tag-ok">📞 دارد</span>' : "") +
      (l.email ? '<span class="tag tag-ok">✉️ دارد</span>' : "") +
      (l.instagram ? '<span class="tag tag-ok">📸 دارد</span>' : "") + "</div>" +
      (listView ? "" : '<div class="lead-acts">' + acts.join("") + "</div>") +
      "</article>";
  }
  function renderLeadChips() {
    var seg = $("segChips");
    var facets = S.facets && S.facets.segment ? S.facets.segment : null;
    var keys = facets ? Object.keys(facets) : ["hot", "warm", "cold"];
    var html = '<button class="chip" data-seg="" data-on="' + (!S.filters.segment ? 1 : 0) + '">همه</button>';
    keys.forEach(function (k) {
      if (!SEG_FA[k]) return;
      html += '<button class="chip" data-seg="' + esc(k) + '" data-on="' + (S.filters.segment === k ? 1 : 0) + '">' + SEG_FA[k] +
        (facets && facets[k] != null ? " (" + fa(facets[k]) + ")" : "") + "</button>";
    });
    seg.innerHTML = html;
    var fc = $("filterChips");
    fc.innerHTML = Object.keys(HAS_FA).map(function (k) {
      return '<button class="chip" data-has="' + k + '" data-on="' + (S.filters.has[k] ? 1 : 0) + '">' + HAS_FA[k] + "</button>";
    }).join("") + '<button class="chip" data-status="new" data-on="' + (S.filters.status === "new" ? 1 : 0) + '">🆕 فقط جدید</button>';
  }
  function loadLeads(append) {
    var list = $("leadList");
    if (!append) { S.offset = 0; list.innerHTML = N.skeletonCards(4); }
    var p = {
      q: S.filters.q, segment: S.filters.segment, status: S.filters.status, run_id: S.filters.run_id,
      sort: S.sort, limit: PAGE, offset: S.offset
    };
    Object.keys(S.filters.has).forEach(function (k) { if (S.filters.has[k]) p[k] = 1; });
    API.leads(p).then(function (j) {
      var leads = j.leads || [];
      S.total = j.total != null ? j.total : leads.length;
      S.facets = j.facets || null;
      if (!append) list.innerHTML = "";
      if (!leads.length && !append) {
        list.innerHTML = '<div class="empty"><span class="ico">🕵️</span><b>چیزی پیدا نشد</b>' +
          "<span>فیلترها رو کمی شل‌تر کن، یا از تب «شکار» یه جست‌وجوی جدید بزن.</span></div>";
      } else {
        list.insertAdjacentHTML("beforeend", leads.map(function (l) { return leadCard(normLead(l), S.view === "list"); }).join(""));
      }
      S.offset += leads.length;
      $("leadCount").textContent = (S.filters.run_id ? "لیدهای این کار: " : "") + fa(S.total) + " لید پیدا شده" +
        (S.total > S.offset ? " · " + fa(S.offset) + " تا نشون داده شده" : "");
      $("leadMore").hidden = S.offset >= S.total;
      renderLeadChips();
    }, function (e) {
      list.innerHTML = '<div class="empty"><span class="ico">⚠️</span><b>نشده که بگیرم</b><span>' + esc(e.message) + "</span></div>";
    });
  }

  /* ────────────────────────── شیت لید ────────────────────────── */
  function openLead(id) {
    API.lead(id).then(function (l) {
      normLead(l);
      var contacts = "";
      function row(icon, label, val, href) {
        return '<div class="contact-row"><span>' + icon + "</span><span>" + esc(val) + "</span>" +
          (href ? '<a class="act" href="' + esc(href) + '" target="_blank" rel="noopener">باز کن</a>' : "") +
          '<button class="act" data-act="copy" data-val="' + esc(val) + '">کپی</button></div>';
      }
      if (l.phone) contacts += row("📞", "تلفن", l.phone, "tel:" + l.phone);
      if (l.email) contacts += row("✉️", "ایمیل", l.email, "mailto:" + l.email);
      if (l.website) contacts += row("🌐", "سایت", l.website, /^https?:/.test(l.website) ? l.website : "https://" + l.website);
      if (l.instagram) contacts += row("📸", "اینستاگرام", l.instagram, l.instagram);
      if (l.facebook) contacts += row("📘", "فیسبوک", l.facebook, l.facebook);
      if (l.linkedin) contacts += row("💼", "لینکدین", l.linkedin, l.linkedin);
      var mh = mapsHref(l);
      var factors = (l.factors || []).map(function (f) {
        var w = Math.max(1, f.weight || 10);
        return '<div class="factor"><b>' + esc(f.fa) + "</b> <small>" + num(f.points, 1) + " از " + fa(w) + " — " + esc(f.detail || "") + "</small>" +
          N.barHtml((f.points / w) * 100) + "</div>";
      }).join("");
      var html =
        '<div class="lead-head" style="cursor:default"><span class="lead-emoji">' + emojiFor(l) + "</span>" +
        '<span class="lead-title"><b>' + esc(l.title) + "</b><small>" + esc(l.niche_fa || l.category || "") + " · " + esc(l.city || "") +
        (l.address ? " · " + esc(l.address) : "") + "</small></span>" + N.ringHtml(l.score) + "</div>" +
        '<div class="lead-meta">' +
        (l.segment === "hot" ? '<span class="tag tag-hot">🔥 داغ — اول این بزنگ</span>' : l.segment === "warm" ? '<span class="tag tag-warm">گرم</span>' : '<span class="tag">سرد</span>') +
        (l.rating ? '<span class="tag">⭐ ' + num(l.rating, 1) + " با " + fa(l.reviews || 0) + " نظر</span>" : "") +
        '<span class="tag">کامل بودن اطلاعات: ' + pct(l.completeness) + "</span></div>" +
        (contacts ? '<div class="sheet-section"><b>راه‌های تماس</b><div class="contact-list">' + contacts + "</div></div>" : "") +
        (mh ? '<a class="btn btn-ghost" href="' + esc(mh) + '" target="_blank" rel="noopener">🗺 جای دقیقش روی نقشه (' + num(l.lat != null ? l.lat : l.latitude, 4) + "، " + num(l.lon != null ? l.lon : l.longitude, 4) + ")</a>" : "") +
        (l.pitch ? '<div class="sheet-section"><b>چی بگی که گوش کنه؟</b><div class="glass" style="padding:12px 14px;font-size:13.5px">' + esc(l.pitch) +
          '</div><button class="btn btn-ghost btn-sm" data-act="copy" data-val="' + esc(l.pitch) + '">📋 کپی متن پیشنهادی</button></div>' : "") +
        (factors ? '<div class="sheet-section"><b>چرا این امتیاز؟</b><div class="factors">' + factors + "</div></div>" : "") +
        ((l.gaps_fa || []).length ? '<div class="sheet-section"><b>جاهای خالی‌ای که می‌تونی پر کنی</b><div class="chips">' +
          (l.gaps_fa || []).map(function (g) { return '<span class="tag">' + esc(typeof g === "string" ? g : g.fa) + "</span>"; }).join("") + "</div></div>" : "") +
        '<div class="sheet-section"><b>وضعیتش تو دفترچهٔ تو</b><div class="status-seg" id="statusSeg">' +
        Object.keys(STATUS_FA).map(function (k) {
          return '<button class="chip" data-act="status" data-id="' + esc(l.id || l.place_id) + '" data-status="' + k + '" data-on="' + ((l.status || "new") === k ? 1 : 0) + '">' + STATUS_FA[k] + "</button>";
        }).join("") + "</div></div>";
      N.sheet(l.title, html);
      N.buzz(10);
    }, function (e) { N.toast(e.message, "err"); });
  }

  /* ────────────────────────── نما: کارها ────────────────────────── */
  function loadRuns() {
    API.queue().then(function (q) {
      var st = q.stats || {};
      $("queueStats").innerHTML =
        '<span class="chip">⏳ در صف: ' + fa(st.pending || 0) + "</span>" +
        '<span class="chip">🏃 در حال اجرا: ' + fa(st.running || 0) + "</span>" +
        '<span class="chip">✅ انجام‌شده: ' + fa(st.done || 0) + "</span>" +
        '<span class="chip">⚠️ خطا: ' + fa(st.failed || 0) + "</span>";
      $("queueNote").textContent = q.active === false
        ? "صف غیرفعاله — کارها همون لحظه اجرا می‌شن."
        : (st.oldest_wait_s ? "قدیمی‌ترین کار " + fa(Math.round(st.oldest_wait_s / 60)) + " دقیقه‌ست منتظره." : "صف خلوته؛ هر کاری بزنی زود شروع می‌شه.");
    }, function () { $("queueNote").textContent = ""; });
    API.runs().then(function (j) {
      var runs = j.runs || [];
      var box = $("runList");
      if (!runs.length) {
        box.innerHTML = '<div class="empty"><span class="ico">🗂</span><b>هنوز کاری نزدی</b><span>از تب «شکار» اولین جست‌وجو رو بزن.</span></div>';
        return;
      }
      box.innerHTML = runs.map(function (r) {
        var active = r.status === "queued" || r.status === "running" || r.status === "pending";
        return '<article class="run" data-act="run-open" data-id="' + esc(r.id) + '" tabindex="0" role="button">' +
          '<div class="run-head"><span>' + (active ? "🏃" : r.status === "failed" ? "⚠️" : "✅") + "</span><b>" +
          esc(r.niche_fa || "همهٔ کسب‌وکارها") + " · " + esc(r.city || "") + "</b>" +
          "<time>" + esc(ago(typeof r.created_at === "number" ? new Date(r.created_at * 1000).toISOString() : r.created_at)) + "</time></div>" +
          '<div class="run-stats"><span class="tag">' + fa(r.total || 0) + " لید</span>" +
          '<span class="tag tag-hot">' + fa(r.hot || 0) + " داغ</span>" +
          '<span class="tag">' + esc(r.engine === "demo" ? "نمایشی" : r.live ? "واقعی" : "موتور: " + (r.engine || "—")) + "</span>" +
          (active ? '<span class="tag tag-warm">در حال اجرا…</span>' : "") + "</div></article>";
      }).join("");
    }, function (e) { $("runList").innerHTML = '<div class="empty"><span class="ico">⚠️</span><b>' + esc(e.message) + "</b></div>"; });
  }
  function openRun(id) {
    API.progress(id).then(function (j) {
      var p = j.progress || {};
      var html = '<div class="sheet-section"><b>پیشرفت</b>' + N.barHtml(p.percent || 0) +
        '<p class="muted" style="margin:0">' + esc(p.message || "—") + "</p></div>" +
        '<div class="chips"><button class="chip" data-act="run-leads" data-id="' + esc(id) + '">📇 لیدهاش رو ببین</button>' +
        '<button class="chip" data-act="run-export" data-id="' + esc(id) + '" data-fmt="xlsx">⬇️ اکسل</button>' +
        '<button class="chip" data-act="run-export" data-id="' + esc(id) + '" data-fmt="csv">⬇️ CSV</button>' +
        '<button class="chip" data-act="run-del" data-id="' + esc(id) + '">🗑 حذف کار</button></div>';
      N.sheet("جزئیات کار", html);
    }, function (e) { N.toast(e.message, "err"); });
  }

  /* ────────────────────────── شیت منطقه (آبشاری) ────────────────────────── */
  function openAreaSheet() {
    var st = { province: S.area.province, city: S.area.city, district: S.area.district, street: S.area.street };
    function crumb() {
      var parts = [];
      if (st.province) parts.push('<button data-act="area-lvl" data-lvl="province">استان</button>');
      if (st.city) parts.push("› <button data-act=\"area-lvl\" data-lvl=\"city\">شهر</button>");
      if (st.district) parts.push("› <button data-act=\"area-lvl\" data-lvl=\"district\">منطقه</button>");
      if (st.street) parts.push("› <span>خیابان</span>");
      return '<div class="breadcrumb">' + (parts.join(" ") || "از استان شروع کن") + "</div>";
    }
    function paint(body) {
      var html = '<div class="cascade">' + crumb() +
        '<input class="input" id="areaSearch" type="search" placeholder="یا مستقیم بنویس: ونک، خیابان انقلاب، شیراز…">' +
        '<div class="stack-list" id="areaStack"></div>' +
        '<button class="btn btn-primary" data-act="area-done">همین‌جا خوبه ✔</button></div>';
      body.innerHTML = html;
      var stack = body.querySelector("#areaStack");
      function items(list, kind) {
        stack.innerHTML = list.map(function (x) {
          return '<button class="stack-item" data-act="area-pick" data-kind="' + kind + '" data-id="' + esc(x.id != null ? x.id : x.fa) + '" data-fa="' + esc(x.fa) + '">' +
            "<span>" + esc(x.fa) + "</span>" + (x.note ? "<small>" + esc(x.note) + "</small>"
              : x.districts != null ? "<small>" + fa(x.districts) + " منطقه</small>" : "") + "</button>";
        }).join("") || '<p class="muted">چیزی نیست.</p>';
      }
      function loadLevel() {
        if (!st.city) {
          return jget("api/regions").then(function (h) {
            var provs = h.provinces || [];
            if (!st.province) {               // گام اول: همهٔ ۳۱ استان
              items(provs.map(function (p) {
                return { id: p.id, fa: p.fa, note: fa((p.cities || []).length) + " شهر" };
              }), "province");
              return;
            }
            var row = provs.filter(function (p) { return p.id === st.province; })[0];
            items(((row && row.cities) || []).map(function (c) {
              return { id: st.province + "|" + c.id, fa: c.fa, city: c.id, province: st.province, districts: c.districts };
            }), "city");
          });
        }
        if (!st.district) {
          return jget("api/regions/districts?city=" + encodeURIComponent(st.city)).then(function (d) { items(d.districts || [], "district"); });
        }
        return jget("api/regions/streets?city=" + encodeURIComponent(st.city) + "&district=" + encodeURIComponent(st.district)).then(function (s2) { items(s2.streets || [], "street"); });
      }
      if (S.mode === "demo") {
        stack.innerHTML = '<p class="muted">در حالت نمایشی، منطقه رو می‌تونی همون‌جا توی جمله بنویسی ' +
          "(مثلاً «ونک تهران»)؛ من می‌فهممش. یا یه شهر پایه انتخاب کن:</p>" +
          [["tehran", "تهران"], ["mashhad", "مشهد"], ["isfahan", "اصفهان"], ["shiraz", "شیراز"],
           ["tabriz", "تبریز"], ["karaj", "کرج"]].map(function (c) {
            return '<button class="stack-item' + (st.city === c[0] ? " is-on" : "") + '" data-act="area-pick" data-kind="city-fa" data-id="' + c[0] + '" data-fa="' + c[1] + '"><span>' + c[1] + "</span></button>";
          }).join("");
      } else {
        loadLevel().catch(function (e) { stack.innerHTML = '<p class="muted">' + esc(e.message) + "</p>"; });
      }
      var search = body.querySelector("#areaSearch");
      var t = null;
      search.addEventListener("input", function () {
        clearTimeout(t);
        t = setTimeout(function () {
          var q = search.value.trim();
          if (q.length < 2) { loadLevel().catch(function () { }); return; }
          jget("api/regions/search?q=" + encodeURIComponent(q)).then(function (r) {
            var list = (r || []).map ? (r || []) : (r.cities || []).concat(r.districts || [], r.streets || []);
            items(list.map(function (x) {
              var title = x.kind === "city"
                ? (x.fa || x.label) + (x.province_fa ? " — " + x.province_fa : "")
                : (x.label || x.fa);
              return { id: x.kind + "|" + (x.province || "") + "|" + (x.city || "") + "|" + (x.name || ""), fa: title, kind: x.kind, raw: x };
            }), "any");
            stack.querySelectorAll("[data-kind='any']").forEach(function (b, i) { b._raw = list[i]; });
          }).catch(function () { });
        }, 250);
      });
      body.querySelector("[data-act='area-pick']");
      body.onclick = function (ev) {
        var pick = ev.target.closest("[data-act='area-pick']");
        if (pick) {
          var kind = pick.dataset.kind, raw = pick._raw;
          if (kind === "any" && raw) {
            st.province = raw.province || st.province;
            st.city = raw.kind === "city" ? (raw.city || raw.id || st.city) : (raw.city || st.city);
            if (raw.kind === "district") st.district = raw.name; if (raw.kind === "street") { st.district = raw.district || st.district; st.street = raw.name; }
            if (raw.kind === "city") { st.district = ""; st.street = ""; }
          } else if (kind === "province") {
            st.province = pick.dataset.id; st.city = ""; st.district = ""; st.street = "";
          } else if (kind === "city") {
            var ids = String(pick.dataset.id).split("|");
            st.province = ids[0]; st.city = ids[1] || ids[0]; st.district = ""; st.street = "";
          } else if (kind === "district") { st.district = pick.dataset.fa; st.street = ""; }
          else if (kind === "street") { st.street = pick.dataset.fa; }
          else if (kind === "city-fa") { st.city = pick.dataset.id; st.cityFa = pick.dataset.fa; st.district = ""; st.street = ""; }
          paint(body);
          return;
        }
        var lvl = ev.target.closest("[data-act='area-lvl']");
        if (lvl) {
          if (lvl.dataset.lvl === "province") { st.province = ""; st.city = ""; st.district = ""; st.street = ""; }
          if (lvl.dataset.lvl === "city") { st.district = ""; st.street = ""; }
          if (lvl.dataset.lvl === "district") { st.street = ""; }
          paint(body);
          return;
        }
        if (ev.target.closest("[data-act='area-done']")) {
          resolveArea(st, body);
        }
      };
    }
    N.sheet("کجا بگردم؟", "", paint);
  }
  function resolveArea(st, body) {
    function finish(label) {
      S.area = { label: label || st.city || "تهران", province: st.province, city: st.city, district: st.district, street: st.street };
      $("areaLabel").textContent = S.area.label;
      N.sheetHide();
      N.toast("باشه! می‌گردم توی: " + S.area.label, "ok");
    }
    if (S.mode === "demo") { finish(st.street || st.district || st.cityFa || S.area.label); return; }
    jget("api/regions/resolve?province=" + encodeURIComponent(st.province || "") + "&city=" + encodeURIComponent(st.city || "") +
      "&district=" + encodeURIComponent(st.district || "") + "&street=" + encodeURIComponent(st.street || ""))
      .then(function (r) { finish(r.keyword_label || r.label); }, function () { finish(st.city); });
  }

  /* ────────────────────────── شیت صنف ────────────────────────── */
  function openNicheSheet() {
    function paint(body) {
      body.innerHTML = '<input class="input" id="nicheSearch" type="search" placeholder="اسم صنف رو بنویس… (مثلاً: قفل‌ساز)">' +
        '<button class="stack-item" data-act="niche-pick" data-fa="همهٔ کسب‌وکارها" data-id=""><span>🌐 همهٔ کسب‌وکارها</span><small>کل شهر</small></button>' +
        '<div class="stack-list" id="nicheStack"><p class="muted">دارم فهرست رو می‌گیرم…</p></div>';
      var stack = body.querySelector("#nicheStack");
      function show(list) {
        var byGroup = {};
        list.forEach(function (n) { (byGroup[n.group || "سایر"] = byGroup[n.group || "سایر"] || []).push(n); });
        stack.innerHTML = Object.keys(byGroup).map(function (g) {
          return '<div class="sheet-section"><b>' + esc(g) + "</b>" + byGroup[g].map(function (n) {
            return '<button class="stack-item' + (S.niche.query === n.fa ? " is-on" : "") + '" data-act="niche-pick" data-id="' + esc(n.id) + '" data-fa="' + esc(n.fa) + '"><span>' + esc(n.fa) + "</span></button>";
          }).join("") + "</div>";
        }).join("") || '<p class="muted">صنفی با این اسم نیست؛ ولی می‌تونی همون رو توی جملهٔ بالا بنویسی، خودم می‌فهمم.</p>';
      }
      API.options().then(function (o) {
        var all = [];
        if (S.mode === "demo") { demoCatalog().forEach(function (n) { all.push(n); }); }
        else {
          return jget("api/niches").then(function (j) { show(j.niches || j || []); });
        }
        show(all);
      }).catch(function (e) { stack.innerHTML = '<p class="muted">' + esc(e.message) + "</p>"; });
      var t = null;
      body.querySelector("#nicheSearch").addEventListener("input", function (ev) {
        clearTimeout(t);
        var q = ev.target.value.trim();
        t = setTimeout(function () {
          if (!q) { API.options().then(function () { }); return; }
          API.suggest(q).then(function (s) { show(s.niches || []); });
        }, 220);
      });
      body.onclick = function (ev) {
        var pick = ev.target.closest("[data-act='niche-pick']");
        if (!pick) return;
        S.niche = { label: pick.dataset.fa, query: pick.dataset.fa === "همهٔ کسب‌وکارها" ? "" : pick.dataset.fa };
        $("nicheLabel").textContent = S.niche.label;
        N.sheetHide();
        N.toast("باشه: " + S.niche.label, "ok");
      };
    }
    N.sheet("چی‌کاره‌ها؟", "", paint);
  }

  /* ────────────────────────── اجرای کار + پولینگ ────────────────────────── */
  function buildRequest() {
    var base = (S.parse && S.parse.request) ? JSON.parse(JSON.stringify(S.parse.request)) : {};
    base.niche_query = S.niche.query || base.niche_query || "همهٔ کسب‌وکارها";
    base.city_query = S.area.label || base.city_query || "تهران";
    if (S.area.province) base.province = S.area.province;
    if (S.area.city) base.city_key = S.area.city;
    if (S.area.district) base.district = S.area.district;
    if (S.area.street) base.street = S.area.street;
    base.depth = +$("depth").value || 4;
    base.grid = +$("grid").value || 0;
    base.limit = +$("limit").value || 0;
    base.socials = $("socials").checked;
    base.emails = $("emails").checked;
    base.profile = goalProfile();
    base.engine_mode = $("demoMode").checked ? "demo" : ($("engineMode").value || "auto");
    return base;
  }
  function startRun() {
    var btn = $("goBtn");
    btn.classList.add("is-busy"); btn.disabled = true;
    var q = $("q").value.trim();
    var go = function () {
      var req = buildRequest();
      return API.start(req).then(function (r) {
        S.run = { run_id: r.run_id, title: (req.niche_query || "") + " · " + (req.city_query || "") };
        N.toast(S.mode === "demo" ? "اجرای نمایشی شروع شد 🎬" : (r.status === "queued" ? "رفت توی صف؛ خودم بیدارش می‌کنم ⏳" : "شروع شد! 🚀"), "ok");
        showLive(true);
        poll();
      }, function (e) {
        N.toast("نشد که شروع کنم: " + e.message, "err");
      }).then(function () { btn.classList.remove("is-busy"); btn.disabled = false; });
    };
    if (q && (!S.parse || S.parse.query !== q)) {
      API.parse(q).then(function (p) { S.parse = p; renderParseNote(); return go(); }, function () { return go(); });
    } else go();
  }
  function showLive(on) {
    $("huntLive").hidden = !on;
    $("dock").hidden = !on;
  }
  function poll() {
    if (S.pollTimer) clearInterval(S.pollTimer);
    S.pollTimer = setInterval(function () {
      if (!S.run) { clearInterval(S.pollTimer); S.pollTimer = null; return; }
      API.progress(S.run.run_id).then(function (j) {
        var p = j.progress || {};
        paintProgress(p);
        maybeTick(p);
        if (p.stage === "done" || p.status === "done") {
          clearInterval(S.pollTimer); S.pollTimer = null;
          var tot = p.stats ? (p.stats.total || 0) : null;
          if (tot === 0) {
            N.toast("توی این محدوده چیزی نبود — محدودهٔ بزرگ‌تری رو امتحان کن یا صنف رو عوض کن.", "warn", 6500);
          } else {
            N.toast("تمام شد! " + (p.stats ? fa(tot) + " لید (" + fa(p.stats.hot || 0) + " داغ)" : ""), "ok", 5200);
          }
          S.run = null;
          setTimeout(function () { showLive(false); }, 2600);
          loadRuns();
          if (!$("view-leads").hidden) loadLeads(false);
        } else if (p.stage === "failed" || p.status === "failed") {
          clearInterval(S.pollTimer); S.pollTimer = null;
          N.toast("اُوف، به خطا خورد: " + (p.message || ""), "err", 6000);
          S.run = null; showLive(false);
        }
      }, function () { /* شاید هنوز آماده نیست؛ دور بعد */ });
    }, 2500);
  }
  function paintProgress(p) {
    var pctv = Math.max(0, Math.min(100, p.percent || 0));
    $("huntLiveFill").style.width = pctv + "%";
    $("huntLiveStage").textContent = p.stage === "queued" ? "توی صف" : p.stage === "done" ? "تمام" : "در حال کار";
    $("huntLiveTitle").textContent = S.run ? S.run.title : "";
    $("huntLiveText").textContent = p.message || "…";
    $("dockTitle").textContent = S.run ? S.run.title : "";
    $("dockText").textContent = p.message || "…";
    var off = N.RING_C * (1 - pctv / 100);
    $("dockFill").setAttribute("stroke-dasharray", N.RING_C.toFixed(1));
    $("dockFill").setAttribute("stroke-dashoffset", off.toFixed(1));
  }

  /* ────────────────────────── سلامت / بیشتر ────────────────────────── */
  function renderHealth() {
    var h = S.health || {};
    var eng = h.engine || {};
    var db = h.db || {};
    var cells = [
      ["موتور", ENGINE_FA[eng.active] || "در دسترس نیست"],
      ["پایگاه داده", (h.config && h.config.db_backend) === "supabase" ? "☁️ ابری (Supabase)" : (h.config && h.config.db_backend) === "demo" ? "🎬 داخل برنامه" : "💾 محلی"],
      ["صف کارها", h.queue_mode === "always" ? "همیشه فعال" : h.queue_mode === "never" ? "خاموش (اجرای فوری)" : "خودکار"],
      ["لیدهای ذخیره‌شده", fa(db.leads != null ? db.leads : 0) + " لید · " + fa(db.hot != null ? db.hot : 0) + " داغ"],
      ["ایمیل", h.email_transport === "gmail_api" ? "جیمیل وصله ✉️" : h.email_transport === "smtp" ? "SMTP" : "تنظیم نشده"],
      ["نسخه", h.version || "نُوا ۱٫۰"]
    ];
    $("healthCard").innerHTML = cells.map(function (c) {
      return '<div class="hcell"><small>' + esc(c[0]) + "</small><b>" + esc(c[1]) + "</b></div>";
    }).join("");
    $("moreNote").textContent = eng.reason || "";
    var attr = $("attribution");
    if (attr) {
      attr.innerHTML = eng.active === "osm"
        ? 'دادهٔ این نسخه از OpenStreetMap می‌آید — © مشارکت‌کنندگان OpenStreetMap (ODbL).'
        : eng.active === "places" ? "دادهٔ این نسخه از Google Places API می‌آید."
        : eng.active === "docker" ? "دادهٔ این نسخه از موتور گوگل‌مپ (اسکرپر) می‌آید."
        : "این نسخه روی دادهٔ نمونه کار می‌کند تا موتور واقعی وصل شود.";
    }
    paintCoverage();
    $("verTxt").textContent = "نُوا — رابط نسل بعدِ شکار مشتری · ساخته‌شده روی موتور گوگل‌مپ";
  }
  /* پوشش تگ‌محور: چند صنف و چند فیلتر OSM در دسترس است + وضعیت Overpass. */
  function paintCoverage() {
    var box = $("coverageTxt");
    if (!box) return;
    var h = S.health || {};
    var tags = h.tag_map || {};
    var src = h.sources || {};
    var ov = src.overpass || {};
    var parts = [];
    if (tags.niches) parts.push(fa(tags.niches) + " صنف با تگ اختصاصی");
    if (tags.filters) parts.push(fa(tags.filters) + " فیلتر نقشه");
    if (tags.all_business) parts.push("جاروی همهٔ کسب‌وکارها");
    var line = parts.length ? "پوشش: " + parts.join(" · ") : "پوشش: نامعلوم";
    if (ov.mode) {
      line += " · " + (ov.up === false ? "نقشهٔ Overpass فعلاً جواب نمی‌دهد؛ مسیر جایگزین کار می‌کند" :
        ov.up === true ? "Overpass آماده است" :
        ov.mode === "overpass" ? "فقط مسیر Overpass" : "حالت " + ov.mode);
    }
    if (h.engine && !h.engine.live) line += " — الان دادهٔ نمایشی است.";
    box.textContent = line;
  }
  function fmtProbe(d) {
    if (!d || d.ok === false) {
      return '<p class="muted">پاسخی نیامد: ' + esc((d && (d.error || d.message)) || "خطای نامشخص") + "</p>";
    }
    var chips = [
      fa(d.rows || 0) + " کسب‌وکار",
      "⏱ " + fa(d.seconds || 0) + " ثانیه",
      "📞 " + fa(d.with_phone || 0),
      "🌐 " + fa(d.with_website || 0)
    ];
    var head = '<div class="probe-h">' + chips.map(function (c) {
      return '<span class="chip">' + esc(c) + "</span>";
    }).join("") + "</div>";
    var note = d.demo ? '<p class="muted">حالت نمایشی — این عددها نمونهٔ داخل برنامه‌اند، نه نقشهٔ واقعی.</p>' : "";
    var rows = (d.sample || []).map(function (r) {
      var name = esc(r.title || "—");
      if (r.link) name = '<a href="' + esc(r.link) + '" target="_blank" rel="noopener">' + name + "</a>";
      return "<li>" + name + ' <small class="muted">' + esc(r.category || "—") + "</small></li>";
    }).join("");
    return note + head + (rows ? "<ol>" + rows + "</ol>" : '<p class="muted">در این محدوده چیزی پیدا نشد.</p>');
  }
  function runProbe() {
    var btn = $("probeBtn"), out = $("probeOut");
    if (!btn || !out) return;
    var city = (S.area && (S.area.label || S.area.city)) || "تهران";
    var all = !S.niche || !S.niche.query;
    var niche = all ? "" : (S.niche.label || S.niche.query);
    btn.disabled = true;
    btn.textContent = "⏳ دارم از نقشه می‌پرسم…";
    out.hidden = false;
    out.innerHTML = '<p class="muted">یک درخواست واقعی به Overpass می‌رود؛ چند ثانیه صبر کن.</p>';
    API.diag(city, niche, all).then(function (d) {
      out.innerHTML = fmtProbe(d);
      N.toast(d && d.ok && !d.demo ? "زنده جواب داد ✅" : "پاسخ آمد", d && d.ok ? "ok" : "warn");
    }, function (err) {
      out.innerHTML = '<p class="muted">' + esc(err.message || "خطا") + "</p>";
      N.toast("سنجش زنده نشد", "warn");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "🛰 یه پرسش زنده از نقشه";
    });
  }

  function paintChips() {
    var h = S.health || {};
    var eng = h.engine || {};
    var chip = $("engineChip");
    chip.textContent = ENGINE_CHIP[eng.active] || "بدون موتور";
    chip.dataset.tone = eng.live ? "ok" : eng.active === "demo" ? "warn" : "err";
    var dbc = $("dbChip");
    if (h.config && h.config.db_backend === "supabase") { dbc.hidden = false; dbc.textContent = "☁️ دیتابیس ابری"; }
    $("railHealthTxt").textContent = (eng.live ? "دادهٔ واقعی · " : eng.active === "demo" ? "روی دادهٔ نمایشی · " : "") +
      fa((h.db || {}).leads != null ? h.db.leads : 0) + " لید داخل پایگاه";
  }

  /* ────────────────────────── خروجی گرفتن ────────────────────────── */
  function downloadDemo(fmt) {
    var leads = S.demoProg ? S.demoProg.leads : (S.demo ? S.demo.leads : []);
    var cols = ["title", "niche_fa", "city", "address", "phone", "email", "website", "instagram", "lat", "lon", "score", "segment"];
    var csv = "\ufeff" + cols.join(",") + "\n" + leads.map(function (l) {
      return cols.map(function (c) { return '"' + String(l[c] == null ? "" : l[c]).replace(/"/g, '""') + '"'; }).join(",");
    }).join("\n");
    var blob = new Blob([csv], { type: fmt === "csv" ? "text/csv;charset=utf-8" : "application/vnd.ms-excel" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "nova-leads." + (fmt === "csv" ? "csv" : "xls");
    document.body.appendChild(a); a.click(); a.remove();
    N.toast("فایل دانلود شد ⬇️", "ok");
  }
  function doExport(runId, fmt) {
    if (S.mode === "demo") { downloadDemo(fmt); return; }
    var a = document.createElement("a");
    a.href = API.exportHref(runId, fmt);
    a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
    N.toast("دارم فایل رو می‌سازم… ⬇️", "ok");
  }

  /* ────────────────────────── رویدادها ────────────────────────── */
  function bind() {
    /* ناوبری */
    document.querySelectorAll("[data-tab]").forEach(function (b) {
      b.addEventListener("click", function () { N.buzz(6); N.setTab(b.dataset.tab); closeRail(); });
    });
    N.onTab(function (t) {
      if (t === "leads") loadLeads(false);
      if (t === "runs") loadRuns();
      if (t === "more") renderHealth();
    });

    /* ریل موبایل */
    $("navToggle").addEventListener("click", function () {
      var open = document.body.classList.toggle("rail-open");
      $("navToggle").setAttribute("aria-expanded", open ? "true" : "false");
      $("scrim").hidden = !open;
    });
    function closeRail() { document.body.classList.remove("rail-open"); $("scrim").hidden = true; }
    $("scrim").addEventListener("click", closeRail);

    /* پوسته */
    $("themeBtn").addEventListener("click", function () {
      var cur = document.documentElement.dataset.theme;
      N.applyTheme(cur === "auto" ? "light" : cur === "light" ? "dark" : "auto");
    });
    document.querySelectorAll("[data-theme-val]").forEach(function (b) {
      b.addEventListener("click", function () { N.applyTheme(b.dataset.themeVal); });
    });
    $("hapticSw").addEventListener("change", function () {
      N.setHaptic($("hapticSw").checked);
      try { localStorage.setItem("nova-haptic", $("hapticSw").checked ? "1" : "0"); } catch (e) { /* بی‌خیال */ }
      N.buzz(20);
    });

    /* سازندهٔ جست‌وجو */
    var qt = null;
    $("q").addEventListener("input", function () {
      var v = $("q").value;
      $("qClear").hidden = !v;
      clearTimeout(qt);
      qt = setTimeout(function () {
        if (v.trim().length < 2) { $("suggest").hidden = true; S.parse = null; $("parseNote").hidden = true; return; }
        API.suggest(v.trim()).then(renderSuggest, function () { });
        API.parse(v.trim()).then(function (p) { S.parse = p; renderParseNote(); }, function () { });
      }, 320);
    });
    $("q").addEventListener("keydown", function (ev) {
      var box = $("suggest");
      var btns = box.hidden ? [] : Array.prototype.slice.call(box.querySelectorAll("button"));
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        if (!btns.length) return;
        ev.preventDefault();
        S.sugHi = (S.sugHi + (ev.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length;
        btns.forEach(function (b, i) { b.classList.toggle("is-hi", i === S.sugHi); });
      } else if (ev.key === "Enter") {
        if (S.sugHi >= 0 && btns[S.sugHi]) { fillQ(btns[S.sugHi].dataset.fill); }
        else { box.hidden = true; startRun(); }
      } else if (ev.key === "Escape") box.hidden = true;
    });
    function fillQ(v) {
      var cur = $("q").value.trim();
      $("q").value = cur && cur.indexOf(v) >= 0 ? cur : (cur ? cur + " " + v : v);
      $("suggest").hidden = true;
      $("q").dispatchEvent(new Event("input"));
    }
    $("suggest").addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-fill]");
      if (b) fillQ(b.dataset.fill);
    });
    $("qClear").addEventListener("click", function () {
      $("q").value = ""; $("qClear").hidden = true; $("suggest").hidden = true; $("parseNote").hidden = true; S.parse = null;
      $("q").focus();
    });

    $("sizeSeg").addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-size]");
      if (b) applySize(b.dataset.size);
    });
    $("goalChips").addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-goal]");
      if (!b) return;
      S.goal = b.dataset.goal;
      renderGoals();
      N.buzz(6);
    });
    $("areaBtn").addEventListener("click", openAreaSheet);
    $("nicheBtn").addEventListener("click", openNicheSheet);
    $("goBtn").addEventListener("click", function () { N.buzz(12); startRun(); });
    $("demoMode").addEventListener("change", function () {
      S.mode = $("demoMode").checked ? "demo" : "live";
      boot(false);
    });

    /* لیدها */
    var lt = null;
    $("lq").addEventListener("input", function () {
      clearTimeout(lt);
      lt = setTimeout(function () { S.filters.q = $("lq").value.trim(); loadLeads(false); }, 300);
    });
    $("segChips").addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-seg]");
      if (!b) return;
      S.filters.segment = b.dataset.seg; loadLeads(false);
    });
    $("filterChips").addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-has]");
      if (b) { S.filters.has[b.dataset.has] = !S.filters.has[b.dataset.has]; loadLeads(false); return; }
      var s = ev.target.closest("[data-status]");
      if (s) { S.filters.status = S.filters.status === "new" ? "" : "new"; loadLeads(false); }
    });
    $("sortSel").addEventListener("change", function () { S.sort = $("sortSel").value; loadLeads(false); });
    $("viewToggle").addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-view]");
      if (!b) return;
      S.view = b.dataset.view;
      document.querySelectorAll("#viewToggle .seg-btn").forEach(function (x) { x.classList.toggle("is-on", x === b); });
      $("leadList").classList.toggle("is-list", S.view === "list");
      loadLeads(false);
    });
    $("leadMore").addEventListener("click", function () { loadLeads(true); });

    /* داک */
    $("dockBtn").addEventListener("click", function () { if (S.run) openRun(S.run.run_id); });
    $("dockClose").addEventListener("click", function () { $("dock").hidden = true; });

    /* کلیک سراسری (data-act) */
    document.addEventListener("click", function (ev) {
      var close = ev.target.closest("[data-act='sheet-close']");
      if (close) { N.sheetHide(); return; }
      var stop = ev.target.closest("[data-stop]");
      var el = ev.target.closest("[data-act]");
      if (!el || stop) return;
      var act = el.dataset.act;
      if (act === "lead-open") { openLead(el.dataset.id); return; }
      if (act === "run-open") { openRun(el.dataset.id); return; }
      if (act === "run-leads") { S.filters.run_id = el.dataset.id; N.sheetHide(); N.setTab("leads"); return; }
      if (act === "run-export") { doExport(el.dataset.id, el.dataset.fmt || "xlsx"); return; }
      if (act === "run-del") {
        if (!confirm("این کار و لیدهاش حذف شن؟")) return;
        API.delRun(el.dataset.id).then(function () { N.sheetHide(); N.toast("حذف شد 🗑", "ok"); loadRuns(); }, function (e) { N.toast(e.message, "err"); });
        return;
      }
      if (act === "copy") {
        var v = el.dataset.val || "";
        if (navigator.clipboard) navigator.clipboard.writeText(v).then(function () { N.toast("کپی شد 📋", "ok"); }, function () { N.toast(v); });
        else N.toast(v);
        return;
      }
      if (act === "status") {
        API.setStatus(el.dataset.id, el.dataset.status).then(function () {
          document.querySelectorAll("#statusSeg [data-status]").forEach(function (b) { b.dataset.on = b === el ? "1" : "0"; });
          N.toast("وضعیت عوض شد: " + (STATUS_FA[el.dataset.status] || ""), "ok");
          if (!$("view-leads").hidden) loadLeads(false);
        }, function (e) { N.toast(e.message, "err"); });
        return;
      }
      if (act === "open-run") { if (S.run) openRun(S.run.run_id); return; }
      if (act === "to-leads") { S.filters.run_id = S.run ? S.run.run_id : ""; N.setTab("leads"); return; }
      if (act === "probe") { runProbe(); return; }
      if (act === "engine") { engineSheet(); return; }
      if (act === "db") { engineSheet(); return; }
      if (act === "about") { aboutSheet(); return; }
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { if (N.sheetIsOpen()) N.sheetHide(); closeRailIf(); }
    });
    function closeRailIf() { document.body.classList.remove("rail-open"); $("scrim").hidden = true; }

    /* نصب PWA */
    var deferred = null;
    globalThis.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault(); deferred = e;
      $("installBtn").hidden = false; $("installBtn2").hidden = false;
    });
    function install() { if (deferred) deferred.prompt(); }
    $("installBtn").addEventListener("click", install);
    $("installBtn2").addEventListener("click", install);
  }

  function engineSheet() {
    var h = S.health || {};
    var eng = h.engine || {};
    N.sheet("مغز برنامه", '<div class="sheet-section"><b>موتور جست‌وجو</b>' +
      '<p class="muted" style="margin:0">' + esc(eng.reason || "موتور واقعی = داکرِ گوگل‌مپ؛ اگه بالا نباشه، حالت نمایشی با دادهٔ نمونه کار می‌کنه.") + "</p>" +
      '<div class="chips"><span class="chip">حالت: ' + esc(eng.active || "—") + "</span>" +
      '<span class="chip">پایگاه: ' + esc((h.config || {}).db_backend || "—") + "</span>" +
      ((h.tag_map || {}).niches ? '<span class="chip">پوشش: ' + fa(h.tag_map.niches) + " صنف</span>" : "") + "</div>" +
      '<label class="switch"><input type="checkbox" id="engineSheetDemo" ' + ($("demoMode").checked ? "checked" : "") + "><i></i><span>همین‌جا حالت نمایشی رو روشن/خاموش کن</span></label></div>",
      function (body) {
        body.querySelector("#engineSheetDemo").addEventListener("change", function (ev) {
          $("demoMode").checked = ev.target.checked;
          S.mode = ev.target.checked ? "demo" : "live";
          N.sheetHide();
          boot(false);
        });
      });
  }
  function aboutSheet() {
    N.sheet("نُوا چیه؟",
      '<div class="sheet-section"><p style="margin:0">نُوا یه «مشتری‌یاب»ه: جمله‌ات رو می‌گیره، توی نقشهٔ شهر می‌گرده، ' +
      "کسب‌وکارهای مرتبط رو پیدا می‌کنه و بهت می‌گه کدوم‌هاشون <b>داغ‌ترن</b> — یعنی احتمال بیشتری داره که مشتری تو بشن.</p>" +
      "<p>برای هر کدوم: تلفن، ایمیل، سایت، اینستاگرام و <b>جای دقیق روی نقشه</b> رو می‌ذاره کف دستت؛ بعد می‌تونی وضعیتشون رو یادداشت کنی (تماس گرفتم/مشتری شد/…) و خروجی اکسل بگیری.</p>" +
      "<p class=\"muted\">حالت نمایشی = بدون موتور، با دادهٔ نمونه؛ برای دیدن واقعیِ جریان. برای دادهٔ واقعی، موتور داکر یا استقرار ابری لازمه.</p></div>");
  }

  /* ────────────────────────── راه‌اندازی ────────────────────────── */
  function boot(first) {
    N.loadTheme();
    N.ringDefs();
    if (first) {
      N.bindOnline();
      N.registerSW();
      bind();
      try {
        $("hapticSw").checked = (localStorage.getItem("nova-haptic") || "1") === "1";
        N.setHaptic($("hapticSw").checked);
      } catch (e) { /* بی‌خیال */ }
      var hash = (location.hash || "").replace("#", "");
      if (N.TABS.indexOf(hash) >= 0) N.setTab(hash, { scroll: false });
    }
    renderGoals();
    renderSizes();
    loadDemo().then(function () {
      return API.health().then(function (h) {
        S.health = h;
        if (h.config && h.config.access_code_set && !code()) askCode();
        if ($("demoMode").checked && h.engine && h.engine.live) {
          /* کاربر دمو خواسته؛ همون می‌مونه */ }
        paintChips();
        if (!$("view-more").hidden) renderHealth();
        if (!$("view-more").hidden && !h.tag_map) {
          /* کارت پوشش/سنجش به فهرست تگ‌ها و وضعیت Overpass نیاز دارد */
          API.health(true).then(function (hh) { S.health = hh; renderHealth(); }, function () {});
        }
      }, function () {
        S.mode = "demo";
        $("demoMode").checked = true;
        S.health = demoHealth();
        paintChips();
        N.toast("سرور در دسترس نیست — رفتم روی حالت نمایشی 🎬", "warn");
      });
    }).then(function () {
      return API.options().then(function (o) { S.options = o; renderGoals(); }, function () { S.options = FALLBACK_OPTIONS; renderGoals(); });
    }).then(function () {
      if (first) loadLeads(false);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { boot(true); });
  else boot(true);
})();
