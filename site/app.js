/* LeadGen Studio — سایت عمومی (GitHub Pages)
 * یک صفحه کاملاً ایستا: داده از data/sample.json خوانده می‌شود و همه نمودارها،
 * کاوشگر لید، نقشه پراکندگی و متن کمپین در همین مرورگر ساخته می‌شوند.
 * بدون هیچ کتابخانه یا فونت بیرونی. قالب‌های کمپین آینهٔ leadgen/campaign.py هستند. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  const PER_PAGE = 25;

  const state = {
    data: null, leads: [], view: [], page: 1, preset: "contact",
    active: null, channel: "email",
    sender: {
      name: "سارا محمدی", company: "استودیو رشد", role: "مشاور رشد کسب‌وکارهای محلی",
      phone: "+98 912 000 0000", email: "sara@roshd.studio", website: "https://roshd.studio",
      offer: "۳۰ دقیقه مشاوره رایگان",
      case_study: "در پروژه مشابه، مشتری‌های محلی یک کسب‌وکار طی ۶۰ روز ۲٫۳ برابر شد",
    },
  };

  // ── ابزارها ──────────────────────────────────────────────────────────
  const fa = (v) => String(v ?? "").replace(/\d/g, (d) => FA_DIGITS[+d]);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (n, total) => (total ? Math.round((100 * n) / total) : 0);

  function toast(text, kind = "ok") {
    const wrap = $("toasts");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = text;
    wrap.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  async function copy(text, label = "کپی شد") {
    try {
      await navigator.clipboard.writeText(text);
      toast(label);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); toast(label); } catch { toast("کپی ناموفق بود", "err"); }
      ta.remove();
    }
  }

  function download(name, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  // ── قالب‌های کمپین (آینهٔ leadgen/campaign.py) ────────────────────────
  const ctxOf = (l) => {
    const parts = String(l.address || "").split(/[،,]/);
    return {
      business: String(l.title || "").replace(/[()|]/g, " ").trim() || "کسب‌وکار شما",
      category: l.category || l.niche_fa || "کسب‌وکار شما",
      city: (parts[parts.length - 1] || "").trim() || l.city || "",
      gap: (l.gaps_fa && l.gaps_fa[0]) || "حضور دیجیتال",
      pitch: l.pitch || "بهبود حضور دیجیتال",
      rating: l.rating || "", reviews: l.reviews || 0,
      phone: l.phone || "", email: l.email || "", website: l.website || "",
      instagram: l.instagram || "", s: state.sender,
    };
  };
  const t = (tpl, c) => tpl
    .replace(/\{sender\.(\w+)\}/g, (_, k) => c.s[k] ?? "")
    .replace(/\{(\w+)\}/g, (_, k) => (c[k] ?? ""));

  const EMAIL_SEQ = [
    { step: 1, day: 0,
      subject: "{business} — یک پیشنهاد مشخص برای {category} در {city}",
      body: `سلام تیم {business}،

من {sender.role} {sender.company} هستم. وقتی پروفایل «{business}» را در گوگل‌مپ دیدم،
یک نکته برایم روشن شد: {gap}. یعنی بخش قابل توجهی از مشتری‌های محلی که همین حالا
«{category}» را جست‌وجو می‌کنند، شما را نمی‌بینند یا نمی‌توانند راحت درخواست بدهند.

کاری که ما می‌کنیم مشخص است: {pitch}.
{sender.case_study}.

اگر موافق باشید، {sender.offer} رزرو کنیم؛ در همان جلسه یک بررسی کوتاه از وضعیت
جست‌وجوی محلی «{business}» به شما می‌دهم — بدون تعهد.

با احترام،
{sender.name} — {sender.company}
{sender.phone} | {sender.email} | {sender.website}

(اگر ترجیح می‌دهید دیگر پیام نگیرید، فقط بنویسید «لغو».)` },
    { step: 2, day: 3,
      subject: "پاسخ: {business} — ۳ عددی که درباره جست‌وجوی محلی جالب است",
      body: `سلام دوباره،

کوتاه و عددی می‌گویم تا تصمیم‌گیری راحت باشد:

۱) حدود ۴۶٪ از جست‌وجوهای گوگل «محلی» هستند.
۲) کسب‌وکارهایی که پروفایل گوگل‌مپ کامل و عکس دارند، تا ~۵۲۰٪ تماس بیشتر می‌گیرند.
۳) برای «{category}» در {city}، رقبا معمولاً با {gap} کار می‌کنند — یعنی هنوز جای رشد هست.

پیشنهاد من برای «{business}» همچنان همان است: {pitch}.

اگر الان زمان مناسبی نیست، فقط یک «بعداً» بفرستید تا سه ماه دیگر پیام بدهم.

{sender.name} — {sender.company} | {sender.phone}` },
    { step: 3, day: 8,
      subject: "پرونده {business} را ببندم؟",
      body: `سلام تیم {business}،

نمی‌خواهم صندوق ورودی‌تان را اشغال کنم. این آخرین پیام من درباره {pitch} برای
«{business}» ({category} در {city}) است.

دو حالت دارد:
• اگر علاقه‌مندید، {sender.offer} — این لینک/شماره کافی است: {sender.phone}
• اگر نه، هیچ اشکالی ندارد؛ فقط «لغو» بنویسید تا از فهرست خارج شوید.

برایتان در {city} پرفروش‌ترین فصل را آرزو می‌کنم.
{sender.name}` },
  ];

  const WHATSAPP = `سلام، وقتتون بخیر 🌷
«{business}» رو توی گوگل‌مپ برای {category} در {city} دیدم.
یک مورد مشخص به چشمم اومد: {gap}.

ما روی {pitch} کار می‌کنیم و {sender.case_study}.

اگر مایل باشید یک نمونه و هزینه تقریبی بفرستم؟ فقط یک «بله» کافیه.
{sender.name} — {sender.company}
(اگر مزاحم شدم، عذرخواهی می‌کنم؛ با «لغو» دیگه پیام نمی‌دم.)`;

  const SMS = `{business} عزیز، {gap} باعث می‌شه مشتری محلی شما رو پیدا نکنه.
{pitch} — {sender.offer}. {sender.name} {sender.phone} | لغو=۱`;

  const IG_SEQ = [
    `سلام 👋 پروفایل «{business}» رو دیدم؛ کارتون توی {category} واقعاً تمیزه.
یک نکته کوچیک که معمولاً مشتری از دست می‌ده: {gap}.
اگر دوست داشتید بگید تا یک پیشنهاد کوتاه و عملی براتون بفرستم (بدون هزینه).`,
    `خیلی کوتاه: ما {pitch} رو برای کسب‌وکارهای محلی {city} انجام می‌دیم.
{sender.case_study}.
اگر مایل بودید، نمونه‌کار و هزینه تقریبی رو همین‌جا می‌فرستم 🙂
{sender.name} — {sender.company}`,
  ];

  const PHONE = `۱) سلام، {sender.name} هستم از {sender.company}. مزاحم نیستم؟ (۱۰ ثانیه)
۲) پروفایل «{business}» رو در گوگل‌مپ دیدم؛ {rating} ستاره با {reviews} نظر — اعتبارتون خوبه.
۳) اما یک شکاف مشخص هست: {gap}. یعنی مشتری محلی یا پیداتون نمی‌کنه یا راحت درخواست نمی‌ده.
۴) کار ما: {pitch}. {sender.case_study}.
۵) پیشنهاد: {sender.offer}. فردا ساعت ۱۰ یا ۴ بعدازظهر کدام مناسب‌تره؟
۶) اگر نه: ممنون از وقتتون؛ اجازه هست دو ماه دیگر یک بار دیگر تماس بگیرم؟`;

  const CHANNEL_FA = { email: "ایمیل", whatsapp: "واتس‌اپ", sms: "پیامک",
                       instagram: "دایرکت اینستاگرام", phone: "تماس تلفنی" };

  function messageFor(lead, channel) {
    const c = ctxOf(lead);
    if (channel === "email") {
      const touches = EMAIL_SEQ.map((x) => ({ ...x, subject: t(x.subject, c), body: t(x.body, c) }));
      return {
        to: c.email, subject: touches[0].subject,
        text: touches.map((x) => `[لمس ${x.step} — روز ${x.day}]\nموضوع: ${x.subject}\n\n${x.body}`).join("\n\n---\n\n"),
      };
    }
    if (channel === "whatsapp") return { to: c.phone, subject: "", text: t(WHATSAPP, c) };
    if (channel === "sms") return { to: c.phone, subject: "", text: t(SMS, c).slice(0, 280) };
    if (channel === "instagram") {
      return { to: c.instagram ? `@${c.instagram}` : "", subject: "",
               text: IG_SEQ.map((b, i) => `[پیام ${i + 1}]\n${t(b, c)}`).join("\n\n---\n\n") };
    }
    return { to: c.phone, subject: "سناریوی تماس", text: t(PHONE, c) };
  }

  // ── نمودارها ─────────────────────────────────────────────────────────
  function bars(el, rows, { max, kind = "" } = {}) {
    const top = max ?? Math.max(1, ...rows.map((r) => r.count));
    el.innerHTML = rows.map((r) => `
      <div class="chart-row">
        <span class="lbl" title="${esc(r.label)}">${esc(r.label)}</span>
        <span class="track"><span class="fill ${r.kind || kind}" style="width:${Math.max(2, (100 * r.count) / top)}%"></span></span>
        <span class="val">${fa(r.count)}${r.percent != null ? ` · ${fa(r.percent)}٪` : ""}</span>
      </div>`).join("") || '<p class="hint">داده‌ای نیست.</p>';
  }

  function donut(el, segments) {
    const total = Object.values(segments).reduce((a, s) => a + s.count, 0) || 1;
    const colors = { hot: "#fb7185", warm: "#fbbf24", cold: "#60a5fa" };
    const names = { hot: "داغ 🔥", warm: "گرم 🌤", cold: "سرد ❄️" };
    const R = 52, C = 2 * Math.PI * R;
    let offset = 0;
    const arcs = Object.entries(segments).filter(([, s]) => s.count > 0).map(([key, s]) => {
      const len = (C * s.count) / total;
      const seg = `<circle r="${R}" cx="70" cy="70" fill="none" stroke="${colors[key]}" stroke-width="20"
        stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"
        transform="rotate(-90 70 70)"><title>${names[key]}: ${s.count}</title></circle>`;
      offset += len;
      return seg;
    }).join("");
    el.innerHTML = `<svg width="140" height="140" viewBox="0 0 140 140" role="img" aria-label="نمودار بخش‌بندی">
      ${arcs}<text x="70" y="66" text-anchor="middle" fill="#e8edf9" font-size="22" font-family="monospace">${fa(total)}</text>
      <text x="70" y="86" text-anchor="middle" fill="#7385a8" font-size="11">لید</text></svg>`;
    $("segmentLegend").innerHTML = Object.entries(segments).map(([key, s]) => `
      <div><i style="background:${colors[key]}"></i>${names[key]}: <b>${fa(s.count)}</b>
      <small>(${fa(s.percent)}٪ · میانگین ${fa(s.avg_score || 0)})</small></div>`).join("");
  }

  // ── رندر بخش‌ها ──────────────────────────────────────────────────────
  function renderAll() {
    const d = state.data, a = d.analytics, cov = a.coverage;
    $("demoNotice").innerHTML = `<b>⚠ دادهٔ نمایشی:</b> ${esc(d.disclaimer)}
      <br>نمونهٔ این صفحه: <b>${fa(d.leads.length)}</b> لید از جمعیت <b>${fa(d.meta.population)}</b> ردیف،
      در <b>${fa(d.meta.jobs)}</b> job متوالی و <b>${fa(d.meta.niches.length)}</b> صنف
      (${d.meta.niches.map(esc).join("، ")}) — شهر ${esc(d.meta.city)}، پروفایل
      <code>${esc(d.meta.profile)}</code>.`;
    $("footMeta").textContent = `نمونه ساخته‌شده در ${d.generated_at} · موتور ${d.meta.engine} · ${d.leads.length} لید`;

    const cat = d.meta.catalog;
    $("stNiches").textContent = fa(cat.niches); $("stGroups").textContent = fa(cat.groups);
    $("stCities").textContent = fa(cat.cities);  $("stPresets").textContent = fa(cat.channels);

    $("demoKpis").innerHTML = [
      ["لید در نمونه", cov.total, ""],
      ["لید داغ 🔥", a.segments.hot.count, `${a.segments.hot.percent}٪`],
      ["قابل تماس", cov.reachable, `${pct(cov.reachable, cov.total)}٪`],
      ["دارای ایمیل", cov.email, `${pct(cov.email, cov.total)}٪`],
      ["دارای شبکه اجتماعی", cov.any_social, `${pct(cov.any_social, cov.total)}٪`],
      ["میانگین کامل بودن اطلاعات", `${Math.round(cov.avg_completeness)}٪`, "از ۶ کانال"],
    ].map(([label, value, sub]) => `
      <div class="hstat"><b>${fa(value)}</b><span>${esc(label)}${sub ? ` · ${fa(sub)}` : ""}</span></div>`).join("");

    $("covHint").textContent = `میانگین کامل بودن: ${Math.round(cov.avg_completeness)}٪`;
    bars($("coverageChart"), [
      { label: "وب‌سایت", count: cov.website, percent: pct(cov.website, cov.total) },
      { label: "ایمیل", count: cov.email, percent: pct(cov.email, cov.total) },
      { label: "تلفن", count: cov.phone, percent: pct(cov.phone, cov.total) },
      { label: "اینستاگرام", count: cov.instagram, percent: pct(cov.instagram, cov.total) },
      { label: "فیسبوک", count: cov.facebook, percent: pct(cov.facebook, cov.total) },
      { label: "لینکدین", count: cov.linkedin, percent: pct(cov.linkedin, cov.total) },
      { label: "لوکیشن دقیق", count: d.leads.filter((l) => l.lat || l.lon).length,
        percent: pct(d.leads.filter((l) => l.lat || l.lon).length, cov.total) },
      { label: "نشانی کامل", count: d.leads.filter((l) => l.address).length,
        percent: pct(d.leads.filter((l) => l.address).length, cov.total) },
    ], { kind: "ok" });

    $("segHint").textContent = `پروفایل: ${d.meta.profile}`;
    donut($("segmentDonut"), a.segments);
    bars($("scoreChart"), a.score_distribution.filter((r) => r.count > 0)
      .map((r) => ({ label: `امتیاز ${r.range}`, count: r.count,
                     kind: +r.range.split("-")[0] >= 75 ? "hot" : (+r.range.split("-")[0] >= 55 ? "warm" : "cold") })));

    bars($("gapsChart"), a.gaps.slice(0, 8).map((g) => ({ label: g.fa, count: g.count, percent: g.percent })), { kind: "hot" });
    bars($("channelChart"), a.channels.map((c) => ({ label: c.label, count: c.count, percent: c.percent })));
    bars($("nicheChart"), a.categories.map((c) => ({ label: c.label, count: c.count })));

    $("insights").innerHTML = a.insights.map((i) =>
      `<div class="insight ${esc(i.kind)}"><span>◈</span><span>${esc(i.fa)}</span></div>`).join("");

    renderScatter();
    renderPresets();
    fillFilters();
    applyFilters();
  }

  function renderScatter() {
    const box = $("scatter");
    const pts = state.leads.filter((l) => l.lat && l.lon);
    if (!pts.length) { box.innerHTML = '<p class="hint" style="padding:1rem">مختصاتی در نمونه نیست.</p>'; return; }
    const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLon = Math.min(...lons), maxLon = Math.max(...lons);
    $("mapBBox").textContent = `${minLat.toFixed(3)}…${maxLat.toFixed(3)}, ${minLon.toFixed(3)}…${maxLon.toFixed(3)}`;
    box.innerHTML = pts.map((p) => {
      const x = ((maxLon - p.lon) / ((maxLon - minLon) || 1)) * 94 + 3;   // RTL: شرق سمت راست
      const y = ((maxLat - p.lat) / ((maxLat - minLat) || 1)) * 88 + 6;
      return `<span class="pt ${p.segment}" style="left:${x.toFixed(2)}%;top:${y.toFixed(2)}%"
        data-i="${state.leads.indexOf(p)}" tabindex="0" role="button"
        aria-label="${esc(p.title)}"></span>`;
    }).join("") + `<span class="axis" style="top:6px;left:10px">${maxLat.toFixed(3)}°N</span>
      <span class="axis" style="bottom:6px;left:10px">${minLat.toFixed(3)}°N</span>
      <span class="axis" style="top:6px;right:10px">${maxLon.toFixed(3)}°E</span>
      <span class="axis" style="bottom:6px;right:10px">${minLon.toFixed(3)}°E</span>`;

    const tip = $("tip");
    box.querySelectorAll(".pt").forEach((el) => {
      const lead = state.leads[+el.dataset.i];
      el.addEventListener("mouseenter", (e) => {
        tip.innerHTML = `<b>${esc(lead.title)}</b><br>${esc(lead.niche_fa)} · ${esc(lead.segment_fa)}
          <br><span class="mono">${lead.lat.toFixed(5)}, ${lead.lon.toFixed(5)}</span>
          <br>امتیاز ${fa(lead.score)} · ⭐ ${fa(lead.rating)} (${fa(lead.reviews)} نظر)`;
        tip.style.display = "block";
        tip.style.left = `${Math.min(window.innerWidth - 280, e.clientX + 14)}px`;
        tip.style.top = `${e.clientY + 14}px`;
      });
      el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
      el.addEventListener("click", () => { tip.style.display = "none"; openDrawer(lead); });
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") openDrawer(lead); });
    });
  }

  function renderPresets() {
    const names = { lead: "پیش‌فرض لید", contact: "کارت تماس کامل", location: "مکان و تماس",
                    all: "همه ستون‌ها" };
    $("preset").innerHTML = Object.keys(state.data.columns).map((k) =>
      `<option value="${k}"${k === state.preset ? " selected" : ""}>${names[k] || k} (${fa(state.data.columns[k].length)} ستون)</option>`).join("");
    $("presetHint").textContent =
      "در نسخهٔ نصبی، همین پیش‌تنظیم‌ها روی فایل Excel/CSV/JSON اعمال می‌شوند (CLI: --cols، API: ?cols=). " +
      "پیش‌تنظیم «همه ستون‌ها» در نصب واقعی شامل هر ۳۴ ستون خام موتور است؛ در این دموی ثابت فقط ستون‌های موجود در نمونه پر می‌شوند.";
    $("presetTable").innerHTML = `<div class="table-wrap"><table class="docs-table">
      <thead><tr><th>پیش‌تنظیم</th><th>ستون‌ها</th></tr></thead><tbody>
      ${Object.entries(state.data.columns).map(([k, cols]) =>
        `<tr><td><code>${k}</code><br><small>${names[k] || ""}</small></td>
         <td>${cols.map((c) => `<span class="pill" style="margin:.12rem">${esc(c[1])}</span>`).join("")}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function fillFilters() {
    const uniq = (fn) => [...new Set(state.leads.map(fn).filter(Boolean))].sort();
    const fill = (sel, values, labeler = (v) => v) => {
      const cur = sel.value;
      sel.innerHTML = sel.options[0].outerHTML + values.map((v) =>
        `<option value="${esc(v)}">${esc(labeler(v))}</option>`).join("");
      sel.value = cur;
    };
    fill($("fSegment"), uniq((l) => l.segment), (v) => ({ hot: "داغ 🔥", warm: "گرم 🌤", cold: "سرد ❄️" }[v] || v));
    fill($("fNiche"), uniq((l) => l.niche_fa));
    fill($("fChannel"), uniq((l) => l.channel_fa));
    fill($("fGap"), uniq((l) => (l.gaps_fa || []).join("|")).flatMap((s) => s.split("|")).filter((v, i, arr) => arr.indexOf(v) === i));
  }

  // ── کاوشگر لیدها ─────────────────────────────────────────────────────
  function applyFilters() {
    const q = $("q").value.trim().toLowerCase();
    const seg = $("fSegment").value, niche = $("fNiche").value;
    const ch = $("fChannel").value, gap = $("fGap").value;
    const min = +($("minScore").value || 0);
    const onlyReach = $("onlyReachable").checked, onlyNoSite = $("onlyNoSite").checked;

    state.view = state.leads.filter((l) => {
      if (seg && l.segment !== seg) return false;
      if (niche && l.niche_fa !== niche) return false;
      if (ch && l.channel_fa !== ch) return false;
      if (gap && !(l.gaps_fa || []).includes(gap)) return false;
      if (min && (l.score || 0) < min) return false;
      if (onlyReach && !(l.email || l.phone || l.instagram || l.facebook || l.linkedin)) return false;
      if (onlyNoSite && l.website) return false;
      if (q) {
        const hay = [l.title, l.address, l.domain, l.email, l.phone, l.plus_code, l.niche_fa,
                     l.instagram, l.location].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const by = $("sortBy").value;
    state.view.sort((a, b) => by === "title"
      ? String(a.title).localeCompare(String(b.title), "fa")
      : (+b[by] || 0) - (+a[by] || 0));

    const pages = Math.max(1, Math.ceil(state.view.length / PER_PAGE));
    state.page = Math.min(state.page, pages);
    renderTable();
    renderPager(pages);
    $("demoCount").textContent = `${fa(state.view.length)} از ${fa(state.leads.length)} لید · ` +
      `داغ: ${fa(state.view.filter((l) => l.segment === "hot").length)} · ` +
      `میانگین امتیاز: ${fa(state.view.length ? Math.round(state.view.reduce((s, l) => s + (+l.score || 0), 0) / state.view.length) : 0)}`;
  }

  function socialCell(l) {
    const items = [];
    if (l.instagram) items.push(`<a href="https://instagram.com/${encodeURIComponent(l.instagram)}" target="_blank" rel="noopener" title="اینستاگرام">IG</a>`);
    if (l.facebook) items.push(`<a href="https://facebook.com/${encodeURIComponent(l.facebook)}" target="_blank" rel="noopener" title="فیسبوک">FB</a>`);
    if (l.linkedin) items.push(`<a href="https://linkedin.com/in/${encodeURIComponent(l.linkedin)}" target="_blank" rel="noopener" title="لینکدین">IN</a>`);
    return items.length ? items.join(" ") : '<span class="badge muted">—</span>';
  }

  function renderTable() {
    const start = (state.page - 1) * PER_PAGE;
    const rows = state.view.slice(start, start + PER_PAGE);
    $("leadsBody").innerHTML = rows.length ? rows.map((l) => `
      <tr data-i="${state.leads.indexOf(l)}">
        <td><b>${esc(l.title)}</b><br><small>${esc((l.tags || []).slice(0, 2).join(" · "))}</small></td>
        <td>${esc(l.niche_fa)}</td>
        <td>${esc(l.address || l.city)}<br><small class="mono">${esc(l.plus_code || "")}</small></td>
        <td class="num">${l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : '<span class="badge muted">—</span>'}</td>
        <td class="num">${l.email ? `<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>` : '<span class="badge muted">—</span>'}</td>
        <td class="num">${l.website ? `<a href="${esc(l.website)}" target="_blank" rel="noopener">${esc(l.domain)}</a>` : '<span class="badge muted">بدون سایت</span>'}</td>
        <td class="num">${socialCell(l)}</td>
        <td class="num">${l.maps_link ? `<a href="${esc(l.maps_link)}" target="_blank" rel="noopener" title="${esc(l.location)}">📍 نقشه</a>` : "—"}</td>
        <td class="num">${fa(l.rating || "—")}</td>
        <td class="num">${fa(l.reviews || 0)}</td>
        <td><span class="score ${l.segment}">${fa(l.score)}</span></td>
        <td>${esc(l.channel_fa)}<br><small>${esc(l.completeness)}٪ کامل</small></td>
      </tr>`).join("")
      : '<tr><td colspan="12" class="empty">لیدی با این فیلترها پیدا نشد.</td></tr>';

    $("leadsBody").querySelectorAll("tr[data-i]").forEach((tr) => {
      tr.addEventListener("click", (e) => {
        if (e.target.closest("a")) return;
        openDrawer(state.leads[+tr.dataset.i]);
      });
    });
  }

  function renderPager(pages) {
    $("pager").innerHTML = `
      <button class="ghost" id="prev" ${state.page <= 1 ? "disabled" : ""}>› قبلی</button>
      <span>صفحه ${fa(state.page)} از ${fa(pages)}</span>
      <button class="ghost" id="next" ${state.page >= pages ? "disabled" : ""}>بعدی ‹</button>`;
    $("prev").onclick = () => { state.page--; renderTable(); renderPager(pages); };
    $("next").onclick = () => { state.page++; renderTable(); renderPager(pages); };
  }

  // ── کشوی لید ─────────────────────────────────────────────────────────
  function openDrawer(lead) {
    state.active = lead;
    state.channel = lead.channel || "email";
    $("drawerTitle").textContent = lead.title;
    $("drawerBody").innerHTML = `
      <div class="pills" style="margin-bottom:.6rem">
        <span class="badge ${lead.segment}">${esc(lead.segment_fa)}</span>
        <span class="pill">امتیاز <b>${fa(lead.score)}</b></span>
        <span class="pill">کانال: <b>${esc(lead.channel_fa)}</b></span>
        <span class="pill">کامل بودن <b>${fa(lead.completeness)}٪</b></span>
      </div>
      <p class="hint" style="margin-top:0">زاویه فروش پیشنهادی: <b style="color:var(--text)">${esc(lead.pitch)}</b></p>

      <h4 class="section-title">اطلاعات کسب‌وکار</h4>
      <dl class="kv">
        <dt>صنف منبع</dt><dd>${esc(lead.niche_fa)}</dd>
        <dt>دسته گوگل</dt><dd>${esc(lead.category || "—")}</dd>
        <dt>نشانی</dt><dd>${esc(lead.address || "—")}</dd>
        <dt>لوکیشن دقیق</dt><dd class="mono">${esc(lead.location || "—")}</dd>
        <dt>پلاس‌کد</dt><dd class="mono">${esc(lead.plus_code || "—")}</dd>
        <dt>لینک نقشه</dt><dd>${lead.maps_link ? `<a href="${esc(lead.maps_link)}" target="_blank" rel="noopener">📍 باز کردن در گوگل‌مپ ↗</a>` : "—"}</dd>
        <dt>تلفن</dt><dd>${lead.phone ? `<a href="tel:${esc(lead.phone)}" class="mono">${esc(lead.phone)}</a>` : "—"}</dd>
        <dt>ایمیل</dt><dd>${lead.email ? `<a href="mailto:${esc(lead.email)}" class="mono">${esc(lead.email)}</a>` : "—"}</dd>
        <dt>وب‌سایت</dt><dd>${lead.website ? `<a href="${esc(lead.website)}" target="_blank" rel="noopener" class="mono">${esc(lead.website)}</a>` : '<span class="badge warm">بدون وب‌سایت</span>'}</dd>
        <dt>شبکه اجتماعی</dt><dd>${socialCell(lead)}</dd>
        <dt>امتیاز گوگل</dt><dd>${fa(lead.rating || "—")} از ۵ با ${fa(lead.reviews || 0)} نظر</dd>
      </dl>

      <h4 class="section-title">چرا این امتیاز؟</h4>
      <div class="fx-list">${(lead.factors || []).map((f) => `
        <div class="fx"><span>${esc(f.fa)} — <b>${fa(f.points)}</b> از ${fa(f.weight)}</span>
          <span class="track"><span class="fill" style="width:${Math.min(100, (100 * f.points) / (f.weight || 1))}%"></span></span>
          <small>${esc(f.detail)}</small></div>`).join("") || '<p class="hint">جزئیاتی ذخیره نشده.</p>'}
      </div>

      <h4 class="section-title">شکاف‌ها و برچسب‌ها</h4>
      <div class="tag-list">${(lead.gaps_fa || []).map((g) => `<span class="badge warm">${esc(g)}</span>`).join("")}
        ${(lead.tags || []).filter((x) => !(lead.gaps_fa || []).includes(x)).map((x) => `<span class="badge muted">${esc(x)}</span>`).join("")}</div>

      <h4 class="section-title">متن آماده ارسال</h4>
      <div class="campaign-tabs" id="campTabs"></div>
      <div class="card" style="margin:.5rem 0;padding:.8rem">
        <div class="row">
          <div class="half field"><label for="sName">نام شما</label><input id="sName" value="${esc(state.sender.name)}"></div>
          <div class="half field"><label for="sCompany">کسب‌وکار شما</label><input id="sCompany" value="${esc(state.sender.company)}"></div>
        </div>
        <div class="row">
          <div class="half field"><label for="sRole">عنوان</label><input id="sRole" value="${esc(state.sender.role)}"></div>
          <div class="half field"><label for="sOffer">پیشنهاد</label><input id="sOffer" value="${esc(state.sender.offer)}"></div>
        </div>
        <div class="row">
          <div class="half field"><label for="sPhone">تلفن</label><input id="sPhone" value="${esc(state.sender.phone)}"></div>
          <div class="half field"><label for="sEmail">ایمیل</label><input id="sEmail" value="${esc(state.sender.email)}"></div>
        </div>
        <div class="field"><label for="sSite">سایت</label><input id="sSite" value="${esc(state.sender.website)}"></div>
        <div class="field"><label for="sCase">جملهٔ نمونه‌کار</label><input id="sCase" value="${esc(state.sender.case_study)}"></div>
      </div>
      <div class="msg-head" id="msgHead"></div>
      <div class="msg" id="msgBox"></div>
      <div class="camp-actions" id="msgActions"></div>
      <p class="hint">این متن‌ها در مرورگر شما ساخته می‌شوند (آینهٔ <code>leadgen/campaign.py</code>). در نسخهٔ نصبی، همین متن‌ها برای همهٔ لیدها یک‌جا در فایل کمپین و شیت «کمپین» اکسل نوشته می‌شود.</p>`;

    $("drawer").hidden = false; $("scrim").hidden = false;
    bindSender(); renderCampaign();
  }

  function bindSender() {
    const map = { sName: "name", sCompany: "company", sRole: "role", sOffer: "offer",
                  sPhone: "phone", sEmail: "email", sSite: "website", sCase: "case_study" };
    Object.entries(map).forEach(([id, key]) => {
      $(id).addEventListener("input", (e) => { state.sender[key] = e.target.value; renderCampaign(); });
    });
  }

  function renderCampaign() {
    if (!state.active) return;
    $("campTabs").innerHTML = Object.keys(CHANNEL_FA).map((c) =>
      `<button class="tab ${c === state.channel ? "active" : ""}" data-c="${c}">${CHANNEL_FA[c]}</button>`).join("");
    $("campTabs").querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => { state.channel = b.dataset.c; renderCampaign(); }));

    const msg = messageFor(state.active, state.channel);
    $("msgHead").innerHTML = `<span>گیرنده: <b class="mono">${esc(msg.to || "—")}</b></span>` +
      (msg.subject ? `<span>موضوع: <b>${esc(msg.subject)}</b></span>` : "") +
      `<span>${fa(msg.text.length)} نویسه</span>`;
    $("msgBox").textContent = msg.text;

    const actions = [];
    if (state.channel === "email" && msg.to) {
      actions.push(`<a class="primary" href="mailto:${encodeURIComponent(msg.to)}?subject=${encodeURIComponent(msg.subject)}&body=${encodeURIComponent(EMAIL_SEQ.length ? msg.text.split("\n\n---\n\n")[0] : msg.text)}">✉ باز کردن در ایمیل</a>`);
    }
    if (state.channel === "whatsapp" && state.active.phone) {
      const digits = String(state.active.phone).replace(/\D/g, "");
      actions.push(`<a class="primary" target="_blank" rel="noopener" href="https://wa.me/${digits.startsWith("98") ? digits : "98" + digits.replace(/^0/, "")}?text=${encodeURIComponent(messageFor(state.active, "whatsapp").text)}">💬 باز کردن واتس‌اپ</a>`);
    }
    if (msg.to && state.channel === "phone") actions.push(`<a class="primary" href="tel:${esc(msg.to)}">📞 تماس</a>`);
    actions.push(`<button class="ghost" id="copyMsg">📋 کپی متن</button>`);
    actions.push(`<button class="ghost" id="copyAll">📋 کپی همهٔ فیلدهای این لید</button>`);
    $("msgActions").innerHTML = actions.join("");
    $("copyMsg").onclick = () => copy(msg.text, "متن کپی شد");
    $("copyAll").onclick = () => copy(JSON.stringify(state.active, null, 2), "JSON لید کپی شد");
  }

  function closeDrawer() {
    $("drawer").hidden = true; $("scrim").hidden = true; state.active = null;
  }

  // ── خروجی CSV/JSON در مرورگر ─────────────────────────────────────────
  function valueOf(lead, key) {
    if (key === "emails") return lead.email || "";
    if (key === "email") return lead.email || "";
    if (key === "status") return "new";
    if (key === "note") return "";
    if (key === "gaps") return (lead.gaps_fa || []).join(" | ");
    if (key === "tags") return (lead.tags || []).join(" | ");
    if (key === "score_factors") return (lead.factors || []).map((f) => `${f.fa}:${f.points}`).join(" | ");
    const v = lead[key];
    return v == null ? "" : v;
  }

  function csvCell(v) {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function exportCsv() {
    const cols = state.data.columns[state.preset] || [];
    const head = cols.map((c) => csvCell(c[1])).join(",");
    const body = state.view.map((l) => cols.map((c) => csvCell(valueOf(l, c[0]))).join(",")).join("\n");
    download(`leadgen-demo-${state.preset}.csv`, "\ufeff" + head + "\n" + body, "text/csv;charset=utf-8");
    toast(`${fa(state.view.length)} ردیف با پیش‌تنظیم «${state.preset}» دانلود شد`);
  }

  function exportJson() {
    download("leadgen-demo-leads.json", JSON.stringify(state.view, null, 2), "application/json;charset=utf-8");
    toast(`${fa(state.view.length)} لید به JSON تبدیل شد`);
  }

  // ── رویدادها و بارگذاری ──────────────────────────────────────────────
  function bind() {
    ["q", "fSegment", "fNiche", "fChannel", "fGap", "sortBy", "minScore"].forEach((id) => {
      $(id).addEventListener("input", () => { if (id === "q" || id === "minScore") state.page = 1; applyFilters(); });
      $(id).addEventListener("change", () => { state.page = 1; applyFilters(); });
    });
    ["onlyReachable", "onlyNoSite"].forEach((id) => $(id).addEventListener("change", () => { state.page = 1; applyFilters(); }));
    $("preset").addEventListener("change", (e) => { state.preset = e.target.value; toast(`پیش‌تنظیم: ${e.target.value}`); });
    $("btnCsv").onclick = exportCsv;
    $("btnJson").onclick = exportJson;
    $("btnReset").onclick = () => {
      $("q").value = ""; $("minScore").value = "0";
      ["fSegment", "fNiche", "fChannel", "fGap"].forEach((id) => { $(id).value = ""; });
      $("onlyReachable").checked = $("onlyNoSite").checked = false;
      $("sortBy").value = "score"; state.page = 1; applyFilters();
    };
    $("drawerClose").onclick = closeDrawer;
    $("scrim").onclick = closeDrawer;
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

    document.querySelectorAll(".copybtn").forEach((b) =>
      b.addEventListener("click", () => copy(b.dataset.copy, "دستور کپی شد")));
  }

  async function boot() {
    bind();
    try {
      const res = await fetch("data/sample.json", { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
      state.leads = state.data.leads;
      renderAll();
    } catch (err) {
      $("demoNotice").innerHTML = `<b>✗ داده نمونه بارگذاری نشد:</b> ${esc(err.message)}.
        این صفحه باید از طریق HTTP باز شود (باز کردن مستقیم فایل با <code>file://</code> در بعضی مرورگرها
        <code>fetch</code> را مسدود می‌کند). برای اجرای محلی:
        <code>python3 -m http.server -d site 8080</code> و سپس <code>http://localhost:8080</code>.`;
      toast("بارگذاری داده نمونه ناموفق بود", "err");
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
