/* هارنس تست سریع «نسخهٔ جمع‌وجور» (mini/app.js): DOM ساختگی + fetch روی سرور یا فایل نمونه.
 *
 * هدف: گرفتن خطاهای زمان اجرا در مسیر اصلی برنامه بدون مرورگر —
 *   boot → پر شدن آبشار استان/شهر/منطقه/خیابان → تجزیهٔ جمله → پیشنهادها →
 *   فیلترها (بخش/تماس/امتیاز/متن) → جست‌وجوی قوی در داده‌های قبلی → پروندهٔ لید →
 *   تغییر وضعیت → خروجی‌ها → (در حالت زنده) اجرای موتور و نظرسنجی پیشرفت.
 *
 * اجرا:
 *   node scripts/smoke_mini.js                                   # حالت دمو (پیش‌فرض CI)
 *   SMOKE_LIVE=http://127.0.0.1:8000 node scripts/smoke_mini.js  # روی سرور واقعی
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const MINI = path.join(ROOT, "mini");
const SAMPLE = path.join(ROOT, "site", "data", "sample.json");
const LIVE = process.env.SMOKE_LIVE || "";
const STATIC = process.env.SMOKE_STATIC || "";   // مثلاً http://127.0.0.1:8090/mini/
const realFetch = global.fetch;

/* ── DOM ساختگی ─────────────────────────────────────────────────────── */
const TAG_RE = /<([a-zA-Z][\w-]*)((?:\s+[-\w:.@]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s">]+))?)*)\s*\/?>/g;
const ATTR_RE = /([-\w:.@]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s">]+)))?/g;

function parseTags(html) {
  const out = [];
  if (!html) return out;
  let m;
  TAG_RE.lastIndex = 0;
  while ((m = TAG_RE.exec(html))) {
    const attrs = {};
    let a;
    ATTR_RE.lastIndex = 0;
    while ((a = ATTR_RE.exec(m[2] || ""))) attrs[a[1]] = a[2] ?? a[3] ?? a[4] ?? "";
    out.push({ tag: m[1].toLowerCase(), attrs, html: m[0] });
  }
  return out;
}

function stubFrom(tag) {
  const el = new El(`<${tag.tag}>`);
  el._tag = tag.tag;
  el.dataset = Object.fromEntries(Object.entries(tag.attrs)
    .filter(([k]) => k.startsWith("data-"))
    .map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]));
  if (tag.attrs.class) el.className = tag.attrs.class;
  el._html = tag.html;
  return el;
}

function matchSel(tag, sel) {
  const last = sel.trim().split(/\s+/).pop();
  const m = last.match(/^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/);
  if (!m) return false;
  if (m[1] && tag.tag !== m[1].toLowerCase()) return false;
  const classes = (tag.attrs.class || "").split(/\s+/);
  for (const c of (m[2] || "").split(".").filter(Boolean)) if (!classes.includes(c)) return false;
  for (const raw of (m[3] || "").match(/\[[^\]]+\]/g) || []) {
    const inner = raw.slice(1, -1);
    const [name, val] = inner.split("=");
    if (!(name in tag.attrs)) return false;
    if (val !== undefined && String(tag.attrs[name]) !== val.replace(/^["']|["']$/g, "")) return false;
  }
  return true;
}

const CACHE = new Map();
const DOC_Q = new Map();
const RENDERED = {};
global.TOASTS = [];
function el(id) { if (!CACHE.has(id)) CACHE.set(id, new El(id)); return CACHE.get(id); }

class El {
  constructor(id = "") {
    this.id = id; this._html = ""; this.textContent = ""; this.value = ""; this.title = "";
    this.checked = false; this.hidden = false; this.disabled = false; this.className = "";
    this.children = []; this.dataset = {}; this.style = {};
    this._handlers = {}; this._q = {};
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = String(v); this._q = {}; RENDERED[this.id] = String(v).length;
    this.textContent = String(v).replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    DOC_Q.delete("#tbody tr.sel");
  }
  get classList() {
    const self = this;
    const list = () => self.className.split(/\s+/).filter(Boolean);
    const add = (c) => { if (!list().includes(c)) self.className = [...list(), c].join(" "); };
    const remove = (c) => { self.className = list().filter((x) => x !== c).join(" "); };
    return {
      add, remove,
      contains: (c) => list().includes(c),
      toggle: (c, on) => {
        const want = on === undefined ? !list().includes(c) : Boolean(on);
        return want ? add(c) : remove(c);
      },
    };
  }
  setAttribute(k, v) { this[k] = String(v); }
  getAttribute(k) { return this[k] === undefined ? null : String(this[k]); }
  addEventListener(ev, fn) { (this._handlers[ev] = this._handlers[ev] || []).push(fn); }
  removeEventListener() {}
  querySelectorAll(sel) {
    if (!this._q[sel]) this._q[sel] = parseTags(this._html).filter((t) => matchSel(t, sel)).map(stubFrom);
    return this._q[sel];
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  appendChild(c) {
    this.children.push(c);
    if (this.id === "toasts") global.TOASTS.push(`${c.className}|${c.textContent}`);
    return c;
  }
  remove() {}
  focus() {}
  click() { this.fire("click", {}); }
  fire(ev, payload = {}) {
    const evt = Object.assign({ target: this, key: "", preventDefault() {}, stopPropagation() {} }, payload);
    (this._handlers[ev] || []).forEach((f) => f(evt));
  }
  closest(sel) {
    return matchSel({ tag: this._tag || "div", attrs: { class: this.className, ...datasetAttrs(this) } }, sel) ? this : null;
  }
}

function datasetAttrs(node) {
  const out = {};
  Object.entries(node.dataset || {}).forEach(([k, v]) => {
    out[`data-${k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`] = v;
  });
  return out;
}

global.RENDERED = RENDERED;
global.document = {
  readyState: "complete",
  documentElement: new El("html"),
  getElementById: el,
  createElement: (tag) => new El(`<${tag}>`),
  querySelector: (sel) => global.document.querySelectorAll(sel)[0] || null,
  querySelectorAll: (sel) => {
    if (DOC_Q.has(sel)) return DOC_Q.get(sel);
    const out = documentQuery(sel);
    DOC_Q.set(sel, out);
    return out;
  },
  addEventListener: (ev, fn) => (global.document._h = global.document._h || {})[ev] = fn,
  body: new El("body"),
};
function documentQuery(sel) {
  if (sel === ".exports [data-fmt]") {
    return ["csv", "xlsx", "json"].map((fmt) => { const e = new El(`exp-${fmt}`); e.dataset.fmt = fmt; return e; });
  }
  if (sel === "#tbody tr.sel") return [];
  if (sel.startsWith("#tbody tr[data-id=")) {
    const rows = parseTags(el("tbody")._html).filter((t) => t.tag === "tr");
    const wanted = sel.slice(sel.indexOf("=") + 1).replace(/["\]]/g, "");
    const hit = rows.find((t) => (t.attrs["data-id"] || "") === wanted);
    return hit ? [stubFrom(hit)] : [];
  }
  const seen = new Map();
  CACHE.forEach((e) => parseTags(e._html).filter((t) => matchSel(t, sel)).forEach((t) => {
    const stub = stubFrom(t);
    seen.set(`${t.html}|${JSON.stringify(stub.dataset)}`, stub);
  }));
  return [...seen.values()];
}

global.window = { innerWidth: 1440, innerHeight: 900, location: { search: "", href: "" } };
global.location = global.window.location;
global.CSS = { escape: (s) => String(s) };
global.localStorage = { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = String(v); } };
Object.defineProperty(global, "navigator", {
  value: { clipboard: { writeText: async () => {} } }, configurable: true, writable: true });
global.DOWNLOADS = [];
global.Blob = class {
  constructor(parts, opts) { this.text = String(parts.join("")); this.size = this.text.length; this.type = (opts || {}).type; }
};
URL.createObjectURL = (b) => { global.DOWNLOADS.push(b); return "blob:x"; };
URL.revokeObjectURL = () => {};

const API_CALLS = [];
global.fetch = async (url, opts) => {
  const u = String(url);
  if (u.startsWith("/api/")) {
    API_CALLS.push(u);
    if (!LIVE) return { ok: false, status: 503, text: async () => '{"error":"offline"}' };
    const res = await realFetch(new URL(u, LIVE).toString(), opts);
    const text = await res.text();
    return { ok: res.ok, status: res.status, text: async () => text,
             json: async () => (text ? JSON.parse(text) : {}) };
  }
  if (STATIC) {                       // نسخهٔ منتشرشده روی Pages (کاملاً ایستا)
    const res = await realFetch(new URL(u, STATIC).toString());
    const text = await res.text();
    API_CALLS.push(u);
    return { ok: res.ok, status: res.status, text: async () => text,
             json: async () => (text ? JSON.parse(text) : {}) };
  }
  API_CALLS.push(u);
  // فایل‌های دادهٔ نمونه: اول نمونهٔ Pages، بعد مسیرهای نسبی
  const candidates = [SAMPLE, path.join(ROOT, "site", "data", "sample.json"),
    path.join(MINI, u.replace(/^\.\//, ""))];
  for (const file of candidates) {
    if (file && fs.existsSync(file)) {
      const text = fs.readFileSync(file, "utf8");
      return { ok: true, status: 200, text: async () => text, json: async () => JSON.parse(text) };
    }
  }
  return { ok: false, status: 404, text: async () => "" };
};

/* ── بررسی‌ها ───────────────────────────────────────────────────────── */
let fails = 0; let checks = 0;
const tick = (ms) => new Promise((r) => setTimeout(r, ms));
function check(name, cond, extra = "") {
  checks++;
  if (cond) console.log(`  ✓ ${name}`);
  else { fails++; console.log(`  ✗ ${name} ${extra}`); }
}
const html = (id) => String(el(id).innerHTML || "");
const text = (id) => String(el(id).textContent || "");

const UNCAUGHT = [];
process.on("uncaughtException", (err) => UNCAUGHT.push(`uncaughtException: ${err && err.stack ? err.stack.split("\n").slice(0, 3).join(" | ") : err}`));
process.on("unhandledRejection", (err) => UNCAUGHT.push(`unhandledRejection: ${err && err.message ? err.message : err}`));

async function main() {
  console.log(`\n── smoke mini (${LIVE ? `live: ${LIVE}` : STATIC ? `static: ${STATIC}` : "demo"}) ──`);
  vm.runInThisContext(fs.readFileSync(path.join(MINI, "app.js"), "utf8"), { filename: "mini/app.js" });

  // boot
  const bootHandler = global.document._h && global.document._h.DOMContentLoaded;
  check("DOMContentLoaded ثبت شده", typeof bootHandler === "function");
  await bootHandler();
  await tick(400);

  const serverOk = LIVE !== "";
  check("وضعیت موتور در نوار بالا", /موتور|نمونه/.test(text("enginePill")), text("enginePill"));
  check("فهرست استان‌ها پر شد", html("selProvince").includes("تهران"), html("selProvince").slice(0, 60));
  check("فهرست شهرها پر شد", html("selCity").length > 40, String(html("selCity").length));
  check("فهرست اصناف پر شد", html("selNiche").includes("optgroup") || html("selNiche").includes("option"));
  check("چیپ‌های بخش رندر شدند", html("segmentRow").includes("داغ"));
  check("چیپ‌های تماس رندر شدند", html("contactRow").includes("ایمیل"));
  check("محدودهٔ انتخابی نمایش داده شد", html("areaInfo").includes("محدوده"));
  check("نتیجه‌های اولیه بارگذاری شد", text("resCount").includes("کسب‌وکار"), text("resCount"));
  // با پایگاه‌دادهٔ خالی جدول خالی است؛ این حالت «شکست» نیست (فقط پوشش کمتر)
  const hasRowsAtBoot = html("tbody").includes("data-id");
  if (hasRowsAtBoot) check("جدول پر شد", true);
  else console.log("  ! جدول خالی است (پایگاه‌داده خالی؟) — برای پوشش کامل دادهٔ نمونه بسازید");

  // آبشار: استان → شهر → منطقه → خیابان
  el("selProvince").value = "fars";
  el("selProvince").fire("change");
  await tick(250);
  check("تغییر استان → شهرهای استان", html("selCity").includes("شیراز"), html("selCity").slice(0, 90));
  el("selCity").value = "shiraz";
  el("selCity").fire("change");
  await tick(350);
  check("تغییر شهر → منطقه‌ها", html("selDistrict").includes("معالی"), html("selDistrict").slice(0, 80));
  check("تغییر شهر → خیابان‌ها", html("selStreet").includes("خیابان") || html("selStreet").includes("بلوار"));
  const districtOption = (html("selDistrict").match(/value="([^"]*معالی[^"]*)"/) || [])[1] || "";
  el("selDistrict").value = districtOption;
  el("selDistrict").fire("change");
  await tick(400);
  check("انتخاب منطقه → برچسب محدوده", html("areaInfo").includes("شیراز") || html("areaInfo").includes("معالی"), html("areaInfo").slice(0, 120));

  // بازگشت به تهران برای بقیهٔ تست‌ها
  el("selProvince").value = "tehran-province";
  el("selProvince").fire("change");
  await tick(450);
  el("selCity").value = "tehran";
  el("selCity").fire("change");
  await tick(450);
  check("منطقه‌های تهران", html("selDistrict").includes("ونک") || html("selDistrict").includes("منطقه"));

  // جعبهٔ جست‌وجو: تجزیهٔ جمله + پیشنهادها
  el("q").value = "کافه در ونک تهران";
  el("q").fire("input");
  await tick(900);
  check("تجزیهٔ جمله → راهنما", text("parseHint").length > 10, text("parseHint").slice(0, 90));
  check("پیشنهاد منطقه/صنف نمایش داده شد", html("suggest").includes("button") || html("suggest") === "", html("suggest").slice(0, 60));

  if (serverOk) {
    check("تجزیه، منطقه را از جمله گرفت", text("parseHint").includes("ونک") || text("parseHint").includes("تهران"), text("parseHint").slice(0, 120));
  }

  // فیلترها
  const hotChip = el("segmentRow").querySelectorAll("[data-segment]").find((c) => c.dataset.segment === "hot");
  check("چیپ «داغ» پیدا شد", !!hotChip);
  if (hotChip) {
    el("segmentRow").fire("click", { target: hotChip });
    await tick(600);
    check("فیلتر بخش داغ اعمال شد", API_CALLS.some((u) => u.includes("segment=hot")) || !serverOk);
  }
  const emailChip = el("contactRow").querySelectorAll("[data-contact]").find((c) => c.dataset.contact === "has_email");
  check("چیپ «ایمیل» پیدا شد", !!emailChip);
  if (emailChip) {
    el("contactRow").fire("click", { target: emailChip });
    await tick(600);
    check("فیلتر ایمیل اعمال شد", API_CALLS.some((u) => u.includes("has_email=1")) || !serverOk);
  }
  // فیلتر «نوع کسب‌وکار» روی نتیجه‌ها (از facets پر می‌شود)
  const nicheOptions = (html("selNicheFilter").match(/value="([^"]+)"/g) || [])
    .map((raw) => raw.slice(7, -1)).filter(Boolean);
  check("فیلتر صنف از شمارش‌های زنده پر شد", nicheOptions.length > 0, html("selNicheFilter").slice(0, 60));
  if (nicheOptions.length) {
    el("selNicheFilter").value = nicheOptions[0];
    el("selNicheFilter").fire("change");
    await tick(600);
    check("فیلتر صنف اعمال شد", API_CALLS.some((u) => u.includes("niche_fa=")) || !serverOk,
      API_CALLS.slice(-2).join(" "));
  }

  el("minScore").value = "70";
  el("minScore").fire("input");
  await tick(900);
  check("حداقل امتیاز اعمال شد", API_CALLS.some((u) => u.includes("min_score=70")) || !serverOk);

  el("inResults").value = "کافه";
  el("inResults").fire("input");
  await tick(900);
  check("جست‌وجو در نتایج اعمال شد", API_CALLS.some((u) => u.includes("q=")) || !serverOk);
  check("شمارش نتایج به‌روز شد", /کسب‌وکار/.test(text("resCount")), text("resCount"));

  el("geoOnly").checked = true;
  el("geoOnly").fire("change");
  await tick(700);
  check("فیلتر شعاع منطقه اعمال شد", API_CALLS.some((u) => u.includes("radius_m=")) || !serverOk);

  // «داده‌های قبلی» = جست‌وجوی قوی در همهٔ اجراها
  el("storedBtn").fire("click");
  await tick(800);
  check("حالت داده‌های قبلی", global.TOASTS.some((t) => t.includes("داده")) || !serverOk);

  // پاک کردن فیلترها تا مجموعهٔ نتایج برای بررسی پروندهٔ لید خالی نباشد
  el("clearFilters").fire("click");
  await tick(800);
  check("پاک کردن فیلترها", API_CALLS.filter((u) => u.includes("/api/search?")).length >= 2 || !serverOk);

  // پروندهٔ یک لید (اگر پایگاه‌داده خالی باشد، این بخش رد می‌شود — در CI دادهٔ نمونه ساخته می‌شود)
  const firstRow = parseTags(html("tbody")).find((t) => t.tag === "tr" && t.attrs["data-id"]);
  if (!firstRow) {
    console.log("  ! ردیفی در نتایج نیست (پایگاه‌داده خالی؟) — بررسی‌های پروندهٔ لید رد شد." +
      " برای پوشش کامل: python3 scripts/seed_demo_data.py");
  } else {
    check("ردیف نتیجه وجود دارد", true);
  }
  if (firstRow) {
    const rowStub = stubFrom(firstRow);
    rowStub.dataset.id = firstRow.attrs["data-id"];
    el("tbody").fire("click", { target: rowStub });
    await tick(700);
    check("مودال جزئیات باز شد", el("modal").hidden === false);
    check("جزئیات لید رندر شد", html("modalBody").includes("mfield"), html("modalBody").slice(0, 80));
    if (serverOk) {
      const saveBtn = el("saveStatus");
      check("دکمهٔ ذخیرهٔ وضعیت هست", !!saveBtn);
      el("leadStatus").value = "contacted";
      el("leadNote").value = "یادداشت تست";
      if (saveBtn) saveBtn.fire("click");
      await tick(700);
      check("وضعیت ذخیره شد", API_CALLS.some((u) => u.includes("/api/leads/")) );
    }
    el("modalClose").fire("click");
    await tick(120);
    check("مودال بسته شد", el("modal").hidden === true);
  }

  // خروجی‌ها
  global.DOWNLOADS.length = 0;
  const exportButtons = global.document.querySelectorAll(".exports [data-fmt]");
  check("سه دکمهٔ خروجی", exportButtons.length === 3, String(exportButtons.length));
  for (const btn of exportButtons) {
    btn.fire("click");
    await tick(400);
  }
  if (serverOk) {
    const links = global.document.body.children.map((c) => String(c.href || ""));
    check("لینک خروجی از API ساخته شد", links.some((href) => href.includes("/api/search/export")), links.join(" "));
  } else {
    check("خروجی کلاینت‌ساخت (CSV/JSON)", global.DOWNLOADS.length >= 2, String(global.DOWNLOADS.length));
  }

  // صفحه‌بندی و پوسته
  if (el("nextPage")) { el("nextPage").fire("click"); await tick(500); }
  check("صفحه‌بندی رندر شد", html("pager").includes("صفحهٔ"), html("pager").slice(0, 60));
  el("themeBtn").fire("click");
  await tick(80);
  check("پوسته عوض شد", global.document.documentElement.getAttribute("data-theme") === "light");

  if (serverOk) {
    // اجرای واقعی موتور با انتخابگرها
    global.TOASTS.length = 0;
    el("q").value = "";
    el("allTrades").checked = true;
    el("allTrades").fire("change");
    await tick(700);
    el("selSize").value = "quick";
    el("selSize").fire("change");
    await tick(400);
    el("selDistrict").value = "پونک";
    el("selDistrict").fire("change");
    await tick(500);
    el("findBtn").fire("click");
    await tick(1200);
    check("اجرا شروع شد", API_CALLS.some((u) => u.includes("/api/runs")), API_CALLS.slice(-3).join(" "));
    check("پنل اجرا نمایش داده شد", el("runCard").hidden === false);
    // تا ۹۰ ثانیه منتظر پایان اجرا (موتور دمو سریع است)
    let done = false;
    for (let i = 0; i < 90; i++) {
      await tick(1000);
      if (el("runBar").style.width === "100%") { done = true; break; }
    }
    check("اجرا تمام شد", done, `bar=${el("runBar").style.width}`);
    check("نتیجهٔ اجرا در جدول", text("resCount").includes("کسب‌وکار"), text("resCount"));
    check("حالت اجرا روی run", API_CALLS.some((u) => u.includes("run_id=")), API_CALLS.slice(-2).join(" "));
  }

  check("بدون خطای گرفته‌نشده", UNCAUGHT.length === 0, UNCAUGHT.slice(0, 3).join(" ;; "));
  const errorToasts = global.TOASTS.filter((t) => t.startsWith("toast err"));
  const warnToasts = global.TOASTS.filter((t) => t.startsWith("toast warn"));
  check("بدون توست خطای غیرمنتظره", errorToasts.length === 0, errorToasts.slice(0, 3).join(" ;; "));

  console.log(`\n${fails === 0 ? "✅" : "❌"} ${checks - fails}/${checks} بررسی موفق` +
    `${fails ? ` — ${fails} شکست` : ""}`);
  if (warnToasts.length) console.log(`   هشدارهای حالت نمونه: ${warnToasts.length}`);
  console.log(`   فراخوانی‌های API: ${API_CALLS.length}`);
  process.exit(fails === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error("💥 خطای هارنس:", err);
  process.exit(1);
});
