/* لیدژن مینی — رابط جمع‌وجور
   یک جعبهٔ جست‌وجو + استان ← شهر ← منطقه ← خیابان + فیلترهای قوی.

   دو لایهٔ داده:
     Live — سرور محلی (/api/…): تجزیهٔ جمله، اجرای موتور، جست‌وجوی قوی در SQLite، خروجی فایل
     Demo — فایل نمونه (demo-data.json): همان تجربه به‌صورت آفلاین روی GitHub Pages

   هیچ وابستگی بیرونی ندارد؛ همه‌چیز با fetch و DOM خام نوشته شده است. */
(() => {
  "use strict";

  // ── ابزارها ──────────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  const faNum = (value) => String(value == null ? "" : value).replace(/[0-9]/g, (d) => FA_DIGITS[+d]);
  const num = (value, digits = 0) => {
    const n = Number(value);
    if (!isFinite(n)) return "—";
    return faNum(digits ? n.toFixed(digits) : Math.round(n).toLocaleString("en-US"));
  };
  const esc = (value) => String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const debounce = (fn, wait = 250) => {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function toast(message, kind = "") {
    const box = $("toasts");
    if (!box) return;
    const item = document.createElement("div");
    item.className = `toast ${kind}`;
    item.textContent = message;
    box.appendChild(item);
    setTimeout(() => {
      item.style.opacity = "0";
      item.style.transition = "opacity .3s";
      setTimeout(() => item.remove(), 320);
    }, kind === "err" ? 7000 : 4200);
  }

  async function getJSON(url, options) {
    const res = await fetch(url, options);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (err) { data = null; }
    if (!res.ok) {
      const message = (data && (data.error || data.message)) || `خطای ${res.status}`;
      throw new Error(message);
    }
    return data;
  }

  const qs = (params) => {
    const parts = [];
    Object.keys(params || {}).forEach((key) => {
      const value = params[key];
      if (value === null || value === undefined || value === "") return;
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
    });
    return parts.length ? `?${parts.join("&")}` : "";
  };

  // haversine — برای فیلتر «فقط داخل محدودهٔ انتخابی» در حالت آفلاین
  function distanceM(lat1, lon1, lat2, lon2) {
    const toRad = (x) => (x * Math.PI) / 180;
    const R = 6371000;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  // ── ثابت‌های رابط ────────────────────────────────────────────────────────
  const SEGMENTS = [
    { id: "", fa: "همه" },
    { id: "hot", fa: "داغ" },
    { id: "warm", fa: "گرم" },
    { id: "cold", fa: "سرد" },
  ];
  const CONTACTS = [
    { id: "has_phone", fa: "تلفن" },
    { id: "has_email", fa: "ایمیل" },
    { id: "has_website", fa: "وب‌سایت" },
    { id: "has_instagram", fa: "اینستاگرام" },
    { id: "has_social", fa: "شبکه اجتماعی" },
    { id: "no_website", fa: "بدون سایت ⭐" },
  ];
  const SORTS = [
    { id: "score", fa: "امتیاز لید" },
    { id: "rating", fa: "ستاره گوگل" },
    { id: "reviews", fa: "تعداد نظر" },
    { id: "completeness", fa: "کامل بودن اطلاعات" },
    { id: "title", fa: "نام (الفبا)" },
    { id: "created_at", fa: "تازگی" },
  ];
  const STATUSES = [
    { id: "", fa: "همه وضعیت‌ها" },
    { id: "new", fa: "تازه" },
    { id: "contacted", fa: "تماس گرفته" },
    { id: "replied", fa: "پاسخ داده" },
    { id: "qualified", fa: "واجد شرایط" },
    { id: "won", fa: "مشتری شد" },
    { id: "lost", fa: "از دست رفت" },
  ];
  const STATUS_FA = { new: "تازه", contacted: "تماس گرفته", replied: "پاسخ داده", qualified: "واجد شرایط", won: "مشتری شد", lost: "از دست رفت" };
  const SEGMENT_FA = { hot: "داغ", warm: "گرم", cold: "سرد" };
  const GOALS = [
    { id: "site", fa: "🖥️ سایت/فروشگاه می‌فروشم" },
    { id: "ads", fa: "📣 تبلیغات و مارکتینگ" },
    { id: "supply", fa: "📦 تأمین کالا (B2B)" },
    { id: "hire", fa: "🧑‍💼 استخدام" },
    { id: "any", fa: "🎯 فقط فهرست کسب‌وکارها" },
  ];
  const SIZES = [
    { id: "quick", fa: "⚡ سریع (~۱-۳ دقیقه)" },
    { id: "standard", fa: "🎯 استاندارد (~۳-۸ دقیقه)" },
    { id: "deep", fa: "🛰️ کامل (~۱۰-۳۵ دقیقه)" },
  ];
  const PRESETS = [
    { id: "contact", fa: "کارت تماس + لوکیشن" },
    { id: "lead", fa: "لید (پیش‌فرض)" },
    { id: "location", fa: "فقط مکان و تماس" },
    { id: "all", fa: "همه ستون‌ها" },
  ];

  // ── وضعیت برنامه ─────────────────────────────────────────────────────────
  const state = {
    serverOk: false,
    engine: "",
    mode: "stored",              // stored = جست‌وجو در داده‌های موجود | run = نتیجهٔ اجرای تازه
    runId: "",
    parsed: null,
    regions: { provinces: [], countries: [] },
    districts: [],
    streets: [],
    niches: [],
    groups: [],
    sel: { province: "", city: "", district: "", street: "", niche: "", goal: "site", size: "standard", allTrades: false },
    area: null,                  // نتیجهٔ resolve: {label, level, level_fa, lat, lon, radius, zoom}
    filters: {
      q: "", segment: "", status: "", gap: "", niche_id: "", niche_fa: "",
      has_phone: null, has_email: null, has_website: null, has_social: null, has_instagram: null,
      no_website: false, min_score: "", min_rating: "", min_reviews: "",
      sort: "score", order: "desc", geoOnly: false,
    },
    leads: [],
    total: 0,
    facets: {},
    page: 1,
    perPage: 25,
    busy: false,
    poller: null,
    demoLeads: [],
    demoMeta: null,
    theme: "dark",
  };

  // ── لایهٔ زنده (سرور محلی) ───────────────────────────────────────────────
  const Live = {
    async health() {
      const data = await getJSON("/api/health");
      state.engine = (data.engine && data.engine.mode) || "";
      return data;
    },
    async regions() { return getJSON("/api/regions"); },
    async districts(city) { return getJSON(`/api/regions/districts${qs({ city })}`); },
    async streets(city, district) { return getJSON(`/api/regions/streets${qs({ city, district })}`); },
    async areaSearch(text) { return getJSON(`/api/regions/search${qs({ q: text, limit: 8 })}`); },
    async suggest(text) { return getJSON(`/api/smart/suggest${qs({ q: text, limit: 6 })}`); },
    async parse(body) { return getJSON("/api/smart/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); },
    async start(request) { return getJSON("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) }); },
    async progress(runId) { return getJSON(`/api/runs/${encodeURIComponent(runId)}/progress`); },
    async search(params) { return getJSON(`/api/search${qs(params)}`); },
    async lead(id) { return getJSON(`/api/leads/${encodeURIComponent(id)}`); },
    async setStatus(id, status, note) {
      return getJSON(`/api/leads/${encodeURIComponent(id)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, note }),
      });
    },
    exportUrl(params) { return `/api/search/export${qs(params)}`; },
  };

  // ── لایهٔ آفلاین (دادهٔ نمونه) ──────────────────────────────────────────
  const Demo = {
    payload: null,
    async load() {
      const paths = ["demo-data.json", "../demo-data.json", "data/sample.json",
        "../data/sample.json", "../site/data/sample.json", "../../site/data/sample.json"];
      let lastError = null;
      for (const path of paths) {                       // eslint-disable-line no-await-in-loop
        try {
          const res = await fetch(path);
          if (!res.ok) continue;
          const text = await res.text();
          const data = text ? JSON.parse(text) : null;
          if (data && Array.isArray(data.leads) && data.leads.length) {
            Demo.payload = data;
            return data;
          }
        } catch (err) { lastError = err; }
      }
      throw lastError || new Error("دادهٔ نمونه پیدا نشد");
    },
    mini() { return (Demo.payload && Demo.payload.mini) || {}; },
    leads() {
      if (!Demo.payload) return [];
      return Demo.payload.leads.map((lead, index) => {
        const emails = Array.isArray(lead.emails) ? lead.emails : String(lead.emails || "").split(/[,\s]+/).filter(Boolean);
        const email = lead.email || lead.email_primary || emails[0] || "";
        return {
          id: lead.place_id || `demo-${index}`,
          ...lead,
          email_primary: email,
          emails,
          has_phone: !!lead.phone,
          has_email: !!email,
          has_website: !!(lead.website || lead.domain),
          has_social: !!(lead.instagram || lead.facebook || lead.linkedin),
          lat: Number(lead.lat || lead.latitude || 0),
          lon: Number(lead.lon || lead.longitude || 0),
          rating: Number(lead.rating || 0),
          reviews: Number(lead.reviews || 0),
          score: Number(lead.score || 0),
          status: lead.status || "new",
          status_fa: lead.status_fa || STATUS_FA[lead.status || "new"] || "تازه",
          segment_fa: lead.segment_fa || SEGMENT_FA[lead.segment] || "",
          gaps: lead.gaps_fa || [],
        };
      });
    },
    regions() {
      const mini = Demo.mini();
      return {
        provinces: mini.provinces || [],
        countries: mini.countries || [],
        radius: mini.radius || { street: 900, district: 2500, city: 10000, province: 30000 },
        levels_fa: mini.levels_fa || { street: "خیابان", district: "منطقه", city: "شهر", province: "استان" },
      };
    },
    districts(city) {
      const rows = (Demo.mini().districts || {})[city] || [];
      return { city, districts: rows.map((row, i) => ({ id: i, fa: row[0], lat: row[1], lon: row[2], kind: "district" })) };
    },
    streets(city) {
      const rows = (Demo.mini().streets || {})[city] || [];
      return { city, streets: rows.map((row, i) => ({ id: i, fa: row[0], lat: row[1], lon: row[2], district: row[3] || "", kind: "street" })) };
    },
    // فهرست نام‌ها: [[شکل‌های مختلف, نام اصلی, شهر, نوع]] — همان چیزی که سرور می‌سازد
    names() { return Demo.mini().names || []; },
    findArea(text, cityKey, level) {
      const needle = String(text || "").replace(/\u200c/g, " ").trim().toLowerCase();
      if (!needle) return null;
      let best = null;
      Demo.names().forEach((row) => {
        const [variant, canonical, city, kind] = row;
        if (level && kind !== level) return;
        if (cityKey && cityKey !== city) return;
        let score = 0;
        if (variant === needle) score = 1;
        else if (variant.startsWith(needle)) score = 0.92;
        else if (variant.includes(needle)) score = 0.8;
        if (!score) return;
        if (!best || score > best.score) {
          const point = Demo.pointOf(canonical, city, kind);
          best = { score, fa: canonical, city, kind, lat: point.lat, lon: point.lon };
        }
      });
      return best;
    },
    pointOf(canonical, city, kind) {
      const table = kind === "street" ? Demo.streets(city).streets : Demo.districts(city).districts;
      const hit = table.find((row) => row.fa === canonical);
      if (hit) return { lat: hit.lat, lon: hit.lon };
      const cityRow = Demo.cityRow(city);
      return { lat: cityRow ? cityRow.lat : null, lon: cityRow ? cityRow.lon : null };
    },
    cityRow(key) {
      const all = [...(Demo.regions().provinces || []), ...(Demo.regions().countries || [])];
      for (const group of all) {
        const hit = (group.cities || []).find((c) => c.id === key);
        if (hit) return hit;
      }
      return null;
    },
    detectArea(text) {
      const needle = String(text || "").replace(/\u200c/g, " ").toLowerCase();
      const names = Demo.names().slice().sort((a, b) => b[0].length - a[0].length);
      const taken = [];
      const hits = [];
      names.forEach((row) => {
        const [variant, canonical, city, kind] = row;
        if (variant.length < 3) return;
        const at = needle.indexOf(variant);
        if (at === -1) return;
        const end = at + variant.length;
        if (taken.some(([s, e]) => s < end && at < e)) return;
        taken.push([at, end]);
        const point = Demo.pointOf(canonical, city, kind);
        hits.push({ fa: canonical, kind, city, at, lat: point.lat, lon: point.lon });
      });
      return hits.sort((a, b) => a.at - b.at);
    },
    areaSearch(text) {
      const needle = String(text || "").replace(/\u200c/g, " ").trim().toLowerCase();
      if (!needle) return { areas: [], in_text: [], cities: [] };
      const seen = new Set();
      const areas = [];
      Demo.names().forEach((row) => {
        const [variant, canonical, city, kind] = row;
        let score = 0;
        if (variant === needle) score = 1;
        else if (variant.startsWith(needle)) score = 0.92;
        else if (variant.includes(needle)) score = 0.78;
        if (!score) return;
        const key = `${city}|${canonical}|${kind}`;
        if (seen.has(key)) return;
        seen.add(key);
        const cityRow = Demo.cityRow(city) || {};
        const point = Demo.pointOf(canonical, city, kind);
        areas.push({ score, kind, fa: canonical, city, city_fa: cityRow.fa || city, lat: point.lat, lon: point.lon, label: `${canonical} — ${cityRow.fa || city}` });
      });
      areas.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label, "fa"));
      return { areas: areas.slice(0, 8).map(({ score, ...rest }) => rest), in_text: Demo.detectArea(text), cities: [] };
    },
    // تجزیهٔ سبک جمله در حالت آفلاین (بدون سرور): صنف از کاتالوگ، منطقه از فهرست نام‌ها
    parse(body) {
      const text = String(body.text || "").replace(/\u200c/g, " ").trim();
      const niches = Demo.mini().niches || [];
      const lower = text.toLowerCase();
      let niche = null;
      if (body.niche_id) niche = niches.find((n) => n.id === body.niche_id) || null;
      if (!niche && text) {
        let best = 0;
        niches.forEach((n) => {
          const words = [n.fa, n.en || "", ...(n.aliases || [])].filter(Boolean);
          words.forEach((word) => {
            const w = String(word).toLowerCase();
            if (w.length < 3) return;
            const score = lower.includes(w) ? w.length : 0;
            if (score > best) { best = score; niche = n; }
          });
        });
      }
      const hits = text ? Demo.detectArea(text) : [];
      const district = body.district || (hits.find((h) => h.kind === "district") || {}).fa || "";
      const street = body.street || (hits.find((h) => h.kind === "street") || {}).fa || "";
      const cityKey = body.city || (hits[0] || {}).city || "";
      const area = Demo.resolve(body.province, cityKey, district, street);
      const allTrades = body.all_trades === true || /همه\s*(مشاغل|کسب|کسبو)/.test(text);
      const ready = !!(allTrades || niche);
      const label = area.label || cityKey || "تهران";
      const size = SIZES.find((s) => s.id === body.size) || SIZES[1];
      return Promise.resolve({
        mode: allTrades ? "sweep" : (niche ? "single" : "empty"),
        ready,
        missing: ready ? [] : ["niche"],
        detected: {
          niche_fa: allTrades ? "همهٔ کسب‌وکارها" : (niche ? niche.fa : ""),
          niche_id: allTrades ? "all-trades" : (niche ? niche.id : ""),
          all_trades: allTrades,
          area,
          city: label, city_key: area.city_key, lang: "fa",
        },
        summary: ready
          ? `${allTrades ? "همهٔ کسب‌وکارها" : `«${niche.fa}»`} در ${label} — شعاع ${faNum(area.radius)} متر (حالت ${size.fa.replace(/^\S+\s/, "")})`
          : "صنف را انتخاب کنید یا «همهٔ کسب‌وکارها» را بزنید.",
        warnings: state.serverOk ? [] : ["موتور زنده در دسترس نیست؛ نتیجه از دادهٔ نمونهٔ همین محدوده نمایش داده می‌شود."],
        keywords: [],
        eta_minutes: [1, 3],
        request: {},
      });
    },
    resolve(province, city, district, street) {
      const radius = Demo.regions().radius;
      const levelsFa = Demo.regions().levels_fa;
      const cityRow = city ? Demo.cityRow(city) : null;
      const parts = [];
      let lat = null; let lon = null; let level = "";
      if (street) {
        const hit = Demo.findArea(street, city, "street") || Demo.findArea(street, "", "street");
        parts.push(hit ? hit.fa : street);
        if (hit && hit.lat) { lat = hit.lat; lon = hit.lon; level = "street"; }
      }
      if (district) {
        const hit = Demo.findArea(district, city, "district") || Demo.findArea(district, "", "district");
        parts.push(hit ? hit.fa : district);
        if (lat === null && hit && hit.lat) { lat = hit.lat; lon = hit.lon; level = "district"; }
        else if (lat !== null && !level) level = "district";
      }
      const cityName = cityRow ? (cityRow.fa || cityRow.label) : "";
      if (cityName) parts.push(cityName);
      if (lat === null && cityRow) { lat = cityRow.lat; lon = cityRow.lon; level = "city"; }
      const provinceRow = (Demo.regions().provinces || []).find((p) => p.id === province);
      if (provinceRow && !parts.includes(provinceRow.fa)) parts.push(provinceRow.fa);
      if (lat === null && provinceRow) { lat = provinceRow.lat; lon = provinceRow.lon; level = "province"; }
      level = level || (lat !== null ? "city" : "");
      return {
        lat, lon, level, level_fa: levelsFa[level] || "",
        radius: radius[level] || radius.city || 10000,
        zoom: level === "street" ? 16 : level === "district" ? 15 : level === "city" ? 13 : 10,
        label: parts.reverse().join("، "),
        keyword_label: parts.slice().reverse().join("، "),
        city_key: cityRow ? cityRow.id : "", province: province || "", district, street,
        districts: cityRow ? (cityRow.districts || 0) : 0,
        streets: cityRow ? (cityRow.streets || 0) : 0,
      };
    },
    async search(params) {
      const rows = state.demoLeads.slice();
      const tokens = String(params.q || "").replace(/\u200c/g, " ").split(/[\s،,;]+/).filter((t) => t.length >= 2);
      const fields = ["title", "category", "niche_fa", "address", "city", "phone", "email",
        "email_primary", "website", "domain", "tags", "pitch"];
      const want = (key) => params[key] === true || params[key] === "1" || params[key] === 1;
      const matched = rows.filter((lead) => {
        if (params.run_id && lead.run_id && lead.run_id !== params.run_id) return false;
        if (params.segment && lead.segment !== params.segment) return false;
        if (params.status && lead.status !== params.status) return false;
        if (params.niche_fa && (lead.niche_fa || lead.category) !== params.niche_fa &&
            !String(lead.category || "").includes(params.niche_fa) &&
            !String(lead.niche_fa || "").includes(params.niche_fa)) return false;
        if (params.niche_id && lead.niche_id !== params.niche_id &&
            !String(lead.category || "").includes(params.niche_id) &&
            !String(lead.title || "").includes(params.niche_id)) return false;
        if (want("has_phone") && !lead.has_phone) return false;
        if (want("has_email") && !lead.has_email) return false;
        if (want("has_website") && !lead.has_website) return false;
        if (want("has_social") && !lead.has_social) return false;
        if (want("has_instagram") && !lead.instagram) return false;
        if (want("no_website") && lead.has_website) return false;
        if (params.min_score && lead.score < Number(params.min_score)) return false;
        if (params.min_rating && lead.rating < Number(params.min_rating)) return false;
        if (params.min_reviews && lead.reviews < Number(params.min_reviews)) return false;
        if (tokens.length) {
          const hay = fields.map((f) => String(lead[f] || "").replace(/\u200c/g, " ").toLowerCase()).join(" ");
          if (!tokens.every((token) => hay.includes(token.toLowerCase()))) return false;
        }
        if (params.lat && params.lon && params.radius_m) {
          const d = distanceM(Number(params.lat), Number(params.lon), lead.lat, lead.lon);
          lead.distance_m = Math.round(d);
          if (d > Number(params.radius_m)) return false;
        }
        return true;
      });
      const sortKey = SORTS.some((s) => s.id === params.sort) ? params.sort : "score";
      matched.sort((a, b) => {
        let left = a[sortKey]; let right = b[sortKey];
        if (typeof left === "string" || typeof right === "string") {
          left = String(left || ""); right = String(right || "");
          return (params.order === "asc" ? 1 : -1) * left.localeCompare(right, "fa");
        }
        return (params.order === "asc" ? 1 : -1) * ((Number(left) || 0) - (Number(right) || 0));
      });
      const limit = Number(params.limit || 25);
      const offset = Number(params.offset || 0);
      const page = matched.slice(offset, offset + limit);
      const facets = { segment: {}, status: {}, contact: {}, niche: {}, runs: {}, total: matched.length, counted: matched.length };
      matched.forEach((lead) => {
        facets.segment[lead.segment] = (facets.segment[lead.segment] || 0) + 1;
        facets.status[lead.status || "new"] = (facets.status[lead.status || "new"] || 0) + 1;
        const nicheKey = lead.niche_fa || "—";
        facets.niche[nicheKey] = (facets.niche[nicheKey] || 0) + 1;
        ["has_phone", "has_email", "has_website", "has_social"].forEach((key) => {
          if (lead[key]) facets.contact[key] = (facets.contact[key] || 0) + 1;
        });
      });
      const scores = matched.map((lead) => lead.score);
      facets.score = {
        min: scores.length ? Math.min(...scores) : 0,
        max: scores.length ? Math.max(...scores) : 0,
        avg: scores.length ? Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 10) / 10 : 0,
      };
      return { leads: page, total: matched.length, returned: page.length, facets, geo: {} };
    },
    async setStatus(id, status, note) {
      const lead = state.demoLeads.find((row) => row.id === id);
      if (!lead) throw new Error("لید پیدا نشد");
      lead.status = status;
      lead.status_fa = STATUS_FA[status] || status;
      if (note !== undefined) lead.note = note;
      return { ok: true, lead };
    },
  };

  let API = Live;

  // ── راه‌اندازی ───────────────────────────────────────────────────────────
  async function boot() {
    applyTheme(localStorage.getItem("leadgen-mini-theme") || "dark");
    bindStatic();
    fillSelects();
    try {
      await Live.health();
      state.serverOk = true;
      API = Live;
      const data = await Live.regions();
      state.regions = data;
      setPill();
      await loadCatalog();
      await fillProvinces();
      await onAreaChange();          // برچسب محدودهٔ پیش‌فرض همین اول نمایش داده شود
      await refresh();
    } catch (err) {
      state.serverOk = false;
      try {
        await Demo.load();
        API = Demo;
        state.demoLeads = Demo.leads();
        state.regions = Demo.regions();
        state.niches = Demo.mini().niches || [];
        state.groups = Demo.mini().groups || [];
        setPill();
        fillSelects();
        await fillProvinces();
        await onAreaChange();
        await refresh();
        toast("سرور محلی در دسترس نیست — حالت نمونه با دادهٔ مصنوعی فعال شد.", "warn");
      } catch (err2) {
        setPill(true);
        toast(`داده‌ای پیدا نشد: ${err2.message || err.message}`, "err");
      }
    }
  }

  function setPill(broken = false) {
    const pill = $("enginePill");
    if (broken) {
      pill.className = "pill";
      pill.textContent = "بدون داده";
      return;
    }
    if (state.serverOk) {
      const live = state.engine && state.engine !== "demo";
      pill.className = `pill ${live ? "ok" : "demo"}`;
      pill.innerHTML = `<span class="dot"></span> موتور: ${live ? "زنده (داکر)" : "دمو — برای داده واقعی: docker compose up -d"}`;
    } else {
      pill.className = "pill demo";
      pill.innerHTML = '<span class="dot"></span> حالت نمونه (صفحهٔ ثابت)';
    }
    if (!state.serverOk) {
      const full = $("fullLink");
      if (full) full.setAttribute("href", "../app/");
      const studio = $("studioLink");
      if (studio) studio.setAttribute("href", "../");
    }
  }

  async function loadCatalog() {
    try {
      const data = await getJSON("/api/catalog");
      state.niches = data.niches || [];
      state.groups = data.groups || [];
    } catch (err) {
      state.niches = [];
    }
  }

  function fillSelects() {
    // صنف‌ها
    const nicheSelect = $("selNiche");
    const groups = {};
    state.niches.forEach((niche) => {
      const group = niche.group || "سایر";
      (groups[group] = groups[group] || []).push(niche);
    });
    let html = '<option value="">از متن یا «همهٔ کسب‌وکارها»</option>';
    html += '<option value="__all__">همهٔ کسب‌وکارها (جاروی اصناف)</option>';
    Object.keys(groups).sort((a, b) => a.localeCompare(b, "fa")).forEach((group) => {
      html += `<optgroup label="${esc(group)}">`;
      groups[group].sort((a, b) => (a.fa || "").localeCompare(b.fa || "", "fa")).forEach((niche) => {
        html += `<option value="${esc(niche.id)}">${esc(niche.fa)}</option>`;
      });
      html += "</optgroup>";
    });
    nicheSelect.innerHTML = html;

    $("selGoal").innerHTML = GOALS.map((g) => `<option value="${g.id}"${g.id === state.sel.goal ? " selected" : ""}>${esc(g.fa)}</option>`).join("");
    $("selSize").innerHTML = SIZES.map((s) => `<option value="${s.id}"${s.id === state.sel.size ? " selected" : ""}>${esc(s.fa)}</option>`).join("");
    $("selCols").innerHTML = PRESETS.map((p) => `<option value="${p.id}"${p.id === "contact" ? " selected" : ""}>${esc(p.fa)}</option>`).join("");
    $("selStatus").innerHTML = STATUSES.map((s) => `<option value="${s.id}">${esc(s.fa)}</option>`).join("");
    $("selSort").innerHTML = SORTS.map((s) => `<option value="${s.id}"${s.id === state.filters.sort ? " selected" : ""}>${esc(s.fa)}</option>`).join("");
    renderChips();
  }

  async function fillProvinces() {
    const provinceSelect = $("selProvince");
    const groups = [...(state.regions.provinces || [])];
    let html = '<option value="">— انتخاب استان —</option>';
    groups.forEach((province) => {
      const count = (province.cities || []).length;
      html += `<option value="${esc(province.id)}" data-kind="province">${esc(province.fa)} (${faNum(count)} شهر)</option>`;
    });
    const foreign = state.regions.countries || [];
    if (foreign.length) {
      html += '<optgroup label="سایر کشورها">';
      foreign.forEach((country) => {
        html += `<option value="${esc(country.id)}" data-kind="country">${esc(country.fa)} (${faNum((country.cities || []).length)})</option>`;
      });
      html += "</optgroup>";
    }
    provinceSelect.innerHTML = html;
    // پیش‌فرض: استان تهران / شهر تهران
    const tehranProvince = (state.regions.provinces || []).find((p) => p.id === "tehran-province");
    if (tehranProvince) {
      provinceSelect.value = "tehran-province";
      state.sel.province = "tehran-province";
      await onProvinceChange(true);
    }
  }

  function provinceCities() {
    const all = [...(state.regions.provinces || []), ...(state.regions.countries || [])];
    const group = all.find((item) => item.id === state.sel.province);
    return group ? (group.cities || []) : [];
  }

  async function onProvinceChange(silent = false) {
    const cities = provinceCities();
    const citySelect = $("selCity");
    citySelect.innerHTML = '<option value="">— انتخاب شهر —</option>' + cities.map((city) => {
      const extra = (city.districts || city.streets)
        ? ` · ${faNum(city.districts || 0)} منطقه`
        : "";
      return `<option value="${esc(city.id)}">${esc(city.fa || city.label)}${extra}</option>`;
    }).join("");
    const capital = cities[0];
    if (capital) {
      citySelect.value = capital.id;
      state.sel.city = capital.id;
    } else {
      state.sel.city = "";
    }
    state.sel.district = "";
    state.sel.street = "";
    await onCityChange(true);
    if (!silent) await onAreaChange();
  }

  async function onCityChange(silent = false) {
    const city = state.sel.city;
    const districtSelect = $("selDistrict");
    const streetSelect = $("selStreet");
    districtSelect.innerHTML = '<option value="">همهٔ منطقه‌ها</option>';
    streetSelect.innerHTML = '<option value="">همهٔ خیابان‌ها</option>';
    state.districts = [];
    state.streets = [];
    if (!city) {
      if (!silent) await onAreaChange();
      return;
    }
    try {
      const [districtData, streetData] = state.serverOk
        ? await Promise.all([Live.districts(city), Live.streets(city, "")])
        : [Demo.districts(city), Demo.streets(city)];
      state.districts = districtData.districts || [];
      state.streets = streetData.streets || [];
      districtSelect.innerHTML = '<option value="">همهٔ منطقه‌ها</option>' +
        state.districts.map((row) => `<option value="${esc(row.fa)}">${esc(row.fa)}</option>`).join("");
      renderStreets();
    } catch (err) {
      toast(`فهرست منطقه‌ها گرفته نشد: ${err.message}`, "err");
    }
    if (!silent) await onAreaChange();
  }

  function renderStreets() {
    const streetSelect = $("selStreet");
    const district = state.sel.district;
    const rows = district
      ? state.streets.filter((row) => !row.district || row.district.includes(district.split("(")[0].trim()) || district.includes(row.district))
      : state.streets;
    const list = rows.length ? rows : state.streets;
    streetSelect.innerHTML = '<option value="">همهٔ خیابان‌ها</option>' +
      list.map((row) => `<option value="${esc(row.fa)}">${esc(row.fa)}</option>`).join("");
    if (state.sel.street && !list.some((row) => row.fa === state.sel.street)) {
      state.sel.street = "";
      streetSelect.value = "";
    }
  }

  async function onAreaChange() {
    const body = {
      province: state.sel.province, city: state.sel.city,
      district: state.sel.district, street: state.sel.street,
    };
    try {
      state.area = state.serverOk
        ? await getJSON(`/api/regions/resolve${qs(body)}`)
        : Demo.resolve(body.province, body.city, body.district, body.street);
    } catch (err) {
      state.area = null;
    }
    renderAreaInfo();
    renderHint();
  }

  function renderAreaInfo() {
    const box = $("areaInfo");
    const area = state.area;
    if (!area || (!area.lat && !area.label)) {
      box.innerHTML = '<span class="dot"></span> محدودهٔ انتخابی: <b>—</b> <span class="tag">استان و شهر را انتخاب کنید</span>';
      return;
    }
    const bits = [];
    if (area.level_fa) bits.push(`<span class="tag">سطح: ${esc(area.level_fa)}</span>`);
    if (area.radius) bits.push(`<span class="tag">شعاع: ${faNum(Number(area.radius).toLocaleString("en-US"))} متر</span>`);
    if (area.districts) bits.push(`<span class="tag">${faNum(area.districts)} منطقه</span>`);
    if (area.streets) bits.push(`<span class="tag">${faNum(area.streets)} خیابان</span>`);
    if (area.lat && area.lon) {
      bits.push(`<span class="tag mono">${Number(area.lat).toFixed(4)}, ${Number(area.lon).toFixed(4)}</span>`);
      bits.push(`<a href="https://www.google.com/maps/search/?api=1&query=${area.lat},${area.lon}" target="_blank" rel="noopener">نمایش روی نقشه ↗</a>`);
    }
    box.innerHTML = `<span class="dot"></span> محدودهٔ انتخابی: <b>${esc(area.label || "—")}</b> ${bits.join(" ")}`;
  }

  function renderHint() {
    const hint = $("parseHint");
    const parsed = state.parsed;
    if (!parsed) {
      hint.textContent = "استان و شهر را انتخاب کنید یا همین‌جا بنویسید؛ منطقه و خیابان هم از متن فهمیده می‌شود.";
      return;
    }
    const area = parsed.detected && parsed.detected.area ? parsed.detected.area : state.area;
    const bits = [`<b>${esc(parsed.detected.niche_fa || "—")}</b>`];
    if (area && area.label) bits.push(`<span class="kv">در ${esc(area.label)}</span>`);
    if (area && area.radius) bits.push(`<span class="kv">شعاع ${faNum(Number(area.radius).toLocaleString("en-US"))} متر</span>`);
    if (parsed.keywords && parsed.keywords.length) bits.push(`<span class="kv">کلیدواژه: ${esc(parsed.keywords[0])}${parsed.keywords.length > 1 ? ` +${faNum(parsed.keywords.length - 1)}` : ""}</span>`);
    if (parsed.eta_minutes) bits.push(`<span class="kv">~${faNum(parsed.eta_minutes[0])}–${faNum(parsed.eta_minutes[1])} دقیقه</span>`);
    if (parsed.warnings && parsed.warnings.length) bits.push(`<span class="kv">⚠ ${faNum(parsed.warnings.length)} هشدار</span>`);
    hint.innerHTML = bits.join(" · ");
    hint.title = (parsed.warnings || []).join("\n");
  }

  // فیلتر «نوع کسب‌وکار» روی نتیجه‌ها — از شمارش‌های زندهٔ facets پر می‌شود
  function renderNicheFilter() {
    const select = $("selNicheFilter");
    if (!select) return;
    const facets = (state.facets || {}).niche || {};
    const current = state.filters.niche_fa;
    const names = Object.keys(facets);
    if (!names.length && !current) {
      select.innerHTML = '<option value="">همهٔ اصناف</option>';
      return;
    }
    select.innerHTML = '<option value="">همهٔ اصناف</option>' +
      names.map((name) => `<option value="${esc(name)}"${name === current ? " selected" : ""}>` +
        `${esc(name)} (${faNum(facets[name])})</option>`).join("") +
      (current && !names.includes(current)
        ? `<option value="${esc(current)}" selected>${esc(current)}</option>` : "");
  }

  function renderChips() {
    const facets = state.facets || {};
    const segments = facets.segment || {};
    $("segmentRow").innerHTML = SEGMENTS.map((segment) => {
      const count = segment.id ? (segments[segment.id] || 0) : (facets.total || state.total || 0);
      const on = state.filters.segment === segment.id;
      return `<button type="button" class="chip ${segment.id} ${on ? "on" : ""}" data-segment="${esc(segment.id)}">` +
        `${esc(segment.fa)}<span class="n">${faNum(count)}</span></button>`;
    }).join("") + '<span class="sep"></span>' +
      `<span class="chip" style="cursor:default">میانگین امتیاز <span class="n">${faNum((facets.score || {}).avg || 0)}</span></span>`;

    $("contactRow").innerHTML = CONTACTS.map((item) => {
      const key = item.id;
      const on = key === "no_website" ? !!state.filters.no_website : state.filters[key] === true;
      const count = (facets.contact || {})[key] || 0;
      return `<button type="button" class="chip ${on ? "on" : ""}" data-contact="${esc(key)}">` +
        `${esc(item.fa)}${count ? `<span class="n">${faNum(count)}</span>` : ""}</button>`;
    }).join("");
  }

  // ── پرس‌وجو از داده‌ها ───────────────────────────────────────────────────
  function searchParams(forExport = false) {
    const filters = state.filters;
    const area = state.area;
    const params = {
      q: filters.q,
      segment: filters.segment,
      status: filters.status,
      gap: filters.gap,
      niche_id: filters.niche_id,
      niche_fa: filters.niche_fa,
      has_phone: filters.has_phone === true ? 1 : "",
      has_email: filters.has_email === true ? 1 : "",
      has_website: filters.has_website === true ? 1 : "",
      has_social: filters.has_social === true ? 1 : "",
      has_instagram: filters.has_instagram === true ? 1 : "",
      no_website: filters.no_website ? 1 : "",
      min_score: filters.min_score,
      min_rating: filters.min_rating,
      min_reviews: filters.min_reviews,
      sort: filters.sort,
      order: filters.order,
      limit: forExport ? 20000 : state.perPage,
      offset: forExport ? 0 : (state.page - 1) * state.perPage,
    };
    if (state.mode === "run" && state.runId) params.run_id = state.runId;
    if (filters.geoOnly && area && area.lat && area.lon) {
      params.lat = area.lat;
      params.lon = area.lon;
      params.radius_m = area.radius || 2500;
    }
    return params;
  }

  async function refresh() {
    if (state.busy) return;
    state.busy = true;
    const tbody = $("tbody");
    tbody.innerHTML = '<tr><td colspan="12" class="empty">در حال بارگذاری…</td></tr>';
    try {
      const params = searchParams();
      if (params.no_website) {
        // «بدون سایت» در سمت سرور با has_website=0 معادل می‌شود
        params.has_website = 0;
        delete params.no_website;
      } else {
        delete params.no_website;
      }
      const data = state.serverOk ? await Live.search(params) : await Demo.search(params);
      state.leads = data.leads || [];
      state.total = data.total || 0;
      state.facets = data.facets || {};
      renderResults();
      renderChips();
      renderNicheFilter();
      renderPager();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="12" class="empty">خطا: ${esc(err.message)}</td></tr>`;
      toast(`جست‌وجو ناموفق: ${err.message}`, "err");
    } finally {
      state.busy = false;
    }
  }

  function socialIcons(lead) {
    const items = [
      { href: lead.instagram, label: "IG", title: "اینستاگرام" },
      { href: lead.facebook, label: "FB", title: "فیس‌بوک" },
      { href: lead.linkedin, label: "IN", title: "لینکدین" },
      { href: lead.website || (lead.domain ? `https://${lead.domain}` : ""), label: "🌐", title: "وب‌سایت" },
      { href: lead.maps_link || (lead.lat && lead.lon ? `https://www.google.com/maps/search/?api=1&query=${lead.lat},${lead.lon}` : ""), label: "📍", title: "نقشه" },
    ];
    return `<span class="icons">` + items.map((item) => (item.href
      ? `<a href="${esc(item.href)}" target="_blank" rel="noopener" title="${esc(item.title)}">${esc(item.label)}</a>`
      : `<span class="off" title="ندارد">${esc(item.label)}</span>`)).join("") + "</span>";
  }

  function renderResults() {
    const tbody = $("tbody");
    const leads = state.leads;
    $("resCount").textContent = `${faNum(state.total)} کسب‌وکار`;
    const notes = [];
    if (state.mode === "run" && state.runId) notes.push(`اجرای ${state.runId}`);
    if (state.area && state.area.label) notes.push(state.area.label);
    if (state.filters.geoOnly && state.area && state.area.radius) notes.push(`شعاع ${faNum(Number(state.area.radius).toLocaleString("en-US"))} متر`);
    if (!state.serverOk) notes.push("دادهٔ نمونه");
    $("resNote").textContent = notes.length ? `— ${notes.join(" · ")}` : "";
    $("emptyRow").hidden = leads.length > 0;
    if (!leads.length) {
      tbody.innerHTML = "";
      return;
    }
    tbody.innerHTML = leads.map((lead) => {
      const segment = lead.segment || "cold";
      const phone = lead.phone ? `<a class="mono" href="tel:${esc(String(lead.phone).replace(/\s/g, ""))}">${esc(lead.phone)}</a>` : '<span class="miss">—</span>';
      const email = lead.email_primary || lead.email
        ? `<a class="mono" href="mailto:${esc(lead.email_primary || lead.email)}">${esc(lead.email_primary || lead.email)}</a>`
        : '<span class="miss">—</span>';
      const site = lead.domain || lead.website
        ? `<a class="mono" href="${esc(lead.website || `https://${lead.domain}`)}" target="_blank" rel="noopener">${esc(lead.domain || "سایت")}</a>`
        : '<span class="miss">—</span>';
      const area = lead.address || lead.city || lead.run_area || "";
      return `<tr data-id="${esc(lead.id)}">
        <td><span class="name">${esc(lead.title || "—")}</span>
          <span class="sub">${esc(lead.category || lead.niche_fa || "")}</span></td>
        <td>${esc(lead.niche_fa || lead.category || "—")}</td>
        <td><span class="sub" title="${esc(area)}">${esc(area)}</span>
          ${lead.distance_m ? `<span class="sub mono">${faNum(Number(lead.distance_m).toLocaleString("en-US"))} m</span>` : ""}</td>
        <td>${phone}</td>
        <td>${email}</td>
        <td>${site}</td>
        <td>${socialIcons(lead)}</td>
        <td class="num">${lead.rating ? faNum(Number(lead.rating).toFixed(1)) : '<span class="miss">—</span>'}</td>
        <td class="num">${lead.reviews ? faNum(Number(lead.reviews).toLocaleString("en-US")) : '<span class="miss">—</span>'}</td>
        <td class="num"><span class="score ${esc(segment)}">${faNum(lead.score || 0)}</span>
          <span class="sub"><span class="seg ${esc(segment)}">${esc(lead.segment_fa || SEGMENT_FA[segment] || "")}</span></span></td>
        <td><span class="seg">${esc(lead.status_fa || STATUS_FA[lead.status] || "تازه")}</span></td>
        <td><button type="button" class="ghost small" data-open="${esc(lead.id)}">جزئیات</button></td>
      </tr>`;
    }).join("");
  }

  function renderPager() {
    const pager = $("pager");
    const pages = Math.max(1, Math.ceil(state.total / state.perPage));
    const from = state.total ? (state.page - 1) * state.perPage + 1 : 0;
    const to = Math.min(state.total, state.page * state.perPage);
    pager.innerHTML = `
      <button type="button" class="ghost small" id="prevPage" ${state.page <= 1 ? "disabled" : ""}>→ قبلی</button>
      <span>صفحهٔ ${faNum(state.page)} از ${faNum(pages)}</span>
      <button type="button" class="ghost small" id="nextPage" ${state.page >= pages ? "disabled" : ""}>بعدی ←</button>
      <span class="sp">نمایش ${faNum(from)}–${faNum(to)} از ${faNum(state.total)}</span>
      <label>در هر صفحه
        <select id="perPage">
          ${[25, 50, 100, 200].map((size) => `<option value="${size}"${size === state.perPage ? " selected" : ""}>${faNum(size)}</option>`).join("")}
        </select>
      </label>`;
    const prev = $("prevPage"); const next = $("nextPage"); const perPage = $("perPage");
    if (prev) prev.addEventListener("click", () => { state.page = Math.max(1, state.page - 1); refresh(); });
    if (next) next.addEventListener("click", () => { state.page = Math.min(pages, state.page + 1); refresh(); });
    if (perPage) perPage.addEventListener("change", () => {
      state.perPage = Number(perPage.value) || 25;
      state.page = 1;
      refresh();
    });
  }

  // ── اجرا (فقط با سرور زنده) ──────────────────────────────────────────────
  function parseBody() {
    const text = $("q").value.trim();
    const nicheValue = $("selNiche").value;
    const allTrades = $("allTrades").checked || nicheValue === "__all__";
    return {
      text,
      goal: $("selGoal").value,
      size: $("selSize").value,
      province: state.sel.province,
      city: state.sel.city || $("selCity").value,
      district: state.sel.district,
      street: state.sel.street,
      niche_id: allTrades ? "" : nicheValue,
      all_trades: allTrades,
      socials: true,
      emails: true,
      cols: "contact",
      export_fmt: "xlsx",
      engine_mode: "auto",
      limit: 0,
    };
  }

  async function parseQuery() {
    const body = parseBody();
    try {
      state.parsed = state.serverOk ? await Live.parse(body) : await Demo.parse(body);
    } catch (err) {
      state.parsed = null;
      toast(`تجزیهٔ جمله ناموفق: ${err.message}`, "err");
    }
    renderHint();
    return state.parsed;
  }

  const parseDebounced = debounce(() => { parseQuery(); }, 320);

  async function suggest(text) {
    const box = $("suggest");
    if (!text || text.length < 2) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    try {
      const areaData = state.serverOk ? await Live.areaSearch(text) : Demo.areaSearch(text);
      const nicheData = state.serverOk ? await Live.suggest(text) : { niches: [] };
      const chips = [];
      (areaData.areas || []).slice(0, 6).forEach((area) => {
        chips.push(`<button type="button" data-area-city="${esc(area.city)}" data-area-name="${esc(area.fa)}" data-area-kind="${esc(area.kind)}"><span class="kind">${area.kind === "street" ? "خیابان" : "منطقه"}</span>${esc(area.label)}</button>`);
      });
      (areaData.cities || []).slice(0, 3).forEach((city) => {
        chips.push(`<button type="button" data-city="${esc(city.id)}"><span class="kind">شهر</span>${esc(city.fa || city.label)}</button>`);
      });
      ((nicheData && nicheData.niches) || []).slice(0, 5).forEach((niche) => {
        chips.push(`<button type="button" data-niche="${esc(niche.id)}"><span class="kind">صنف</span>${esc(niche.fa)}</button>`);
      });
      if ((areaData.in_text || []).length) {
        chips.push(`<button type="button" data-apply="1"><span class="kind">جمله</span>اعمال «${esc((areaData.in_text || []).map((h) => h.fa).join(" و "))}»</button>`);
      }
      box.innerHTML = chips.join("");
      box.hidden = !chips.length;
    } catch (err) {
      box.hidden = true;
    }
  }
  const suggestDebounced = debounce(() => suggest($("q").value.trim()), 260);

  async function find() {
    const parsed = await parseQuery();
    if (!parsed || !parsed.ready) {
      toast(parsed && parsed.summary ? parsed.summary : "صنف یا «همهٔ کسب‌وکارها» را مشخص کنید.", "err");
      return;
    }
    if (!state.serverOk) {
      // بدون سرور، اجرای موتور ممکن نیست؛ همان محدوده را در دادهٔ نمونه جست‌وجو می‌کنیم
      state.mode = "stored";
      state.runId = "";
      state.filters.geoOnly = true;
      $("geoOnly").checked = true;
      await onAreaChange();
      state.page = 1;
      await refresh();
      toast("موتور زنده در دسترس نیست — کسب‌وکارهای دادهٔ نمونه در همین محدوده فهرست شدند.", "warn");
      return;
    }
    const request = parsed.request || {};
    if (!request || (!request.niche_query && !request.all_niches && !request.keywords)) {
      toast("درخواست اجرا ناقص است؛ صنف را مشخص کنید.", "err");
      return;
    }
    $("findBtn").disabled = true;
    try {
      const started = await Live.start(request);
      state.runId = started.run_id || started.id || "";
      state.mode = "run";
      showRun(parsed);
      await poll();
    } catch (err) {
      toast(`اجرا شروع نشد: ${err.message}`, "err");
      $("runCard").classList.remove("err");
      $("runCard").hidden = false;
      $("runCard").classList.add("err");
      $("runMsg").textContent = err.message;
    } finally {
      $("findBtn").disabled = false;
    }
  }

  function showRun(parsed) {
    const card = $("runCard");
    card.hidden = false;
    card.classList.remove("err");
    $("runTitle").textContent = parsed.detected.niche_fa || "اجرا";
    $("runStage").textContent = parsed.detected.area ? parsed.detected.area.label : "";
    $("runBar").style.width = "4%";
    $("runMsg").textContent = parsed.summary || "";
  }

  async function poll() {
    if (!state.runId) return;
    clearTimeout(state.poller);
    let ticks = 0;
    const tick = async () => {
      ticks += 1;
      try {
        const data = await Live.progress(state.runId);
        const progress = data.progress || {};
        const percent = Math.max(2, Math.min(100, Number(progress.percent) || 0));
        $("runBar").style.width = `${percent}%`;
        $("runStage").textContent = progress.stage || "";
        $("runMsg").textContent = progress.message || "";
        if (progress.status === "failed" || progress.error) {
          $("runCard").classList.add("err");
          $("runMsg").textContent = progress.error || progress.message || "اجرا ناموفق بود.";
          toast("اجرا ناموفق بود؛ جزئیات در نوار اجرا آمده است.", "err");
          return;
        }
        if (progress.status === "done" || progress.percent >= 100) {
          $("runBar").style.width = "100%";
          toast("اجرا تمام شد — نتیجه‌ها بارگذاری شدند.", "ok");
          state.page = 1;
          await refresh();
          setTimeout(() => { $("runCard").hidden = true; }, 2600);
          return;
        }
      } catch (err) {
        if (ticks > 3) {
          $("runCard").classList.add("err");
          $("runMsg").textContent = err.message;
          return;
        }
      }
      state.poller = setTimeout(tick, 1200);
    };
    await tick();
  }

  // ── جزئیات یک لید ────────────────────────────────────────────────────────
  function findLead(id) {
    return state.leads.find((lead) => String(lead.id) === String(id)) ||
      state.demoLeads.find((lead) => String(lead.id) === String(id)) || null;
  }

  async function openLead(id) {
    let lead = findLead(id);
    if (state.serverOk) {
      try {
        const detail = ((await Live.lead(id)) || {}).lead || null;
        if (detail) lead = detail;
      } catch (err) { /* همان ردیف فهرست کافی است */ }
    }
    if (!lead) {
      toast("این لید در فهرست جاری نیست.", "err");
      return;
    }
    document.querySelectorAll("#tbody tr.sel").forEach((row) => row.classList.remove("sel"));
    const row = document.querySelector(`#tbody tr[data-id="${CSS.escape(String(id))}"]`);
    if (row) row.classList.add("sel");

    const emails = Array.isArray(lead.emails) && lead.emails.length ? lead.emails : [lead.email_primary || lead.email].filter(Boolean);
    const mapsHref = lead.maps_link || (lead.lat && lead.lon ? `https://www.google.com/maps/search/?api=1&query=${lead.lat},${lead.lon}` : "");
    const waHref = lead.phone ? `https://wa.me/${String(lead.phone).replace(/[^0-9]/g, "")}` : "";
    const field = (label, value, raw = false) => `<div class="mfield"><span>${esc(label)}</span><b>${raw ? value : esc(value || "—")}</b></div>`;
    $("modalBody").innerHTML = `
      <div class="mhead">
        <div>
          <h3 id="modalTitle">${esc(lead.title || "—")}</h3>
          <span class="sub">${esc(lead.category || lead.niche_fa || "")} · ${esc(lead.city || "")}</span>
        </div>
        <span class="seg ${esc(lead.segment || "")}">${esc(lead.segment_fa || SEGMENT_FA[lead.segment] || "")}</span>
        <span class="score ${esc(lead.segment || "")}" style="font-size:1.1rem">${faNum(lead.score || 0)}</span>
      </div>
      <div class="mgrid">
        ${field("تلفن", lead.phone ? `<a class="mono" href="tel:${esc(String(lead.phone).replace(/\s/g, ""))}">${esc(lead.phone)}</a>` : "", true)}
        ${field("ایمیل", emails.length ? emails.map((mail) => `<a class="mono" href="mailto:${esc(mail)}">${esc(mail)}</a>`).join("<br>") : "", true)}
        ${field("وب‌سایت", lead.website || lead.domain ? `<a class="mono" href="${esc(lead.website || `https://${lead.domain}`)}" target="_blank" rel="noopener">${esc(lead.domain || lead.website)}</a>` : "", true)}
        ${field("اینستاگرام", lead.instagram ? `<a class="mono" href="${esc(lead.instagram)}" target="_blank" rel="noopener">${esc(lead.instagram.replace(/^https?:\/\/(www\.)?instagram\.com\//, "@"))}</a>` : "", true)}
        ${field("فیس‌بوک / لینکدین", [lead.facebook, lead.linkedin].filter(Boolean).map((href) => `<a class="mono" href="${esc(href)}" target="_blank" rel="noopener">${esc(href.replace(/^https?:\/\//, "").slice(0, 34))}</a>`).join("<br>") || "", true)}
        ${field("ستاره و نظر", `${lead.rating ? faNum(Number(lead.rating).toFixed(1)) : "—"} ★ · ${faNum(Number(lead.reviews || 0).toLocaleString("en-US"))} نظر`)}
        ${field("لوکیشن دقیق", lead.lat && lead.lon ? `<span class="mono">${Number(lead.lat).toFixed(6)}, ${Number(lead.lon).toFixed(6)}</span>` : "")}
        ${field("پلاس‌کد", lead.plus_code || "")}
        ${field("کامل بودن اطلاعات", `${faNum(lead.completeness || 0)}٪`)}
        ${field("کانال پیشنهادی", lead.channel_fa || lead.channel || "")}
        ${field("منطقه / اجرا", lead.run_area || lead.city || "")}
        ${field("وضعیت", lead.status_fa || STATUS_FA[lead.status] || "تازه")}
      </div>
      <div class="mgrid">
        ${field("نشانی کامل", lead.address || "")}
        ${field("زاویهٔ فروش", lead.pitch || "")}
        ${field("شکاف‌های دیجیتال", (lead.gaps || lead.gaps_fa || []).join("، ") || "")}
        ${field("برچسب‌ها", (lead.tags || []).join("، ") || "")}
      </div>
      <div class="mrow">
        ${mapsHref ? `<a class="ghost small" href="${esc(mapsHref)}" target="_blank" rel="noopener">📍 نقشه</a>` : ""}
        ${lead.phone ? `<a class="ghost small" href="tel:${esc(String(lead.phone).replace(/\s/g, ""))}">📞 تماس</a>` : ""}
        ${waHref ? `<a class="ghost small" href="${esc(waHref)}" target="_blank" rel="noopener">💬 واتساپ</a>` : ""}
        ${lead.phone ? `<button type="button" class="ghost small" data-copy="${esc(lead.phone)}">کپی تلفن</button>` : ""}
        ${emails.length ? `<button type="button" class="ghost small" data-copy="${esc(emails[0])}">کپی ایمیل</button>` : ""}
      </div>
      <div class="mrow" style="margin-top:.7rem">
        <label>وضعیت پیگیری
          <select id="leadStatus">${STATUSES.filter((s) => s.id).map((s) => `<option value="${s.id}"${s.id === lead.status ? " selected" : ""}>${esc(s.fa)}</option>`).join("")}</select>
        </label>
        <button type="button" class="primary" id="saveStatus" style="padding:.4rem .8rem">ذخیرهٔ وضعیت</button>
      </div>
      <textarea class="mnote" id="leadNote" placeholder="یادداشت داخلی…">${esc(lead.note || "")}</textarea>
      ${campaignBlock(lead)}
    `;
    bindCampaign(lead);
    $("modal").hidden = false;
    const saveBtn = $("saveStatus");
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      const status = $("leadStatus").value;
      const note = $("leadNote").value;
      try {
        if (state.serverOk) await Live.setStatus(lead.id, status, note);
        else await Demo.setStatus(lead.id, status, note);
        lead.status = status;
        lead.status_fa = STATUS_FA[status] || status;
        lead.note = note;
        toast("وضعیت ذخیره شد.", "ok");
        renderResults();
      } catch (err) {
        toast(`ذخیره نشد: ${err.message}`, "err");
      }
    });
    $("modalBody").querySelectorAll("[data-copy]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const text = btn.getAttribute("data-copy");
        try {
          await navigator.clipboard.writeText(text);
          toast("کپی شد.", "ok");
        } catch (err) {
          toast(`کپی نشد: ${text}`, "err");
        }
      });
    });
  }

  // متن آمادهٔ تماس — همان قالب‌های کمپین نسخهٔ کامل، فقط برای لید جاری
  const CHANNEL_FA = { email: "ایمیل", whatsapp: "واتساپ", sms: "پیامک", instagram: "اینستاگرام", phone: "تماس تلفنی" };
  const channelFa = (channel) => CHANNEL_FA[channel] || channel;

  function campaignBlock(lead) {
    const campaign = lead.campaign || null;
    if (!campaign) {
      return state.serverOk ? "" :
        '<p class="hint" style="margin-top:.6rem">در حالت نمونه، متن آمادهٔ تماس در دسترس نیست (نیاز به سرور محلی).</p>';
    }
    const channels = Object.keys(campaign);
    const message = campaign[channels[0] || ""] || {};
    return `
      <div class="mrow" style="margin-top:.8rem" id="campaignTabs">
        ${channels.map((channel, index) => `<button type="button" class="ghost small ${index === 0 ? "on" : ""}" data-channel="${esc(channel)}">${esc(channelFa(channel))}</button>`).join("")}
      </div>
      <div class="mfield" style="margin-top:.4rem">
        <span id="campaignTo">${esc(message.to || "")}${message.subject ? ` — ${esc(message.subject)}` : ""}</span>
        <b id="campaignText" style="white-space:pre-wrap;font-weight:400;font-size:.8rem">${esc(message.text || message.body || "")}</b>
      </div>
      <div class="mrow"><button type="button" class="ghost small" id="copyCampaign">کپی متن</button></div>`;
  }

  function bindCampaign(lead) {
    const campaign = lead.campaign;
    if (!campaign) return;
    const tabs = $("campaignTabs");
    if (tabs) tabs.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-channel]");
      if (!btn) return;
      tabs.querySelectorAll("button").forEach((item) => item.classList.remove("on"));
      btn.classList.add("on");
      const message = campaign[btn.dataset.channel] || {};
      $("campaignTo").textContent = `${message.to || ""}${message.subject ? ` — ${message.subject}` : ""}`;
      $("campaignText").textContent = message.text || message.body || "";
    });
    const copyBtn = $("copyCampaign");
    if (copyBtn) copyBtn.addEventListener("click", async () => {
      const text = $("campaignText").textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        toast("متن کمپین کپی شد.", "ok");
      } catch (err) {
        toast("کپی ممکن نشد؛ متن را دستی انتخاب کنید.", "err");
      }
    });
  }

  function closeModal() {
    $("modal").hidden = true;
    $("modalBody").innerHTML = "";
    document.querySelectorAll("#tbody tr.sel").forEach((row) => row.classList.remove("sel"));
  }

  // ── خروجی ────────────────────────────────────────────────────────────────
  function exportAs(fmt) {
    const params = searchParams(true);
    params.fmt = fmt;
    params.cols = $("selCols").value || "contact";
    delete params.limit;
    delete params.offset;
    params.max_rows = 20000;
    if (params.no_website) { params.has_website = 0; delete params.no_website; } else { delete params.no_website; }
    if (!state.serverOk) {
      if (fmt === "xlsx") {
        toast("در حالت نمونه، خروجی اکسل در دسترس نیست — CSV/JSON بگیرید یا سرور را اجرا کنید.", "warn");
        return;
      }
      clientExport(fmt, params);
      return;
    }
    const url = Live.exportUrl(params);
    const link = document.createElement("a");
    link.href = url;
    link.download = "";
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast(`خروجی ${fmt.toUpperCase()} از ${faNum(state.total)} رکورد آماده شد.`, "ok");
  }

  function clientExport(fmt, params) {
    const rows = state.leads;
    if (!rows.length) {
      toast("چیزی برای خروجی گرفتن نیست.", "err");
      return;
    }
    let blob; let name;
    if (fmt === "json") {
      blob = new Blob([JSON.stringify({ total: state.total, filters: params, leads: rows }, null, 2)], { type: "application/json;charset=utf-8" });
      name = "leadgen-mini.json";
    } else {
      const headers = ["نام", "صنف", "نشانی", "تلفن", "ایمیل", "سایت", "اینستاگرام", "امتیاز", "ستاره", "نظر", "lat", "lon"];
      const lines = [headers.join(",")].concat(rows.map((lead) => [
        lead.title, lead.niche_fa || lead.category, lead.address || lead.city, lead.phone,
        lead.email_primary || lead.email, lead.website || lead.domain, lead.instagram,
        lead.score, lead.rating, lead.reviews, lead.lat, lead.lon,
      ].map((cell) => `"${String(cell == null ? "" : cell).replace(/"/g, '""')}"`).join(",")));
      blob = new Blob([`\uFEFF${lines.join("\n")}`], { type: "text/csv;charset=utf-8" });
      name = "leadgen-mini.csv";
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    toast("خروجی از نتیجه‌های نمایش‌داده‌شده ساخته شد.", "ok");
  }

  // ── رویدادها ─────────────────────────────────────────────────────────────
  function bindStatic() {
    $("q").addEventListener("input", () => {
      parseDebounced();
      suggestDebounced();
    });
    $("q").addEventListener("keydown", (event) => {
      if (event.key === "Enter") find();
    });
    $("findBtn").addEventListener("click", find);
    $("storedBtn").addEventListener("click", async () => {
      state.mode = "stored";
      state.runId = "";
      $("runCard").hidden = true;
      const text = $("q").value.trim();
      // «جست‌وجوی قوی»: همان عبارت (بدون نام‌های جغرافیایی) روی همهٔ داده‌های جمع‌شده
      const areaNames = (state.parsed && state.parsed.detected && state.parsed.detected.area
        ? state.parsed.detected.area.in_text : []) || [];
      let query = text;
      areaNames.forEach((name) => { query = query.split(name).join(" "); });
      query = query.replace(/\s+/g, " ").trim();
      state.filters.q = query;
      $("inResults").value = query;
      state.page = 1;
      await refresh();
      toast(query ? `«${query}» در همهٔ داده‌های جمع‌شده جست‌وجو شد.`
        : "همهٔ داده‌های جمع‌شده فهرست شدند.", "ok");
    });

    $("suggest").addEventListener("click", async (event) => {
      const btn = event.target.closest("button");
      if (!btn) return;
      if (btn.dataset.niche) {
        $("selNiche").value = btn.dataset.niche;
        state.sel.niche = btn.dataset.niche;
        $("allTrades").checked = false;
        state.sel.allTrades = false;
        toast("صنف انتخاب شد.");
      } else if (btn.dataset.city) {
        const cityId = btn.dataset.city;
        const province = findProvinceOfCity(cityId);
        if (province) { $("selProvince").value = province; state.sel.province = province; await onProvinceChange(true); }
        $("selCity").value = cityId;
        state.sel.city = cityId;
        await onCityChange(true);
        await onAreaChange();
        toast("شهر انتخاب شد.");
      } else if (btn.dataset.areaName) {
        if (btn.dataset.areaCity) {
          const province = findProvinceOfCity(btn.dataset.areaCity);
          if (province && province !== state.sel.province) {
            $("selProvince").value = province;
            state.sel.province = province;
            await onProvinceChange(true);
          }
          if (btn.dataset.areaCity !== state.sel.city) {
            $("selCity").value = btn.dataset.areaCity;
            state.sel.city = btn.dataset.areaCity;
            await onCityChange(true);
          }
        }
        if (btn.dataset.areaKind === "street") {
          $("selStreet").value = btn.dataset.areaName;
          state.sel.street = btn.dataset.areaName;
        } else {
          $("selDistrict").value = btn.dataset.areaName;
          state.sel.district = btn.dataset.areaName;
          state.sel.street = "";
          $("selStreet").value = "";
          renderStreets();
        }
        await onAreaChange();
        toast(`محدوده: ${btn.dataset.areaName}`);
      } else if (btn.dataset.apply) {
        await parseQuery();
        await refresh();
      }
      $("suggest").hidden = true;
    });

    $("selProvince").addEventListener("change", async (event) => {
      state.sel.province = event.target.value;
      await onProvinceChange();
    });
    $("selCity").addEventListener("change", async (event) => {
      state.sel.city = event.target.value;
      state.sel.district = "";
      state.sel.street = "";
      await onCityChange();
    });
    $("selDistrict").addEventListener("change", async (event) => {
      state.sel.district = event.target.value;
      state.sel.street = "";
      $("selStreet").value = "";
      renderStreets();
      await onAreaChange();
      if (state.filters.geoOnly) { state.page = 1; await refresh(); }
    });
    $("selStreet").addEventListener("change", async (event) => {
      state.sel.street = event.target.value;
      await onAreaChange();
      if (state.filters.geoOnly) { state.page = 1; await refresh(); }
    });
    $("selNiche").addEventListener("change", (event) => {
      const value = event.target.value;
      state.sel.niche = value === "__all__" ? "" : value;
      if (value === "__all__") { $("allTrades").checked = true; state.sel.allTrades = true; }
      parseQuery();
    });
    $("allTrades").addEventListener("change", (event) => {
      state.sel.allTrades = event.target.checked;
      if (event.target.checked) { $("selNiche").value = "__all__"; state.sel.niche = ""; }
      parseQuery();
    });
    $("selGoal").addEventListener("change", () => parseQuery());
    $("selSize").addEventListener("change", () => parseQuery());

    $("segmentRow").addEventListener("click", (event) => {
      const chip = event.target.closest("[data-segment]");
      if (!chip) return;
      state.filters.segment = chip.dataset.segment;
      state.page = 1;
      refresh();
    });
    $("contactRow").addEventListener("click", (event) => {
      const chip = event.target.closest("[data-contact]");
      if (!chip) return;
      const key = chip.dataset.contact;
      if (key === "no_website") {
        state.filters.no_website = !state.filters.no_website;
        if (state.filters.no_website) state.filters.has_website = null;
      } else {
        state.filters[key] = state.filters[key] === true ? null : true;
        if (key === "has_website" && state.filters.has_website === true) state.filters.no_website = false;
      }
      state.page = 1;
      refresh();
    });
    ["minScore", "minRating", "minReviews"].forEach((id) => {
      $(id).addEventListener("input", debounce(() => {
        state.filters[id === "minScore" ? "min_score" : id === "minRating" ? "min_rating" : "min_reviews"] = $(id).value;
        state.page = 1;
        refresh();
      }, 420));
    });
    $("inResults").addEventListener("input", debounce(() => {
      state.filters.q = $("inResults").value.trim();
      state.page = 1;
      refresh();
    }, 380));
    $("selNicheFilter").addEventListener("change", (event) => {
      state.filters.niche_fa = event.target.value;
      state.page = 1;
      refresh();
    });
    $("selStatus").addEventListener("change", (event) => {
      state.filters.status = event.target.value;
      state.page = 1;
      refresh();
    });
    $("selSort").addEventListener("change", (event) => {
      state.filters.sort = event.target.value;
      state.page = 1;
      refresh();
    });
    $("geoOnly").addEventListener("change", async (event) => {
      state.filters.geoOnly = event.target.checked;
      state.page = 1;
      await onAreaChange();
      await refresh();
    });
    $("clearFilters").addEventListener("click", () => {
      state.filters = {
        q: "", segment: "", status: "", gap: "", niche_id: "", niche_fa: "",
        has_phone: null, has_email: null, has_website: null, has_social: null, has_instagram: null,
        no_website: false, min_score: "", min_rating: "", min_reviews: "",
        sort: "score", order: "desc", geoOnly: false,
      };
      ["minScore", "minRating", "minReviews", "inResults"].forEach((id) => { $(id).value = ""; });
      $("selStatus").value = "";
      $("selNicheFilter").value = "";
      $("selSort").value = "score";
      $("geoOnly").checked = false;
      state.page = 1;
      refresh();
      toast("فیلترها پاک شدند.");
    });

    document.querySelectorAll(".exports [data-fmt]").forEach((btn) => {
      btn.addEventListener("click", () => exportAs(btn.dataset.fmt));
    });

    $("tbody").addEventListener("click", (event) => {
      const opener = event.target.closest("[data-open]");
      if (opener) { openLead(opener.dataset.open); return; }
      const row = event.target.closest("tr[data-id]");
      if (row && !event.target.closest("a")) openLead(row.dataset.id);
    });

    $("modalClose").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (event) => { if (event.target === $("modal")) closeModal(); });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeModal();
      if (event.key === "/" && document.activeElement !== $("q") && document.activeElement !== $("inResults")) {
        event.preventDefault();
        $("q").focus();
      }
    });
    $("hideRun").addEventListener("click", () => { $("runCard").hidden = true; });
    $("themeBtn").addEventListener("click", () => {
      const next = state.theme === "dark" ? "light" : "dark";
      applyTheme(next);
      localStorage.setItem("leadgen-mini-theme", next);
    });
  }

  function findProvinceOfCity(cityId) {
    const all = [...(state.regions.provinces || []), ...(state.regions.countries || [])];
    const group = all.find((item) => (item.cities || []).some((city) => city.id === cityId));
    return group ? group.id : "";
  }

  function applyTheme(theme) {
    state.theme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    const btn = $("themeBtn");
    if (btn) btn.textContent = theme === "dark" ? "🌙" : "☀️";
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
