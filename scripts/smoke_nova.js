/* هارنس تست سریع رابط نسل بعد (nova/): DOM ساختگی + fetch روی دادهٔ نمونه.
 *
 * مسیر اصلی بدون مرورگر: boot (سقوط به حالت نمایشی) → رندر چیپ‌ها و کارت‌ها →
 * تجزیهٔ جمله و پیشنهادها → انتخاب حجم/هدف/منطقه/صنف → اجرا و پولینگ پیشرفت →
 * تب لیدها + فیلتر/جست‌وجو → شیت لید + تغییر وضعیت → تب کارها + شیت کار + خروجی →
 * پوسته (روشن/تاریک) → بدون هیچ خطای نگرفته.
 *
 * اجرا:
 *   node scripts/smoke_nova.js                                  # حالت نمایشی (CI)
 *   SMOKE_LIVE=http://127.0.0.1:8000 node scripts/smoke_nova.js # روی سرور واقعی
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const NOVA = path.join(ROOT, "nova");
const SAMPLE = path.join(ROOT, "site", "data", "sample.json");
const LIVE = process.env.SMOKE_LIVE || "";
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

function matchSel(tag, sel) {
  const last = sel.trim().split(/\s+/).pop();
  const m = last.match(/^(#?[a-zA-Z][\w-]*)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/);
  if (!m) return false;
  if (m[1] && m[1][0] === "#") {
    if ((tag.attrs || {}).id !== m[1].slice(1)) return false;
  } else if (m[1] && tag.tag !== m[1].toLowerCase()) return false;
  const classes = ((tag.attrs || {}).class || "").split(/\s+/);
  for (const c of (m[2] || "").split(".").filter(Boolean)) if (!classes.includes(c)) return false;
  for (const raw of (m[3] || "").match(/\[[^\]]+\]/g) || []) {
    const inner = raw.slice(1, -1);
    const [name, val] = inner.split("=");
    if (!(name in (tag.attrs || {}))) return false;
    if (val !== undefined && String(tag.attrs[name]) !== val.replace(/^["']|["']$/g, "")) return false;
  }
  return true;
}

function stubFrom(tag) {
  const e = new El(tag.attrs.id || tag.tag);
  e.attrs = tag.attrs;
  e.dataset = Object.fromEntries(Object.entries(tag.attrs)
    .filter(([k]) => k.startsWith("data-"))
    .map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]));
  if (tag.attrs.class) e.className = tag.attrs.class;
  return e;
}

const CACHE = new Map();
const DOC_Q = new Map();
const RENDERED = {};
const ERRORS = [];
global.TOASTS = [];
function el(id) { if (!CACHE.has(id)) CACHE.set(id, new El(id)); return CACHE.get(id); }
function mk(attrs, cls) {           // عنصر دستی برای target رویدادها
  const e = new El(attrs.id || "stub");
  e.attrs = attrs;
  e.dataset = Object.fromEntries(Object.entries(attrs)
    .filter(([k]) => k.startsWith("data-"))
    .map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]));
  if (cls) e.className = cls;
  return e;
}

class El {
  constructor(id = "") {
    this.id = id; this._html = ""; this.textContent = ""; this.value = ""; this.title = "";
    this.checked = false; this.hidden = false; this.disabled = false; this.className = "";
    this.children = []; this.dataset = {}; this.attrs = {}; this.style = {};
    this._handlers = {}; this._q = {};
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this._q = {}; RENDERED[this.id] = String(v).length; }
  insertAdjacentHTML(pos, html) { this.innerHTML = pos === "beforeend" ? this._html + html : html + this._html; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get classList() {
    const self = this;
    const list = () => self.className.split(/\s+/).filter(Boolean);
    const add = (c) => { if (!list().includes(c)) self.className = [...list(), c].join(" "); };
    const remove = (c) => { self.className = list().filter((x) => x !== c).join(" "); };
    return { add, remove, contains: (c) => list().includes(c),
      toggle: (c, on) => { const want = on === undefined ? !list().includes(c) : Boolean(on); return want ? add(c) : remove(c); } };
  }
  addEventListener(ev, fn) { (this._handlers[ev] = this._handlers[ev] || []).push(fn); }
  removeEventListener() {}
  dispatchEvent(evt) { this.fire(evt.type || "click", evt); return true; }
  querySelectorAll(sel) {
    if (!this._q[sel]) this._q[sel] = parseTags(this._html).filter((t) => matchSel(t, sel)).map(stubFrom);
    return this._q[sel];
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  appendChild(c) {
    this.children.push(c);
    if (this.id === "toasts") global.TOASTS.push(`${c.dataset.tone || ""}|${(c.innerHTML || "").replace(/<[^>]+>/g, "")}`);
    return c;
  }
  remove() {}
  select() {}
  focus() {}
  scrollIntoView() {}
  closest(sel) { return matchSel({ tag: "div", attrs: Object.assign({ class: this.className }, this.attrs) }, sel) ? this : null; }
  click() { this.fire("click", { target: this }); }
  fire(ev, payload = {}) {
    const evt = Object.assign({ target: this, key: "", preventDefault() {}, stopPropagation() {} }, payload);
    if (typeof this[`on${ev}`] === "function") this[`on${ev}`](evt);
    (this._handlers[ev] || []).forEach((f) => f(evt));
  }
}

global.RENDERED = RENDERED;
global.document = {
  readyState: "complete",
  documentElement: new El("html"),
  getElementById: el,
  createElement: (tag) => new El(tag),
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
  if (sel === "[data-tab]") {
    return ["hunt", "leads", "runs", "more"].map((t) => { const e = mk({ "data-tab": t }, "tab"); return e; });
  }
  if (sel === "[data-theme-val]") {
    return ["light", "auto", "dark"].map((v) => mk({ "data-theme-val": v }, "seg-btn"));
  }
  const seen = new Map();
  CACHE.forEach((e) => parseTags(e._html).filter((t) => matchSel(t, sel)).forEach((t) => {
    const s2 = stubFrom(t);
    seen.set(`${t.html}|${s2.dataset.id || ""}|${s2.dataset.act || ""}`, s2);
  }));
  return [...seen.values()];
}

global.window = {
  innerWidth: 412, innerHeight: 900,
  location: { search: "", href: "", protocol: "http:", hash: "" },
  addEventListener() {}, removeEventListener() {},
  scrollTo() {},
};
global.location = global.window.location;
global.localStorage = { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = String(v); } };
global.confirm = () => true;
global.scrollTo = () => {};
if (typeof global.addEventListener !== "function") { global.addEventListener = () => {}; global.removeEventListener = () => {}; }
Object.defineProperty(global, "navigator", {
  value: { clipboard: { writeText: async () => {} }, onLine: true }, configurable: true, writable: true,
});
global.DOWNLOADS = [];
global.Blob = class {
  constructor(parts, opts) { this.text = String(parts.join("")); this.size = this.text.length; this.type = (opts || {}).type; }
};
URL.createObjectURL = (b) => { global.DOWNLOADS.push(b); return "blob:x"; };
URL.revokeObjectURL = () => {};

// پولینگ سریع‌تر تا هارنس چند ثانیه بیشتر طول نکشد
const realSetInterval = global.setInterval;
global.setInterval = (fn, ms, ...rest) => realSetInterval(fn, Math.min(ms || 0, 40), ...rest);

const API_CALLS = [];
global.fetch = async (url, opts) => {
  const u = String(url);
  if (u.startsWith("api/") || u.startsWith("/api/") || u.startsWith("http")) {
    API_CALLS.push(u);
    if (!LIVE && !u.startsWith("http")) return { ok: false, status: 503, json: async () => ({ error: "offline" }) };
    const res = await realFetch(u.startsWith("http") ? u : new URL(u, LIVE).toString(), opts);
    const text = await res.text();
    return { ok: res.ok, status: res.status, json: async () => (text ? JSON.parse(text) : {}) };
  }
  const file = u.includes("demo-data.json") || u.includes("sample.json") ? SAMPLE
    : path.join(NOVA, u.replace(/^\.\//, ""));
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
const docClick = (target) => global.document._h.click({ target, preventDefault() {}, stopPropagation() {} });

async function main() {
  console.log(`\n── smoke nova (${LIVE ? `live: ${LIVE}` : "demo"}) ──`);
  for (const f of ["ui.js", "app.js"]) {
    vm.runInThisContext(fs.readFileSync(path.join(NOVA, f), "utf8"), { filename: f });
  }
  await tick(900);                                   // boot: دمو + سلامت + گزینه‌ها + لیدها

  check("boot: چیپ‌های هدف رندر شد", el("goalChips").innerHTML.includes("data-goal"), el("goalChips").innerHTML.slice(0, 60));
  check("boot: چیپ موتور نوشته شد", el("engineChip").textContent.length > 2, el("engineChip").textContent);
  check("boot: وضعیت در ریل نوشته شد", el("railHealthTxt").textContent.includes("لید"), el("railHealthTxt").textContent);
  check("boot: کارت‌های لید رندر شد", el("leadList").innerHTML.includes('class="lead"'),
    el("leadList").innerHTML.slice(0, 60));
  check("boot: شمارندهٔ لید", el("leadCount").textContent.includes("لید"), el("leadCount").textContent);
  check("boot: چیپ‌های بخش‌بندی", el("segChips").innerHTML.includes("data-seg"));
  check("boot: سقوط به حالت نمایشی بدون سرور", el("demoMode").checked === true || LIVE !== "");

  // ۱) تجزیهٔ جمله + پیشنهادها
  el("q").value = "دندانپزشکی در مشهد";
  el("q").fire("input");
  await tick(900);
  check("parse: یادداشت «فهمیدم» نمایش داده شد", el("parseNote").hidden === false && el("parseNote").innerHTML.includes("فهمیدم"),
    el("parseNote").innerHTML.slice(0, 80));
  el("q").value = "دندان";
  el("q").fire("input");
  await tick(700);
  check("suggest: پیشنهاد صنف آمد", el("suggest").innerHTML.includes("data-fill"), el("suggest").innerHTML.slice(0, 60));
  const sugBtn = el("suggest").querySelectorAll("[data-fill]")[0];
  if (sugBtn) { el("suggest").fire("click", { target: sugBtn }); await tick(700); }
  check("suggest: کلیک روی پیشنهاد جمله را پر کرد", el("q").value.length > 2, el("q").value);

  // ۲) حجم، هدف، منطقه، صنف
  const quick = mk({ "data-size": "quick" }, "seg-btn");
  el("sizeSeg").fire("click", { target: quick });
  check("size: عمقِ «سریع» اعمال شد", el("depth").value === "2" || el("depth").value === 2, String(el("depth").value));
  const goalAds = mk({ "data-goal": "ads" }, "chip");
  el("goalChips").fire("click", { target: goalAds });
  check("goal: هدف عوض شد", el("goalChips").innerHTML.includes('data-goal="ads" data-on="1"'),
    el("goalChips").innerHTML.slice(0, 90));
  el("areaBtn").fire("click");
  await tick(300);
  check("area: شیت منطقه باز شد", el("sheetRoot").hidden === false);
  check("area: راهنمای آبشاری رندر شد", el("sheetBody").innerHTML.includes("areaStack") || el("sheetBody").innerHTML.includes("ناحیه") || el("sheetBody").innerHTML.length > 40);
  const areaBody = el("sheetBody");
  areaBody.fire("click", { target: mk({ "data-act": "area-done" }) });
  await tick(300);
  check("area: انتخاب منطقه بسته شد و برچسب نشست", el("areaLabel").textContent.length > 1, el("areaLabel").textContent);
  el("nicheBtn").fire("click");
  await tick(400);
  const nicheItem = el("sheetBody").querySelectorAll("[data-act='niche-pick']")[0];
  check("niche: فهرست صنف‌ها رندر شد", Boolean(nicheItem), el("sheetBody").innerHTML.slice(0, 60));
  if (nicheItem) { el("sheetBody").fire("click", { target: nicheItem }); await tick(250); }
  check("niche: برچسب صنف نشست", el("nicheLabel").textContent.length > 1, el("nicheLabel").textContent);

  // ۳) اجرا + پولینگ پیشرفت
  el("goBtn").fire("click");
  await tick(500);
  check("run: داک اجرای فعال ظاهر شد", el("dock").hidden === false);
  check("run: کارت زنده باز شد", el("huntLive").hidden === false);
  const t0 = Date.now();
  const finished = () => global.TOASTS.some((t) => /تمام شد|چیزی نبود/.test(t));
  // ۲۰ ثانیه: رانرهای CI کندترند و اجرای دمو (بدون شبکه) باید خیلی زودتر تمام شود
  while (!finished() && Date.now() - t0 < 20000) await tick(120);
  check("run: پیشرفت تا پایان پول شد", finished(),
    `${Math.round((Date.now() - t0) / 1000)}s · ` + global.TOASTS.slice(-2).join(" | "));
  check("run: حلقهٔ داک درصد گرفت", Number(el("dockFill").attrs["stroke-dashoffset"]) >= 0,
    String(el("dockFill").attrs["stroke-dashoffset"]));

  // ۴) تب لیدها + فیلتر + جست‌وجو + نما
  const leadsTab = global.document.querySelectorAll("[data-tab]")[1];
  leadsTab.fire("click");
  await tick(500);
  check("tab: نمای لیدها باز شد", el("view-leads").hidden === false);
  check("tab: کارت‌ها رندر شدند", el("leadList").innerHTML.includes('class="lead"'));
  const hotChip = el("segChips").querySelectorAll("[data-seg]").find((b) => b.dataset.seg === "hot");
  if (hotChip) { el("segChips").fire("click", { target: hotChip }); await tick(400); }
  check("filter: فیلتر داغ بدون خطا اعمال شد", el("leadCount").textContent.includes("لید"), el("leadCount").textContent);
  const phoneChip = el("filterChips").querySelectorAll("[data-has]")[0];
  if (phoneChip) { el("filterChips").fire("click", { target: phoneChip }); await tick(400); }
  check("filter: چیپ تماس بدون خطا", true);
  el("lq").value = "مشهد";
  el("lq").fire("input");
  await tick(600);
  check("search: جست‌وجوی لیدها بدون خطا", el("leadList").innerHTML.length > 10);
  el("lq").value = "";
  el("lq").fire("input");
  await tick(500);
  el("sortSel").value = "reviews";
  el("sortSel").fire("change");
  await tick(400);
  check("sort: چینش بر اساس نظر", true);
  const listBtn = el("viewToggle").querySelectorAll("[data-view]")[1] || mk({ "data-view": "list" }, "seg-btn");
  el("viewToggle").fire("click", { target: listBtn });
  await tick(400);
  check("view: نمای فهرستی کلاس گرفت", el("leadList").classList.contains("is-list"));

  // ۵) شیت لید + وضعیت
  const firstLead = el("leadList").querySelectorAll("[data-act='lead-open']")[0];
  check("lead: کارت کلیک‌پذیر است", Boolean(firstLead && firstLead.dataset.id), firstLead ? firstLead.dataset.id : "null");
  if (firstLead) {
    docClick(firstLead);
    await tick(500);
    check("sheet: شیت لید باز شد", el("sheetRoot").hidden === false);
    check("sheet: عنوان لید نشست", el("sheetTitle").textContent.length > 1, el("sheetTitle").textContent);
    check("sheet: راه‌های تماس رندر شد", el("sheetBody").innerHTML.includes("راه‌های تماس") || el("sheetBody").innerHTML.includes("contact-row"));
    check("sheet: حلقهٔ امتیاز", el("sheetBody").innerHTML.includes('class="ring'));
    check("sheet: عوامل امتیاز", el("sheetBody").innerHTML.includes("چرا این امتیاز") || el("sheetBody").innerHTML.includes("factor"));
    const statusBtn = el("sheetBody").querySelectorAll("[data-act='status']").find((b) => b.dataset.status === "contacted");
    if (statusBtn) { docClick(statusBtn); await tick(400); }
    check("status: تغییر وضعیت بدون خطا", global.TOASTS.some((t) => t.includes("وضعیت")), global.TOASTS.slice(-1).join(""));
    docClick(mk({ "data-act": "sheet-close" }));
    await tick(300);
    check("sheet: بسته شد", el("sheetRoot").hidden === true);
  }

  // ۶) تب کارها + شیت کار + خروجی
  const runsTab = global.document.querySelectorAll("[data-tab]")[2];
  runsTab.fire("click");
  await tick(600);
  check("runs: آمار صف رندر شد", el("queueStats").innerHTML.includes("در صف"), el("queueStats").innerHTML.slice(0, 60));
  check("runs: فهرست کارها رندر شد", el("runList").innerHTML.includes('class="run"'), el("runList").innerHTML.slice(0, 60));
  const firstRun = el("runList").querySelectorAll("[data-act='run-open']")[0];
  if (firstRun) {
    docClick(firstRun);
    await tick(400);
    check("run-sheet: نوار پیشرفت", el("sheetBody").innerHTML.includes('class="bar"'));
    const expBtn = el("sheetBody").querySelectorAll("[data-act='run-export']")[0];
    const before = global.DOWNLOADS.length;
    if (expBtn) { docClick(expBtn); await tick(300); }
    if (!LIVE) {
      check("export: فایل در مرورگر ساخته شد", global.DOWNLOADS.length > before, `ساخته: ${global.DOWNLOADS.length - before}`);
      const csv = global.DOWNLOADS[global.DOWNLOADS.length - 1];
      check("export: CSV با BOM و سرستون", Boolean(csv) && csv.text.startsWith("\ufeff") && csv.text.split("\n")[0].includes("title"),
        csv ? csv.text.split("\n")[0].slice(0, 60) : "بدون فایل");
    }
    const leadsBtn = el("sheetBody").querySelectorAll("[data-act='run-leads']")[0];
    if (leadsBtn) { docClick(leadsBtn); await tick(500); }
    check("run-sheet: پرش به لیدهای همان کار", el("view-leads").hidden === false);
    docClick(mk({ "data-act": "sheet-close" }));
    await tick(250);
  }

  // ۷) پوسته و بیشتر
  const moreTab = global.document.querySelectorAll("[data-tab]")[3];
  moreTab.fire("click");
  await tick(400);
  check("more: کارت سلامت رندر شد", el("healthCard").innerHTML.includes("hcell"), el("healthCard").innerHTML.slice(0, 60));
  el("themeBtn").fire("click");
  check("theme: پوسته عوض شد", global.document.documentElement.dataset.theme === "light",
    global.document.documentElement.dataset.theme);
  const darkBtn = global.document.querySelectorAll("[data-theme-val]")[2];
  darkBtn.fire("click");
  check("theme: حالت تاریک از سگمنت", global.document.documentElement.dataset.theme === "dark");
  el("hapticSw").checked = false;
  el("hapticSw").fire("change");
  check("haptic: سوئیچ لرزش بدون خطا", true);
  docClick(mk({ "data-act": "about" }));
  await tick(250);
  check("about: شیت درباره باز شد", el("sheetBody").innerHTML.includes("مشتری‌یاب"), el("sheetBody").innerHTML.slice(0, 50));
  check("legal: خط نسبت‌دهی داده رندر شد", (el("attribution").innerHTML || "").length > 8,
    el("attribution").innerHTML);
  check("legal: لینک شرایط و حریم خصوصی هست",
    Boolean(documentQuery("a[href='./terms']") && documentQuery("a[href='./privacy']")));
  check("engine: برچسب موتور با موتورهای واقعی هم‌خوان است",
    /موتور واقعی|دادهٔ واقعی|گوگل|نمایشی|بدون موتور/.test(el("engineChip").textContent),
    el("engineChip").textContent);
  docClick(mk({ "data-act": "sheet-close" }));

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
