"""ترجمهٔ صنف فارسی به «کلیدواژهٔ دسته‌ای» انگلیسی برای OpenStreetMap.

چرا لازم است: Nominatim برای عبارت‌های دسته‌ای انگلیسی (special phrases) جست‌وجوی
برچسبی انجام می‌دهد و صدها نتیجه می‌دهد، ولی متن آزاد فارسی را تقریباً «نام‌محور»
جست‌وجو می‌کند. نمونهٔ واقعی (تهران، جعبهٔ شهر):

    «دندانپزشکی»  → ۱ نتیجه
    «dentist»      → ۴۰+ نتیجه

پس برای هر صنف، معادل انگلیسی دسته‌ای هم امتحان می‌شود. فهرست زیر عمداً دستی و
کوچک است (نه ترجمهٔ ماشینی): فقط اصطلاح‌هایی که در OSM واقعاً تگ دارند.
"""

from __future__ import annotations

import re

# (الگوهای فارسی، اصطلاح‌های انگلیسی OSM) — از خاص به عام
LEXICON: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    # سلامت
    (("دندان",), ("dentist", "dental clinic")),
    (("پزشک", "درمانگاه", "کلینیک", "مطب"), ("clinic", "doctors", "medical centre")),
    (("بیمارستان",), ("hospital",)),
    (("داروخانه",), ("pharmacy",)),
    (("دامپزشک", "دامپزشکی"), ("veterinary",)),
    (("بینایی‌سنج", "عینک"), ("optician",)),
    (("فیزیوتراپی",), ("physiotherapist",)),
    (("روان‌شناس", "مشاوره"), ("psychotherapist", "counselling")),
    # زیبایی و آرایش
    (("آرایشگاه", "سالن زیبایی", "پیرایش", "کوتاهی مو"), ("hairdresser", "beauty salon")),
    (("اسپا", "ماساژ"), ("spa", "massage")),
    # خوراک
    (("رستوران", "غذاخوری"), ("restaurant", "fast food")),
    (("کافه", "قهوه"), ("cafe", "coffee shop")),
    (("فست‌فود", "ساندویچ", "پیتزا", "برگر"), ("fast food", "restaurant")),
    (("قنادی", "شیرینی", "کیک"), ("bakery", "confectionery", "pastry shop")),
    (("نانوایی", "نان"), ("bakery",)),
    (("بستنی", "آبمیوه", "کافه بستنی"), ("ice cream", "juice bar")),
    # آموزش
    (("موسیقی", "گیتار", "پیانو", "ویولن", "آواز"), ("music school", "musical instrument")),
    (("نقاشی", "هنر", "طراحی"), ("art school", "art gallery")),
    (("زبان", "آیلتس", "تافل"), ("language school",)),
    (("آموزشگاه", "آموزش", "کلاس", "مدرسه"), ("school", "college", "educational institution")),
    (("مهدکودک", "پیش‌دبستانی"), ("kindergarten",)),
    (("دانشگاه", "دانشکده"), ("university", "college")),
    (("کنکور", "مشاور تحصیلی"), ("school", "educational institution")),
    # ورزش
    (("باشگاه", "بدنسازی", "فیتنس", "کراس‌فیت"), ("gym", "fitness centre", "sports centre")),
    (("استخر", "شنا"), ("swimming pool", "sports centre")),
    (("فوتبال", "زمین چمن"), ("pitch", "sports centre")),
    (("پارک ورزش", "اسکیت", "پینت‌بال"), ("sports centre", "pitch")),
    (("یوگا", "پیلاتس"), ("fitness centre", "yoga studio")),
    # خرید و خدمات
    (("سوپرمارکت", "هایپرمارکت", "بقالی"), ("supermarket", "convenience store", "grocery")),
    (("فروشگاه", "مغازه", "بوتیک"), ("shop",)),
    (("پوشاک", "لباس", "مانتو"), ("clothes", "boutique")),
    (("کفش",), ("shoes",)),
    (("طلافروش", "طلا", "جواهر"), ("jewelry",)),
    (("فرش", "قالی"), ("carpet",)),
    (("لوازم خانگی", "لوازم برقی"), ("electronics", "houseware")),
    (("موبایل", "گوشی"), ("mobile phone", "electronics")),
    (("کامپیوتر", "لپ‌تاپ"), ("computer", "electronics")),
    (("کتاب", "کتابفروشی", "لوازم‌التحریر"), ("books", "stationery")),
    (("گل", "گل‌فروشی"), ("florist",)),
    (("نان خشک", "خشکبار", "آجیل"), ("confectionery", "nuts")),
    # خودرو
    (("خودرو", "ماشین", "اتوسرویس", "تعمیرگاه"), ("car repair", "car parts", "tyre")),
    (("کارواش", "شست‌وشو"), ("car wash",)),
    (("بنزین", "پمپ بنزین", "جایگاه سوخت"), ("fuel",)),
    (("لاستیک", "پنچرگیری"), ("tyre", "car repair")),
    (("نمایندگی خودرو",), ("car", "car dealership")),
    # مسکن، ساخت، حرفه
    (("مشاور املاک", "املاک"), ("estate agent",)),
    (("لوله‌کش", "تاسیسات"), ("plumber", "hardware")),
    (("برقکار", "برق"), ("electrician", "hardware")),
    (("نقاشی ساختمان",), ("painter",)),
    (("مصالح", "ساختمانی"), ("hardware", "doityourself")),
    (("مبل", "دکوراسیون"), ("furniture",)),
    (("آهن‌فروشی", "فلزات"), ("hardware",)),
    # فناوری و اداری
    (("دیجیتال مارکتینگ", "تبلیغات", "آژانس"), ("advertising agency", "marketing")),
    (("چاپ", "تبلیغات محیطی"), ("copyshop", "printing")),
    (("دفتر", "اداری", "حسابداری"), ("office", "accountant")),
    (("وکالت", "وکیل", "حقوقی"), ("lawyer", "office")),
    (("بیمه",), ("insurance",)),
    (("بانک", "صرافی"), ("bank", "bureau de change")),
    (("پست", "ارسال بار"), ("post office", "courier")),
    # اقامت و گردش
    (("هتل", "اقامتگاه"), ("hotel", "guest house")),
    (("آژانس مسافرتی", "تور"), ("travel agency",)),
    # صنعت و کشاورزی
    (("کارگاه", "کارخانه"), ("industrial", "factory")),
    (("کشاورزی", "تراکتور", "بذر"), ("farm", "agrarian")),
    (("گلخانه",), ("greenhouse", "garden centre")),
    # حیوانات
    (("پت‌شاپ", "حیوانات خانگی"), ("pet", "pet shop")),
]

# کلمه‌های عمومی که به‌تنهایی نتیجه نمی‌دهند و باید حذف شوند
_STOP = {
    "و", "در", "با", "از", "برای", "به", "کل", "همه", "تمام", "کسب", "وکار", "کسب‌وکار",
    "خدمات", "فروش", "تولید", "مرکز", "های", "ها", "همراه",
}

_PERSIAN = re.compile(r"[\u0600-\u06FF]")


def _norm(text: str) -> str:
    """یکسان‌سازی ساده: ی/ك عربی، اعراب، فاصله‌های تکرارشده."""
    text = (text or "").replace("ي", "ی").replace("ك", "ک")
    text = re.sub(r"[\u064B-\u0652\u0640]", "", text)
    text = text.replace("‌", " ")          # نیم‌فاصله → فاصله
    return re.sub(r"\s+", " ", text).strip().lower()


def has_persian(text: str) -> bool:
    return bool(_PERSIAN.search(text or ""))


# کلمه‌هایی که به‌تنهایی «هیچ» نیستند ولی برای جست‌وجوی یک‌کلمه‌ای بی‌فایده‌اند
_GENERIC_WORDS = {
    "آموزشگاه", "آموزشکده", "کلاس", "مرکز", "مجتمع", "فروشگاه", "شرکت", "اداره", "سازمان",
    "دفتر", "نمایندگی", "کارگاه", "باشگاه", "سالن", "کلینیک", "مطب", "تالار", "کافه",
    "رستوران", "هتل", "خدمات", "مهندسی", "مشاور", "مشاوره", "تولید", "پخش", "عرضه",
    "بهترین", "بهترین‌ها", "نزدیک", "من", "ما", "شهر", "استان", "ایران", "و", "در", "با",
    # کلمه‌های بسیار عام که در OSM فقط نویز می‌آورند (پارک هنر، ورزشگاه عمومی…)
    "هنر", "ورزش", "خرید", "عمومی", "لوکس", "مدرن", "سنتی",
}


def term_variants(term: str, *, limit: int = 2) -> list[str]:
    """کلمه‌های کلیدیِ یک عبارت فارسی، برای جست‌وجوی یک‌کلمه‌ای.

    نمونهٔ واقعی: «آموزشگاه موسیقی و هنر» در مهاباد با عبارت کامل صفر نتیجه داد،
    ولی «موسیقی» همان شهر را پیدا کرد (Kazhaw Music Academy با اینستاگرام و فیسبوک).
    """
    words = [w for w in _norm(term).split(" ") if len(w) >= 3 and w not in _STOP]
    if len(words) <= 1:
        return []
    specific = [w for w in words if w not in _GENERIC_WORDS]
    ordered = sorted(specific or words, key=len, reverse=True)
    return ordered[:limit]


def derive(texts: list[str] | tuple[str, ...], *, limit: int = 5) -> list[str]:
    """اصطلاح‌های انگلیسی OSM از متن فارسی/انگلیسیِ صنف.

    >>> derive(["آموزشگاه موسیقی و هنر"])[:2]
    ['music school', 'musical instrument']
    """
    blob = _norm(" ".join(t for t in texts if t))
    if not blob:
        return []
    lexicon_terms: list[str] = []
    for patterns, terms in LEXICON:
        if any(_norm(p) in blob for p in patterns):
            for term in terms:
                if term not in lexicon_terms:
                    lexicon_terms.append(term)
    # عبارت انگلیسی خودِ کاربر (مثل «music school») هم ارزشمند است، ولی **بعد** از
    # عبارت‌های واژه‌نامه: در تجربهٔ Production عبارت «coffee shops» در Nominatim صفر
    # داد و «cafe» ۴۶ نتیجه؛ چون موتور فقط چند ثانیه فرصت دارد، حدس اول باید بهترین باشد.
    snippets: list[str] = []
    for text in texts:
        cleaned = _norm(text)
        if cleaned and not has_persian(cleaned) and 2 < len(cleaned) <= 40:
            cleaned = re.sub(rf"\b({'|'.join(_STOP)})\b", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned and cleaned not in lexicon_terms and cleaned not in snippets:
                snippets.insert(0, cleaned)
    out = lexicon_terms + snippets
    # عبارت‌های ساده و انگلیسی‌خالص اول بیایند («cafés & coffee shops» به انتها)
    simple = [t for t in out if _plain(t)]
    return (simple + [t for t in out if not _plain(t)])[:limit]


def _plain(term: str) -> bool:
    """عبارت انگلیسیِ ساده (بدون حرف غیرانگلیسی/نماد و حداکثر سه کلمه)؟

    فقط برای **جابه‌جایی** استفاده می‌شود نه حذف: ترتیب معناییِ واژه‌نامه دست‌نخورده
    می‌ماند و فقط عبارت‌های نمایشی‌تر («cafés & coffee shops») به انتها می‌روند.
    """
    return (term.isascii() and not any(ch in term for ch in "&/(),.'\"")
            and len(term.split()) <= 3)
