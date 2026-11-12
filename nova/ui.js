/* نُوا — چارچوب ریزِ رابط (بدون هیچ کتابخانهٔ بیرونی)
   کارها: انتخابگرها، اعداد فارسی، توست، شیت پایین، تب‌ها، پوسته، لرزش ریز،
   ثبت سرویس‌ورکر، حلقهٔ امتیاز (SVG گرادیان مشترک)، رویدادهای آنلاین/آفلاین. */
(function () {
  "use strict";

  var FA = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];
  var WIN = globalThis;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function socialHref(platform, value) {
    var base = { instagram: "https://instagram.com/", facebook: "https://facebook.com/",
                 linkedin: "https://linkedin.com/company/" }[platform] || "";
    var v = String(value == null ? "" : value).trim();
    if (!v) return "";
    if (/^https?:\/\//i.test(v)) return v;
    if (/^(www\.)?(instagram\.com|instagr\.am|facebook\.com|fb\.com|fb\.me|linkedin\.com)\//i.test(v)) {
      return "https://" + v.replace(/^https?:\/\//i, "");
    }
    return base + v.replace(/^@+/, "").replace(/[?#].*$/, "").replace(/\/+$/, "");
  }
  function socialName(platform, value) {
    var v = String(value == null ? "" : value).trim();
    if (!v) return "";
    var m = v.match(/(?:instagram|facebook|linkedin)\.com\/(?:company\/)?([^/?#]+)/i);
    return m ? "@" + m[1] : ("@" + v.replace(/^@+/, ""));
  }

  function fa(v) {
    if (v == null || v === "") return "";
    return String(v).replace(/[0-9]/g, function (d) { return FA[+d]; })
      .replace(/\./g, "٫").replace(/-/g, "−");
  }
  function num(v, digits) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return fa(digits != null ? n.toFixed(digits) : String(n));
  }
  function pct(v) { return fa(Math.round(Number(v) || 0)) + "٪"; }
  function ago(iso) {
    if (!iso) return "";
    var t = Date.parse(iso);
    if (!isFinite(t)) return "";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "همین الان";
    if (s < 3600) return fa(Math.floor(s / 60)) + " دقیقه پیش";
    if (s < 86400) return fa(Math.floor(s / 3600)) + " ساعت پیش";
    return fa(Math.floor(s / 86400)) + " روز پیش";
  }
  function clock(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("fa-IR", { dateStyle: "medium", timeStyle: "short" }).format(d);
    } catch (e) { return iso.slice(0, 16).replace("T", " "); }
  }

  /* ── لرزش ریز (فقط با رضایت کاربر و فقط روی دستگاه لمسی) ── */
  var hapticOn = true;
  function buzz(ms) {
    if (!hapticOn) return;
    try { if (WIN.navigator && navigator.vibrate) navigator.vibrate(ms || 8); } catch (e) { /* بی‌خیال */ }
  }

  /* ── توست ── */
  function toast(msg, tone, ms) {
    var box = $("toasts");
    if (!box) return;
    var el = document.createElement("div");
    el.className = "toast";
    if (tone) el.dataset.tone = tone;
    el.innerHTML = '<span class="toast-ico">' + (tone === "err" ? "⚠️" : tone === "ok" ? "✅" : "💬") +
      "</span><span>" + esc(msg) + "</span>";
    box.appendChild(el);
    setTimeout(function () {
      el.classList.add("is-out");
      setTimeout(function () { el.remove(); }, 260);
    }, ms || 3200);
  }

  /* ── شیت پایین ── */
  var sheetOpen = null;
  function sheet(title, html, onMount) {
    var root = $("sheetRoot");
    if (!root) return;
    $("sheetTitle").textContent = title || "";
    $("sheetBody").innerHTML = html || "";
    root.hidden = false;
    document.body.style.overflow = "hidden";
    sheetOpen = true;
    if (typeof onMount === "function") onMount($("sheetBody"));
    var closeBtn = $("sheetClose");
    if (closeBtn) closeBtn.focus({ preventScroll: true });
  }
  function sheetHide() {
    var root = $("sheetRoot");
    if (!root || root.hidden) return;
    var panel = root.querySelector(".sheet");
    sheetOpen = false;
    document.body.style.overflow = "";
    if (panel) panel.classList.add("is-closing");
    setTimeout(function () {
      root.hidden = true;
      if (panel) panel.classList.remove("is-closing");
      $("sheetBody").innerHTML = "";
    }, 200);
  }

  /* ── تب‌ها / نماها ── */
  var TABS = ["hunt", "leads", "runs", "more"];
  var onTab = null;
  function setTab(name, opts) {
    if (TABS.indexOf(name) < 0) name = "hunt";
    TABS.forEach(function (t) {
      var view = $("view-" + t);
      if (view) view.hidden = t !== name;
    });
    document.querySelectorAll("[data-tab]").forEach(function (b) {
      var on = b.dataset.tab === name;
      b.classList.toggle("is-active", on);
      if (b.classList.contains("tab") || b.classList.contains("rail-item")) {
        b.setAttribute("aria-current", on ? "page" : "false");
      }
    });
    try { history.replaceState(null, "", "#" + name); } catch (e) { /* بی‌خیال */ }
    if (!opts || opts.scroll !== false && typeof WIN.scrollTo === "function") {
      try { WIN.scrollTo({ top: 0, behavior: "auto" }); } catch (e) { WIN.scrollTo ? WIN.scrollTo(0, 0) : 0; }
    }
    if (typeof onTab === "function") onTab(name);
  }

  /* ── پوسته (روشن/تاریک/خودکار) ── */
  function applyTheme(mode) {
    document.documentElement.dataset.theme = mode || "auto";
    document.querySelectorAll("[data-theme-val]").forEach(function (b) {
      b.classList.toggle("is-on", b.dataset.themeVal === mode);
    });
    try { localStorage.setItem("nova-theme", mode); } catch (e) { /* بی‌خیال */ }
  }
  function loadTheme() {
    var m = "auto";
    try { m = localStorage.getItem("nova-theme") || "auto"; } catch (e) { /* بی‌خیال */ }
    applyTheme(m);
  }

  /* ── حلقهٔ امتیاز: گرادیان مشترک SVG یک‌بار تزریق می‌شود ── */
  function ringDefs() {
    if ($("ringDefsHost")) return "";
    var host = document.createElement("div");
    host.id = "ringDefsHost";
    host.style.cssText = "position:absolute;width:0;height:0;overflow:hidden";
    host.innerHTML = '<svg aria-hidden="true"><defs><linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#4f46e5"/><stop offset=".55" stop-color="#06b6d4"/>' +
      '<stop offset="1" stop-color="#10b981"/></linearGradient></defs></svg>';
    document.body.appendChild(host);
    return "";
  }
  var RING_C = 2 * Math.PI * 15;
  function ringHtml(score, cls) {
    var s = Math.max(0, Math.min(100, Number(score) || 0));
    var off = RING_C * (1 - s / 100);
    var tone = s >= 75 ? "is-hot" : s >= 55 ? "is-warm" : "is-cold";
    return '<span class="ring ' + tone + " " + esc(cls || "") + '" title="امتیاز داغی ' + fa(s) + ' از ۱۰۰">' +
      '<svg viewBox="0 0 36 36" aria-hidden="true"><circle class="ring-bg" cx="18" cy="18" r="15"/>' +
      '<circle class="ring-fg" cx="18" cy="18" r="15" stroke-dasharray="' + RING_C.toFixed(1) +
      '" stroke-dashoffset="' + off.toFixed(1) + '"/></svg><b>' + fa(s) + "</b></span>";
  }
  function barHtml(value) {
    var v = Math.max(0, Math.min(100, Number(value) || 0));
    return '<div class="bar"><i style="width:' + v.toFixed(0) + '%"></i></div>';
  }

  /* ── اسکلتون ── */
  function skeletonCards(n) {
    var out = "";
    for (var i = 0; i < (n || 4); i++) {
      out += '<div class="skel-card" aria-hidden="true">' +
        '<div style="display:flex;gap:10px;align-items:center"><div class="skel" style="width:42px;height:42px;border-radius:14px"></div>' +
        '<div style="flex:1;display:grid;gap:6px"><div class="skel" style="height:13px;width:62%"></div>' +
        '<div class="skel" style="height:10px;width:40%"></div></div>' +
        '<div class="skel" style="width:46px;height:46px;border-radius:50%"></div></div>' +
        '<div style="display:flex;gap:6px"><div class="skel" style="height:22px;width:70px;border-radius:999px"></div>' +
        '<div class="skel" style="height:22px;width:92px;border-radius:999px"></div></div>' +
        '<div style="display:flex;gap:6px"><div class="skel" style="height:30px;width:88px;border-radius:10px"></div>' +
        '<div class="skel" style="height:30px;width:74px;border-radius:10px"></div>' +
        '<div class="skel" style="height:30px;width:64px;border-radius:10px"></div></div></div>';
    }
    return out;
  }

  /* ── شبکه ── */
  function bindOnline() {
    function paint() {
      var off = $("offline");
      if (off) off.hidden = WIN.navigator ? navigator.onLine !== false : true;
    }
    WIN.addEventListener("online", paint);
    WIN.addEventListener("offline", paint);
    paint();
  }

  /* ── سرویس‌ورکر (فقط https و فقط وقتی فایلش هست) ── */
  function registerSW() {
    if (!WIN.navigator || !navigator.serviceWorker) return;
    if (location.protocol !== "https:") return;
    try { navigator.serviceWorker.register("sw.js").catch(function () { /* بی‌خیال */ }); } catch (e) { /* بی‌خیال */ }
  }

  WIN.Nova = {
    $: $, esc: esc, fa: fa, num: num, pct: pct, ago: ago, clock: clock,
    socialHref: socialHref, socialName: socialName,
    toast: toast, sheet: sheet, sheetHide: sheetHide, sheetIsOpen: function () { return !!sheetOpen; },
    setTab: setTab, onTab: function (fn) { onTab = fn; },
    applyTheme: applyTheme, loadTheme: loadTheme,
    buzz: buzz, setHaptic: function (v) { hapticOn = !!v; }, haptic: function () { return hapticOn; },
    ringHtml: ringHtml, barHtml: barHtml, skeletonCards: skeletonCards,
    ringDefs: ringDefs, registerSW: registerSW, bindOnline: bindOnline,
    RING_C: RING_C, TABS: TABS
  };
})();
