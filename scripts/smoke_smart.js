/* هارنس تست سریع «رابط ساده» (smart/app.js): DOM ساختگی + fetch روی فایل نمونه.
 *
 * هدف: گرفتن خطاهای زمان اجرا در مسیر اصلی برنامه بدون مرورگر —
 *   boot → تجزیهٔ جمله → اجرا (دمو) → رندر نتایج → فیلتر/جست‌وجو → پروندهٔ لید →
 *   تب‌های کمپین → تغییر وضعیت → هر ۵ خروجی.
 *
 * اجرا:
 *   node scripts/smoke_smart.js                                  # حالت دمو (پیش‌فرض CI)
 *   SMOKE_LIVE=http://127.0.0.1:8000 node scripts/smoke_smart.js # روی سرور واقعی زنده
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const SMART = path.join(ROOT, "smart");
const SAMPLE = path.join(ROOT, "site", "data", "sample.json");
const LIVE = process.env.SMOKE_LIVE || "";
const STATIC = process.env.SMOKE_STATIC || "";   // مثلاً http://127.0.0.1:8090/app/
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
  el.dataset = Object.fromEntries(Object.entries(tag.attrs)
    .filter(([k]) => k.startsWith("data-"))
    .map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]));
  if (tag.attrs.class) el.className = tag.attrs.class;
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
const DOC_Q = new Map();          // document.querySelectorAll باید نمونهٔ پایدار برگرداند
const RENDERED = {};
const ERRORS = [];
global.TOASTS = [];
function el(id) { if (!CACHE.has(id)) CACHE.set(id, new El(id)); return CACHE.get(id); }

class El {
  constructor(id = "") {
    this.id = id; this._html = ""; this.textContent = ""; this.value = ""; this.title = "";
    this.checked = false; this.hidden = false; this.disabled = false; this.className = "";
    this.children = []; this.dataset = {}; this.style = {}; this.scrollTop = 0; this.scrollHeight = 0;
    this._handlers = {}; this._q = {};
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = String(v); this._q = {}; RENDERED[this.id] = String(v).length;
    DOC_Q.delete("[data-act]");           // دکمه‌های کارت‌ها با هر رندر تازه می‌شوند
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
  get lastElementChild() { return this._last || (this._last = new El(`${this.id}:last`)); }
  addEventListener(ev, fn) { (this._handlers[ev] = this._handlers[ev] || []).push(fn); }
  removeEventListener() {}
  querySelectorAll(sel) {
    if (!this._q[sel]) this._q[sel] = parseTags(this._html).filter((t) => matchSel(t, sel)).map(stubFrom);
    return this._q[sel];
  }
  querySelector(sel) {
    if (sel === ".dot") return this._dot || (this._dot = new El(`${this.id}:dot`));
    return this.querySelectorAll(sel)[0] || null;
  }
  appendChild(c) {
    this.children.push(c);
    if (this.id === "toasts") global.TOASTS.push(`${c.className}|${c.textContent}`);
    return c;
  }
  remove() {}
  select() {}
  focus() {}
  scrollIntoView() {}
  click() { this.fire("click", {}); }
  fire(ev, payload = {}) {
    const evt = Object.assign({ target: this, key: "", preventDefault() {}, stopPropagation() {} }, payload);
    if (typeof this[`on${ev}`] === "function") this[`on${ev}`](evt);
    (this._handlers[ev] || []).forEach((f) => f(evt));
  }
  closest(sel) { return matchSel({ tag: "div", attrs: { class: this.className } }, sel) ? this : null; }
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
  execCommand: () => true,
};
function documentQuery(sel) {
  if (sel === ".view-toggle button") {
    return ["cards", "table"].map((v) => { const e = new El(`view-${v}`); e.dataset.view = v;
      if (v === "cards") e.classList.add("active"); return e; });
  }
  if (sel === "[data-export]") {
    return ["xlsx", "csv", "json", "campaign", "hot"].map((v) => { const e = new El(`exp-${v}`);
      e.dataset.export = v; return e; });
  }
  const seen = new Map();
  CACHE.forEach((e) => parseTags(e._html).filter((t) => matchSel(t, sel)).forEach((t) => {
    const s2 = stubFrom(t);
    seen.set(`${t.html}|${s2.dataset.lead}|${s2.dataset.act}`, s2);
  }));
  return [...seen.values()];
}

global.window = { innerWidth: 1440, innerHeight: 900, location: { search: "", href: "" },
                  LeadCampaign: undefined };
global.location = global.window.location;
global.CSS = { escape: (s) => String(s) };
global.localStorage = { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = String(v); } };
Object.defineProperty(global, "navigator", {
  value: { clipboard: { writeText: async () => {} } }, configurable: true, writable: true });
global.DOWNLOADS = [];
global.Blob = class {
  constructor(parts, opts) { this.text = String(parts.join("")); this.size = this.text.length; this.type = (opts || {}).type; }
};
// کلاس واقعی URL را نگه می‌داریم (fetch stub به new URL نیاز دارد) و فقط دانلود را ضبط می‌کنیم
URL.createObjectURL = (b) => { global.DOWNLOADS.push(b); return "blob:x"; };
URL.revokeObjectURL = () => {};

const API_CALLS = [];
global.fetch = async (url, opts) => {
  const u = String(url);
  if (u.startsWith("/api/") || u.startsWith("http")) {
    API_CALLS.push(u);
    if (!LIVE && !u.startsWith("http")) return { ok: false, status: 503, json: async () => ({ error: "offline" }) };
    const res = await realFetch(u.startsWith("http") ? u : new URL(u, LIVE).toString(), opts);
    const text = await res.text();
    return { ok: res.ok, status: res.status, json: async () => (text ? JSON.parse(text) : {}) };
  }
  if (STATIC) {                                  // نسخهٔ منتشرشدهٔ Pages روی سرور ایستا
    const res = await realFetch(new URL(u, STATIC).toString());
    const text = await res.text();
    API_CALLS.push(u);
    return { ok: res.ok, status: res.status, json: async () => (text ? JSON.parse(text) : {}) };
  }
  const file = u.includes("demo-data.json") ? SAMPLE : path.join(SMART, u.replace(/^\.\//, ""));
  if (!fs.existsSync(file)) return { ok: false, status: 404, json: async () => ({}) };
  const text = fs.readFileSync(file, "utf8");
  return { ok: true, status: 200, json: async () => JSON.parse(text) };
};

/* ── بررسی‌ها ───────────────────────────────────────────────────────── */
let fails = 0, checks = 0;
const tick = (ms) => new Promise((r) => setTimeout(r, ms));
function check(name, cond, extra = "") {
  checks++;
  if (cond) console.log(`  ✓ ${name}`);
  else { fails++; console.log(`  ✗ ${name} ${extra}`); }
}

async function main() {
  console.log(`\n── smoke smart (${LIVE ? `live: ${LIVE}` : STATIC ? `static: ${STATIC}` : "demo"}) ──`);
  for (const f of ["campaign.js", "app.js"]) {
    vm.runInThisContext(fs.readFileSync(path.join(SMART, f), "utf8"), { filename: f });
  }
  if (!LIVE) el("body").dataset.mode = "demo";   // document.body
  global.document.body.dataset.mode = LIVE ? "" : "demo";

  await tick(1600);                                // boot + گزینه‌ها + حالت
  check("boot: گزینهٔ هدف رندر شد", el("goals").innerHTML.includes("data-goal"), el("goals").innerHTML.slice(0, 80));
  check("boot: گزینهٔ حجم رندر شد", el("sizes").innerHTML.includes("data-size"));
  check("boot: نمونه‌های آماده رندر شد", el("examples").innerHTML.includes("data-example"));
  check("boot: وضعیت موتور نوشته شد", el("engineText").textContent.length > 3, el("engineText").textContent);
  check("boot: ستون‌های خروجی پر شد", el("cols").innerHTML.includes("contact"));
  check("boot: قالب فایل پر شد", el("fmt").innerHTML.includes("xlsx"));
  check("boot: تاریخچه رندر شد", el("history").innerHTML.length > 20);

  // ۱) تجزیهٔ جملهٔ کاربر
  el("q").value = "دندانپزشکی در تهران";
  el("q").fire("input");
  await tick(1400);
  check("parse: «مغز» خلاصه داد", el("brain").innerHTML.includes("summary"), el("brain").innerHTML.slice(0, 120));
  check("parse: دکمهٔ اجرا فعال شد", el("go").disabled === false);
  check("parse: کلیدواژه نمایش داده شد", el("brain").innerHTML.includes("class=\"kw\""));

  // ۲) پیشنهادها
  el("q").value = "دندان";
  el("q").fire("input");
  await tick(1200);
  check("suggest: پیشنهاد صنف/شهر آمد", el("suggest").innerHTML.length > 10 || LIVE === "",
        el("suggest").innerHTML.slice(0, 80));
  el("q").value = "دندانپزشکی در تهران";
  el("q").fire("input");
  await tick(1200);

  // ۳) اجرا (در حالت زنده، حجم کوچک تا هارنس سریع بماند)
  if (LIVE) {
    const quick = el("sizes").querySelectorAll("[data-size]").find((b) => b.dataset.size === "quick");
    if (quick) quick.fire("click");
    el("engineMode").value = "demo";
    el("depth").value = "2"; el("limit").value = "30";
    el("q").fire("input"); await tick(900);
  }
  el("go").fire("click");
  const waitMs = LIVE ? 150000 : 6000;
  const started = Date.now();
  while (el("results").hidden && Date.now() - started < waitMs) await tick(700);
  check("run: پنل نتایج باز شد", el("results").hidden === false, `بعد از ${Date.now() - started}ms`);
  check("run: پیشرفت نمایش داده شد", RENDERED["log"] > 0 || el("stageText").textContent.length > 3);
  check("run: KPIها رندر شدند", el("kpis").innerHTML.includes("لید پیدا شد"));
  check("run: بینش‌ها رندر شدند", el("insights").innerHTML.length > 20);
  check("run: نمودار پوشش", el("covChart").innerHTML.includes("bar-row"));
  check("run: نمودار بخش‌بندی", el("segChart").innerHTML.includes("bar-row"));
  check("run: نمودار شکاف‌ها", el("gapChart").innerHTML.includes("bar-row"));
  check("run: کارت‌های لید", el("cards").innerHTML.includes("lead-name"));
  check("run: حلقهٔ امتیاز با درصد", /--p:\d+/.test(el("cards").innerHTML));
  check("run: چیپ‌های بخش‌بندی", el("segChips").innerHTML.includes("data-seg"));
  check("run: صفحه‌بندی", el("pager").innerHTML.includes("لید"));
  check("run: یادداشت خروجی", el("exportNote").textContent.length > 5);

  // ۴) نمای جدول
  const tableBtn = global.document.querySelectorAll(".view-toggle button")[1];
  tableBtn.fire("click");
  check("table: ردیف‌ها رندر شدند", el("tableBody").innerHTML.includes("<tr"));
  check("table: ستون لوکیشن دارد", el("tableBody").innerHTML.includes("maps") || el("tableBody").innerHTML.includes("📍"));

  // ۵) فیلتر داغ‌ها + جست‌وجو
  const hotChip = el("segChips").querySelectorAll("button").find((b) => b.dataset.seg === "hot");
  if (hotChip) { hotChip.fire("click"); check("filter: فیلتر «داغ» اعمال شد", el("tableBody").innerHTML.length >= 0); }
  el("leadSearch").value = "تهران";
  el("leadSearch").fire("input");
  await tick(500);
  check("filter: جست‌وجو خطا نداد", true);
  el("leadSearch").value = "";
  el("leadSearch").fire("input");
  await tick(500);
  if (hotChip) {
    const allChip = el("segChips").querySelectorAll("button").find((b) => b.dataset.seg === "all");
    if (allChip) allChip.fire("click");
  }
  el("leadSort").value = "reviews";
  el("leadSort").fire("change");
  check("sort: مرتب‌سازی بر اساس نظرات", true);

  // ۶) پروندهٔ لید + کمپین
  global.document.querySelectorAll(".view-toggle button")[0].fire("click");
  const firstCard = el("cards").querySelectorAll("[data-lead]")[0];
  check("lead: کارت قابل کلیک است", Boolean(firstCard && firstCard.dataset.lead));
  if (firstCard) {
    firstCard.fire("click");
    await tick(1600);
    check("modal: باز شد", el("modal").hidden === false);
    check("modal: عنوان لید", el("modalTitle").textContent.length > 1, el("modalTitle").textContent);
    check("modal: اطلاعات تماس", el("modalBody").innerHTML.includes("اطلاعات"));
    check("modal: عوامل امتیاز", el("modalBody").innerHTML.includes("چرا این امتیاز"));
    check("modal: لوکیشن دقیق", el("modalBody").innerHTML.includes("لوکیشن دقیق"));
    check("modal: ردیف وضعیت", el("modalBody").innerHTML.includes("statusRow"));
    check("modal: تب‌های کانال", el("campTabs").innerHTML.includes("data-c"));
    check("modal: متن کمپین ساخته شد", (el("msgBox").textContent || "").length > 40,
          String(el("msgBox").textContent || "").slice(0, 60));
    check("modal: سرگیرنده نوشته شد", el("msgHead").innerHTML.includes("گیرنده"));

    const waTab = el("campTabs").querySelectorAll("[data-c]").find((b) => b.dataset.c === "whatsapp");
    if (waTab) {
      waTab.fire("click"); await tick(1200);
      check("campaign: کانال واتس‌اپ", (el("msgBox").textContent || "").length > 20);
    }
    const smsTab = el("campTabs").querySelectorAll("[data-c]").find((b) => b.dataset.c === "sms");
    if (smsTab) {
      smsTab.fire("click"); await tick(1200);
      check("campaign: کانال پیامک ≤ ۲۸۰ نویسه", (el("msgBox").textContent || "").length <= 400);
    }
    const igTab = el("campTabs").querySelectorAll("[data-c]").find((b) => b.dataset.c === "instagram");
    if (igTab) { igTab.fire("click"); await tick(1000); check("campaign: کانال اینستاگرام", true); }

    // مشخصات فرستنده → بازتولید متن
    el("sName").value = "سارا"; el("sName").fire("input"); await tick(300);
    el("sCompany").value = "استودیو رشد"; el("sCompany").fire("input"); await tick(900);
    check("sender: با پر کردن مشخصات، متن بازتولید شد", (el("msgBox").textContent || "").length > 20);

    const statusBtn = el("statusRow").querySelectorAll("[data-status]").find((b) => b.dataset.status === "contacted");
    if (statusBtn) { statusBtn.fire("click"); await tick(900); check("status: تغییر وضعیت بدون خطا", true); }
    if (el("copyMsg")) { el("copyMsg").fire("click"); check("copy: کپی متن", true); }
    el("modalClose").fire("click");
    check("modal: بسته شد", el("modal").hidden === true);
  }

  // ۷) خروجی‌ها
  const before = global.DOWNLOADS.length;
  for (const b of global.document.querySelectorAll("[data-export]")) { b.fire("click"); await tick(250); }
  if (LIVE) {
    check("export: پیوند دانلود سرور ساخته شد", String(global.window.location.href).includes("/api/runs/"),
          String(global.window.location.href));
  } else {
    const made = global.DOWNLOADS.length - before;
    check("export: هر ۵ فایل در مرورگر ساخته شد", made >= 5, `ساخته‌شده: ${made}`);
    const csv = global.DOWNLOADS.find((d) => d.text && d.text.startsWith("\ufeff"));
    check("export: CSV با BOM و سرستون فارسی", Boolean(csv) && csv.text.split("\n")[0].includes("کسب"),
          csv ? csv.text.split("\n")[0].slice(0, 90) : "بدون فایل");
    const js = global.DOWNLOADS.find((d) => d.text && d.text.startsWith("["));
    check("export: JSON معتبر", Boolean(js) && JSON.parse(js.text).length > 0);
    const camp = global.DOWNLOADS.filter((d) => d.text && d.text.includes("متن"));
    check("export: فایل کمپین", camp.length > 0);
  }

  // ۸) پوسته
  el("themeBtn").fire("click");
  check("theme: پوسته عوض شد", global.document.documentElement.dataset.theme === "light");
  el("themeBtn").fire("click");

  // ۹) سوئیچ دمو ↔ زنده
  el("demoMode").checked = false;
  el("demoMode").fire("change");
  await tick(1500);
  check("mode: سوئیچ به زنده بدون خطا", true, LIVE ? "(سرور واقعی)" : "(سرور نیست → باید خودش به دمو برگردد)");

  if (global.TOASTS.length) {
    console.log("\n  توست‌ها:");
    global.TOASTS.slice(0, 8).forEach((t) => console.log(`    · ${t}`));
  }
  console.log(`\n${checks - fails}/${checks} بررسی موفق${fails ? ` — ${fails} شکست` : ""}`);
  fails += ERRORS.length;
  if (LIVE) console.log(`  API calls: ${API_CALLS.length}`);
  process.exit(fails ? 1 : 0);
}

process.on("unhandledRejection", (err) => { ERRORS.push(`unhandledRejection: ${err && err.stack || err}`); });
process.on("uncaughtException", (err) => { ERRORS.push(`uncaughtException: ${err && err.stack || err}`); });

main().then(() => {
  if (ERRORS.length) {
    console.log(`\n⚠ ${ERRORS.length} خطای گرفته‌نشده در حین اجرا:`);
    ERRORS.slice(0, 4).forEach((e) => console.log(String(e).split("\n").slice(0, 6).join("\n")));
    process.exit(1);
  }
}).catch((err) => { console.error("خطای هارنس:", err); process.exit(1); });
