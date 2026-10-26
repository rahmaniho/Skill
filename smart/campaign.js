/* LeadGen Studio — قالب‌های کمپین در مرورگر (آینهٔ leadgen/campaign.py)
 *
 * این ماژول فقط برای «حالت دمو» (بدون سرور پایتون، مثل نسخهٔ GitHub Pages) استفاده می‌شود؛
 * در حالت زنده، متن کمپین از API خود برنامه (/api/leads/{id}) می‌آید.
 * قالب‌ها عمداً با نسخهٔ پایتون یکسان نگه داشته شده‌اند تا خروجی دو نسخه فرق نکند.
 */
(() => {
"use strict";

// مشخصات فرستنده؛ برنامهٔ اصلی (app.js) این آبجکت را با ورودی‌های کاربر پر می‌کند.
const SENDER = {
  name: "", company: "", role: "مشاور رشد کسب‌وکارهای محلی", phone: "",
  email: "", website: "", offer: "۳۰ دقیقه مشاورهٔ رایگان", case_study: "",
};

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
    instagram: l.instagram || "", s: SENDER,
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

window.LeadCampaign = { CHANNEL_FA, messageFor, ctxOf, sender: SENDER };
})();
