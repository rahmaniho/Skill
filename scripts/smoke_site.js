/* هارنس تست سریع سایت ایستا: DOM ساختگی + fetch واقعی روی فایل نمونه.
   فقط برای گرفتن خطاهای زمان اجرا در رندرهای سایت است (جایگزین مرورگر نیست).
   اجرا: node scripts/smoke_site.js */
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SITE = path.join(ROOT, "site");

class El {
  constructor(id = "") {
    this.id = id; this._html = ""; this.textContent = ""; this.value = "";
    this.checked = false; this.hidden = false; this.disabled = false;
    this.children = []; this.dataset = {}; this.style = {};
    this._handlers = {};
    this.options = [{ outerHTML: '<option value="">همه</option>' }];
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this._q = {}; RENDERED[this.id] = String(v).length; }
  addEventListener(ev, fn) { (this._handlers[ev] = this._handlers[ev] || []).push(fn); }
  removeEventListener() {}
  querySelectorAll(sel) {
    // نتایج را نگه می‌داریم تا handlerها روی همان نمونه‌هایی بمانند که رندر attaching کرد
    if (!this._q) this._q = {};
    if (this._q[sel]) return this._q[sel];
    const out = [];
    if (sel === ".pt" && this.id === "scatter") {
      const n = (this._html.match(/class="pt /g) || []).length;
      for (let i = 0; i < n; i++) {
        const el = new El(`pt${i}`); el.dataset.i = String(i); out.push(el);
      }
    }
    if (sel === "tr[data-i]" && this.id === "leadsBody") {
      const n = (this._html.match(/data-i="/g) || []).length;
      for (let i = 0; i < n; i++) { const el = new El(`tr${i}`); el.dataset.i = String(i); out.push(el); }
    }
    if (sel === "button" && this.id === "campTabs") {
      ["email", "whatsapp", "sms", "instagram", "phone"].forEach((c) => {
        const el = new El(`tab-${c}`); el.dataset.c = c; out.push(el);
      });
    }
    this._q[sel] = out;
    return out;
  }
  querySelector() { return null; }
  appendChild(c) { this.children.push(c); return c; }
  remove() {}
  select() {}
  click() { this.fire("click", {}); }
  fire(ev, payload = {}) {
    const evt = Object.assign({ target: this, key: "", preventDefault() {} }, payload);
    if (typeof this[`on${ev}`] === "function") this[`on${ev}`](evt);      // خاصیت on* هم مثل مرورگر
    (this._handlers[ev] || []).forEach((f) => f(evt));
  }
  closest() { return null; }
}

const RENDERED = {};
const CACHE = new Map();
function el(id) {
  if (!CACHE.has(id)) CACHE.set(id, new El(id));
  return CACHE.get(id);
}

global.RENDERED = RENDERED;
global.document = {
  readyState: "complete",
  getElementById: el,
  createElement: (tag) => new El(`<${tag}>`),
  querySelectorAll: (sel) => (sel === ".copybtn" ? [Object.assign(el("copy1"), { dataset: { copy: "ls" } })] : []),
  addEventListener: () => {},
  body: new El("body"),
  execCommand: () => true,
};
global.window = { innerWidth: 1280, innerHeight: 900 };
Object.defineProperty(global, "navigator", {
  value: { clipboard: { writeText: async () => {} } }, configurable: true, writable: true,
});
global.fetch = async (url) => {
  const file = path.join(SITE, String(url).replace(/^\.\//, ""));
  if (!fs.existsSync(file)) return { ok: false, status: 404, json: async () => ({}) };
  return { ok: true, status: 200, json: async () => JSON.parse(fs.readFileSync(file, "utf8")) };
};
global.DOWNLOADS = [];
global.Blob = class {
  constructor(parts, opts) { this.text = String(parts.join("")); this.size = this.text.length; this.type = (opts || {}).type; }
};
global.URL = { createObjectURL: (b) => { global.DOWNLOADS.push(b); return "blob:x"; }, revokeObjectURL: () => {} };

require(path.join(SITE, "app.js"));

// صبر برای بوت async
(async () => {
  await new Promise((r) => setTimeout(r, 300));
  const must = ["demoNotice", "demoKpis", "coverageChart", "segmentDonut", "segmentLegend",
                "scoreChart", "gapsChart", "channelChart", "nicheChart", "insights",
                "scatter", "preset", "presetTable", "presetHint", "leadsBody", "pager",
                "demoCount", "footMeta"];
  let bad = 0;
  for (const id of must) {
    const len = RENDERED[id] || el(id).textContent.length || 0;
    if (!len) { console.error(`✗ ${id} خالی ماند`); bad++; }
  }
  if (!/^\d+$/.test(el("stNiches").textContent.replace(/[۰-۹]/g, (d) => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)))) {
    console.error("✗ آمار قهرمان صفحه پر نشد"); bad++;
  }
  console.log(`رندرها: ${Object.keys(RENDERED).length} بخش | جدول: ${el("leadsBody").innerHTML.length} نویسه` +
              ` | نقشه: ${(el("scatter").innerHTML.match(/class="pt /g) || []).length} نقطه` +
              ` | صفحه‌بندی: ${el("pager").innerHTML.includes("صفحه") ? "ok" : "—"}`);

  // تعامل: فیلتر «داغ» + جست‌وجو + مرتب‌سازی + کشوی لید + کمپین هر کانال
  el("fSegment").value = "hot"; el("fSegment").fire("change");
  const hotRows = (el("leadsBody").innerHTML.match(/data-i="/g) || []).length;
  el("fSegment").value = ""; el("q").value = "لوله"; el("q").fire("input");
  const searchRows = (el("leadsBody").innerHTML.match(/data-i="/g) || []).length;
  el("q").value = ""; el("q").fire("input");
  el("sortBy").value = "reviews"; el("sortBy").fire("change");

  el("drawer").hidden = true;                 // تا باز شدن کشو واقعاً بررسی شود
  let clickError = null;
  try { el("leadsBody").querySelectorAll("tr[data-i]")[0].click(); }
  catch (e) { clickError = e; }
  const drawerOpen = !el("drawer").hidden;
  if (clickError) console.error("✗ خطا در باز کردن کشو:", clickError.message,
    "\n" + String(clickError.stack).split("\n").slice(1, 4).join("\n"));
  const campHtml = el("campTabs").innerHTML;
  el("campTabs").querySelectorAll("button").forEach((b) => b.click());
  const msgLen = el("msgBox").textContent.length;

  // خروجی CSV/JSON
  el("preset").value = "contact"; el("preset").fire("change");   // تغییر پیش‌تنظیم ستون‌ها
  el("btnCsv").click(); el("btnJson").click();
  el("btnReset").click();
  const csv = global.DOWNLOADS.find((b) => b.type.startsWith("text/csv"));
  const jsonDl = global.DOWNLOADS.find((b) => b.type.startsWith("application/json"));
  if (!csv) { console.error("✗ CSV ساخته نشد"); bad++; }
  else {
    const lines = csv.text.replace(/^\ufeff/, "").split("\n");
    const splitCsv = (line) => {
      const out = []; let cur = "", q = false;
      for (const ch of line) {
        if (q) { if (ch === '"') q = false; else cur += ch; }
        else if (ch === '"') q = true;
        else if (ch === ",") { out.push(cur); cur = ""; }
        else cur += ch;
      }
      out.push(cur); return out;
    };
    const cols = splitCsv(lines[0]).length;
    const blankish = lines.slice(1).filter((l) => !l.replace(/[,"]/g, "").trim()).length;
    console.log(`خروجی: CSV ${lines.length - 1} ردیف × ${cols} ستون (پیش‌تنظیم ${el("preset").value})` +
                ` | ردیف کاملاً خالی: ${blankish} | JSON ${jsonDl ? jsonDl.size : 0} بایت`);
    console.log(`سرستون‌ها: ${lines[0].replace(/^\ufeff/, "").split(",").slice(0, 6).join(" ، ")}`);
    if (cols < 10 || lines.length < 50) { console.error("✗ CSV ناقص است"); bad++; }
  }

  console.log(`تعامل: داغ=${hotRows} ردیف | جست‌وجوی «لوله»=${searchRows} ردیف | کشو=${drawerOpen ? "باز" : "بسته"}` +
              ` | تب‌های کمپین=${(campHtml.match(/data-c=/g) || []).length} | آخرین متن=${msgLen} نویسه`);

  if (bad) { console.error(`\n✗ ${bad} بخش رندر نشد`); process.exit(1); }
  if (!drawerOpen || hotRows === 0 || searchRows === 0) { console.error("\n✗ تعامل شکست"); process.exit(1); }
  console.log("\n✓ سایت ایستا بدون خطا رندر و تعامل کرد");
  process.exit(0);
})();
