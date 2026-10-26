"""کتابخانه اصناف (Niche Catalog) + موتور گسترش کلیدواژه.

هدف: برنامه برای **همه اصناف** کار کند. هر صنف شامل:
  • نام فارسی/انگلیسی و نام‌های مستعار (برای جست‌وجوی آزاد کاربر)
  • الگوهای کلیدواژه (که مکان داخلشان bake می‌شود: «plumbers in Denver CO»)
  • شعاع/عمق پیشنهادی برای job موتور
  • سطح نیاز دیجیتال (برای امتیازدهی و انتخاب زاویه کمپین)
  • نقاط درد و قلاب‌های بازاریابی (برای تولید متن outreach فارسی)

اگر صنفی پیدا نشود، `find_niche` یک صنف «سفارشی» از همان عبارت کاربر می‌سازد —
یعنی هیچ صنفی خارج از پوشش نیست.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .geo import NEIGHBORHOODS, _normalize


@dataclass
class Niche:
    id: str
    group: str
    fa: str
    en: str
    keywords_en: list[str] = field(default_factory=list)
    keywords_fa: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    radius: int = 10000
    depth: int = 5
    digital_need: str = "medium"          # high | medium | low
    ticket: str = "medium"                # low | medium | high — ارزش تقریبی مشتری آن صنف
    pains: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    custom: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "group": self.group,
            "fa": self.fa,
            "en": self.en,
            "aliases": self.aliases,
            "radius": self.radius,
            "depth": self.depth,
            "digital_need": self.digital_need,
            "ticket": self.ticket,
            "pains": self.pains,
            "hooks": self.hooks,
            "custom": self.custom,
        }


def _n(id, group, fa, en, en_kw, fa_kw, aliases=(), radius=10000, depth=5,
       digital="medium", ticket="medium", pains=(), hooks=()) -> Niche:
    return Niche(
        id=id, group=group, fa=fa, en=en,
        keywords_en=list(en_kw), keywords_fa=list(fa_kw), aliases=list(aliases),
        radius=radius, depth=depth, digital_need=digital, ticket=ticket,
        pains=list(pains), hooks=list(hooks),
    )


# ── نقاط درد و قلاب‌های مشترک (کوتاه و قابل ترکیب در متن کمپین) ───────────────
_P_NO_SITE = "هنوز وب‌سایت ندارد و مشتری‌ها فقط با تماس یا حضوری پیدایش می‌کنند"
_P_NO_BOOKING = "رزرو/نوبت‌دهی آنلاین ندارد و تماس‌های از دست رفته زیاد است"
_P_FEW_REVIEWS = "تعداد نظرات کم است و در نتایج گوگل‌مپ پایین‌تر از رقبا دیده می‌شود"
_P_NO_INSTA = "حضور فعال در اینستاگرام ندارد"
_P_MANUAL = "کارهای تبلیغاتی و ثبت سفارش را دستی و تلفنی انجام می‌دهد"
_H_SITE = "یک وب‌سایت سریع و موبایلی با فرم درخواست و نقشه"
_H_SEO = "بهینه‌سازی پروفایل گوگل‌مپ تا در جست‌وجوی محلی بالاتر دیده شود"
_H_ADS = "کمپین تبلیغاتی هدفمند روی محله و صنف خودش"
_H_BOOK = "سامانه رزرو/نوبت‌دهی آنلاین و یادآوری پیامکی"
_H_SOCIAL = "تولید محتوا و مدیریت اینستاگرام برای جذب مشتری محلی"
_H_CRM = "سیستم پیگیری مشتری و پیامک انبوه با بازگشت سرمایه مشخص"

CATALOG: list[Niche] = [
    # ═══════════════ سلامت و پزشکی ═══════════════
    _n("dentist", "سلامت و پزشکی", "دندانپزشکی", "Dentists",
       ["dentists", "dental clinics", "dental offices", "pediatric dentists", "cosmetic dentistry"],
       ["دندانپزشکی", "کلینیک دندانپزشکی", "مطب دندانپزشک", "دندانپزشک کودکان", "ایمپلنت دندان"],
       ["دندان پزشک", "دندانپزشک", "dentistry", "dental"], radius=12000, depth=8,
       digital="high", ticket="high",
       pains=[_P_NO_BOOKING, _P_FEW_REVIEWS, _P_NO_SITE],
       hooks=[_H_BOOK, _H_SEO, _H_SITE]),
    _n("orthodontist", "سلامت و پزشکی", "ارتودنسی", "Orthodontists",
       ["orthodontists", "orthodontic clinics", "braces clinic", "invisible aligners"],
       ["ارتودنسی", "کلینیک ارتودنسی", "متخصص ارتودنسی"],
       ["ارتودنتیست", "orthodontics"], digital="high", ticket="high",
       pains=[_P_NO_BOOKING, _P_FEW_REVIEWS], hooks=[_H_BOOK, _H_SEO]),
    _n("doctor", "سلامت و پزشکی", "پزشک و مطب", "Doctors & Clinics",
       ["doctors", "medical clinics", "general practitioners", "specialist doctors", "private clinics"],
       ["مطب پزشک", "کلینیک پزشکی", "پزشک متخصص", "درمانگاه"],
       ["doctor", "پزشکان", "clinic"], radius=12000, depth=8,
       digital="high", ticket="high",
       pains=[_P_NO_BOOKING, _P_MANUAL], hooks=[_H_BOOK, _H_CRM]),
    _n("dermatologist", "سلامت و پزشکی", "پوست و مو و لیزر", "Dermatology & Hair Clinics",
       ["dermatologists", "skin clinics", "hair clinics", "laser hair removal clinics"],
       ["کلینیک پوست و مو", "متخصص پوست", "کلینیک لیزر", "لیزر موهای زائد"],
       ["پوست و زیبایی", "dermatology"], digital="high", ticket="high",
       pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_SOCIAL, _H_BOOK]),
    _n("ophthalmologist", "سلامت و پزشکی", "چشم‌پزشکی", "Ophthalmologists",
       ["ophthalmologists", "eye clinics", "lasik surgery centers", "optometry clinics"],
       ["چشم پزشکی", "کلینیک چشم", "لیزیک", "بینایی‌سنجی"],
       ["چشم پزشک", "eye doctor"], digital="high", ticket="high",
       pains=[_P_NO_BOOKING], hooks=[_H_BOOK, _H_SEO]),
    _n("physiotherapy", "سلامت و پزشکی", "فیزیوتراپی و توانبخشی", "Physiotherapy Centers",
       ["physiotherapy clinics", "rehabilitation centers", "physical therapy", "sports injury clinics"],
       ["فیزیوتراپی", "کلینیک توانبخشی", "کاردرمانی"],
       ["فیزیوتراپ", "physio"], digital="high", ticket="medium",
       pains=[_P_NO_BOOKING, _P_FEW_REVIEWS], hooks=[_H_BOOK, _H_SEO]),
    _n("pharmacy", "سلامت و پزشکی", "داروخانه", "Pharmacies",
       ["pharmacies", "drugstores", "24 hour pharmacies", "compounding pharmacies"],
       ["داروخانه", "داروخانه شبانه‌روزی"],
       ["pharmacy", "داروسازی"], radius=6000, depth=6,
       digital="medium", ticket="low",
       pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),
    _n("lab", "سلامت و پزشکی", "آزمایشگاه و تصویربرداری", "Medical Labs & Imaging",
       ["medical laboratories", "blood test labs", "MRI centers", "ultrasound clinics", "imaging centers"],
       ["آزمایشگاه پزشکی", "آزمایشگاه تشخیص طبی", "سونوگرافی", "مرکز ام آر آی", "رادیولوژی"],
       ["آزمایشگاه", "lab"], digital="high", ticket="medium",
       pains=[_P_NO_BOOKING, _P_MANUAL], hooks=[_H_BOOK, _H_SITE]),
    _n("vet", "سلامت و پزشکی", "دامپزشکی و حیوانات خانگی", "Veterinary Clinics",
       ["veterinary clinics", "animal hospitals", "pet hospitals", "vets"],
       ["کلینیک دامپزشکی", "بیمارستان حیوانات", "دامپزشک"],
       ["veterinarian", "پت کلینیک"], digital="high", ticket="medium",
       pains=[_P_NO_BOOKING, _P_NO_INSTA], hooks=[_H_BOOK, _H_SOCIAL]),
    _n("psychologist", "سلامت و پزشکی", "روانشناسی و مشاوره", "Psychologists & Counselors",
       ["psychologists", "counseling centers", "psychotherapy clinics", "family counseling"],
       ["کلینیک روانشناسی", "مشاوره خانواده", "روان‌درمانی", "کلینیک مشاوره"],
       ["روانپزشک", "psychiatrist"], digital="high", ticket="medium",
       pains=[_P_NO_BOOKING, _P_NO_SITE], hooks=[_H_BOOK, _H_SITE]),
    _n("aesthetic-clinic", "سلامت و پزشکی", "کلینیک زیبایی", "Aesthetic & Beauty Clinics",
       ["aesthetic clinics", "beauty clinics", "botox clinics", "slimming clinics"],
       ["کلینیک زیبایی", "بوتاکس و فیلر", "کلینیک لاغری", "جوانسازی پوست"],
       ["زیبایی", "aesthetic"], digital="high", ticket="high",
       pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_SOCIAL, _H_BOOK]),

    # ═══════════════ زیبایی و آرایشگاه ═══════════════
    _n("hair-salon", "زیبایی و آرایشگاه", "سالن زیبایی و آرایشگاه زنانه", "Hair & Beauty Salons",
       ["hair salons", "beauty salons", "women's hair salons", "hair stylists"],
       ["سالن زیبایی", "آرایشگاه زنانه", "سالن مو", "کراتین و احیا مو"],
       ["salon", "بوتی سنتر"], radius=6000, depth=6,
       digital="high", ticket="medium",
       pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_SOCIAL, _H_BOOK]),
    _n("barbershop", "زیبایی و آرایشگاه", "آرایشگاه مردانه", "Barbershops",
       ["barbershops", "barbers", "men's haircuts", "beard trims"],
       ["آرایشگاه مردانه", "سلمانی", "اصلاح مو و ریش"],
       ["barber"], radius=5000, depth=6,
       digital="high", ticket="low",
       pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_BOOK, _H_SOCIAL]),
    _n("nail-salon", "زیبایی و آرایشگاه", "ناخن و مژه", "Nail & Lash Studios",
       ["nail salons", "nail studios", "eyelash extension salons", "manicure pedicure"],
       ["سالن ناخن", "کاشت ناخن", "اکستنشن مژه", "مانیکور و پدیکور"],
       ["nail"], radius=5000, depth=6,
       digital="high", ticket="low",
       pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_SOCIAL, _H_BOOK]),
    _n("spa", "زیبایی و آرایشگاه", "اسپا و ماساژ", "Spa & Massage Centers",
       ["spa centers", "massage therapy", "day spas", "sauna and hammam"],
       ["اسپا", "ماساژ درمانی", "سونا و جکوزی", "حمام سنتی"],
       ["massage"], digital="high", ticket="high",
       pains=[_P_NO_BOOKING, _P_NO_SITE], hooks=[_H_BOOK, _H_SITE]),

    # ═══════════════ غذا و نوشیدنی ═══════════════
    _n("restaurant", "غذا و نوشیدنی", "رستوران", "Restaurants",
       ["restaurants", "family restaurants", "traditional restaurants", "fine dining"],
       ["رستوران", "رستوران سنتی", "غذاخوری", "چلوکبابی"],
       ["restaurant"], radius=8000, depth=8,
       digital="high", ticket="medium",
       pains=[_P_NO_SITE, "منوی آنلاین و سفارش اینترنتی ندارد", _P_FEW_REVIEWS],
       hooks=["منوی دیجیتال و سفارش آنلاین", _H_SEO, _H_SOCIAL]),
    _n("cafe", "غذا و نوشیدنی", "کافه", "Cafés & Coffee Shops",
       ["cafes", "coffee shops", "specialty coffee", "dessert cafes"],
       ["کافه", "کافی‌شاپ", "قهوه فروشی", "کافه کتاب"],
       ["café", "coffee"], radius=6000, depth=8,
       digital="high", ticket="low",
       pains=[_P_NO_INSTA, _P_NO_SITE], hooks=[_H_SOCIAL, _H_SITE]),
    _n("fast-food", "غذا و نوشیدنی", "فست‌فود و پیتزا", "Fast Food & Pizza",
       ["fast food restaurants", "pizza places", "burger joints", "sandwich shops", "fried chicken"],
       ["فست فود", "پیتزا فروشی", "برگری", "ساندویچی", "مرغ سوخاری"],
       ["pizza", "burger"], radius=6000, depth=8,
       digital="high", ticket="low",
       pains=["سفارش تلفنی دارد ولی سامانه سفارش آنلاین ندارد", _P_NO_INSTA],
       hooks=["سامانه سفارش آنلاین و پیک", _H_ADS]),
    _n("bakery", "غذا و نوشیدنی", "نان و شیرینی", "Bakeries & Pastry",
       ["bakeries", "pastry shops", "cake shops", "confectioneries", "artisan bread"],
       ["نانوایی", "شیرینی‌فروشی", "کیک و شیرینی", "نان فانتزی"],
       ["bakery", "قنادی"], radius=5000, depth=6,
       digital="medium", ticket="low",
       pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SOCIAL, _H_SITE]),
    _n("catering", "غذا و نوشیدنی", "کیترینگ و پذیرایی", "Catering Services",
       ["catering services", "event catering", "corporate catering", "wedding catering"],
       ["کیترینگ", "تهیه غذا", "خدمات پذیرایی", "غذای سازمانی"],
       ["catering", "تهیه غذای"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),

    # ═══════════════ خدمات فنی و ساختمان ═══════════════
    _n("plumber", "خدمات فنی و ساختمان", "لوله‌کشی", "Plumbers",
       ["plumbers", "emergency plumbers", "plumbing services", "drain cleaning"],
       ["لوله کشی", "لوله‌کش ساختمان", "تعمیر لوله و فاضلاب", "نشتی‌یابی"],
       ["plumbing"], radius=15000, depth=6,
       digital="high", ticket="medium",
       pains=[_P_NO_SITE, "مشتری‌ها فقط از دیوار و اسنپ‌پیدایش می‌کنند", _P_FEW_REVIEWS],
       hooks=[_H_SITE, _H_SEO, _H_ADS]),
    _n("electrician", "خدمات فنی و ساختمان", "برق‌کاری", "Electricians",
       ["electricians", "electrical contractors", "emergency electricians", "lighting installation"],
       ["برق کاری", "برقکار ساختمان", "سیم‌کشی", "نصب چراغ و تابلو برق"],
       ["electrical"], radius=15000, depth=6,
       digital="high", ticket="medium",
       pains=[_P_NO_SITE, _P_FEW_REVIEWS], hooks=[_H_SITE, _H_SEO]),
    _n("hvac", "خدمات فنی و ساختمان", "تهویه مطبوع و پکیج", "HVAC & Heating",
       ["hvac contractors", "air conditioning repair", "heating services", "boiler installation"],
       ["تعمیر پکیج", "نصب کولر گازی", "تهویه مطبوع", "تعمیرات سیستم گرمایش"],
       ["hvac", "پکیج و کولر"], radius=15000, depth=6,
       digital="high", ticket="medium",
       pains=[_P_NO_SITE, "فصلی کار می‌کند و در فصل شلوغی لید کم دارد"], hooks=[_H_ADS, _H_SITE]),
    _n("roofing", "خدمات فنی و ساختمان", "سقف و ایزوگام", "Roofing Contractors",
       ["roofing contractors", "roof repair", "waterproofing", "gutter installation"],
       ["ایزوگام", "پشت بام و آب‌بندی", "تعمیر سقف", "شیروانی"],
       ["roofing", "ایزوگام کاری"], radius=20000, depth=5,
       digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_FEW_REVIEWS], hooks=[_H_SITE, _H_SEO]),
    _n("painter", "خدمات فنی و ساختمان", "نقاشی ساختمان", "Painters",
       ["house painters", "painting contractors", "interior painters", "wall painting services"],
       ["نقاشی ساختمان", "نقاش ساختمان", "رنگ‌آمیزی داخلی"],
       ["painting"], radius=15000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_ADS]),
    _n("carpenter", "خدمات فنی و ساختمان", "کابینت و نجاری", "Carpenters & Cabinet Makers",
       ["carpenters", "cabinet makers", "custom furniture makers", "woodworking shops"],
       ["کابینت سازی", "نجاری", "ساخت کمد و کابینت", "مبلمان سفارشی"],
       ["cabinet", "carpentry"], radius=15000, depth=5,
       digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SITE, _H_SOCIAL]),
    _n("interior-design", "خدمات فنی و ساختمان", "طراحی داخلی و دکوراسیون", "Interior Designers",
       ["interior designers", "interior design studios", "home decorators", "kitchen designers"],
       ["طراحی داخلی", "دکوراسیون داخلی", "طراحی دکور"],
       ["decorator"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SOCIAL, _H_SITE]),
    _n("architect", "خدمات فنی و ساختمان", "مهندسی و معماری", "Architects & Engineers",
       ["architects", "architecture firms", "civil engineers", "structural engineers"],
       ["مهندس معمار", "دفتر مهندسی", "شرکت معماری", "نظام مهندسی"],
       ["architecture"], digital="medium", ticket="high",
       pains=[_P_NO_SITE], hooks=[_H_SITE, _H_SEO]),
    _n("contractor", "خدمات فنی و ساختمان", "پیمانکاری و ساخت‌وساز", "Construction Contractors",
       ["construction contractors", "building contractors", "general contractors", "renovation companies"],
       ["پیمانکاری ساختمان", "شرکت ساختمانی", "بازسازی منزل", "پیمانکار"],
       ["construction"], radius=20000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),
    _n("cleaning", "خدمات فنی و ساختمان", "خدمات نظافتی", "Cleaning Services",
       ["cleaning services", "house cleaning", "office cleaning", "carpet cleaning", "window cleaning"],
       ["خدمات نظافتی", "نظافت منزل", "نظافت راه پله", "قالیشویی", "نظافت شرکت"],
       ["cleaning", "نظافتچی"], radius=15000, depth=6,
       digital="high", ticket="low", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_BOOK, _H_SITE]),
    _n("moving", "خدمات فنی و ساختمان", "اسباب‌کشی و حمل بار", "Moving Companies",
       ["moving companies", "house removals", "furniture movers", "local movers"],
       ["اسباب کشی", "باربری", "حمل لوازم منزل", "کارگر اسباب‌کشی"],
       ["moving", "باربری تهران"], radius=20000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE, _P_FEW_REVIEWS], hooks=[_H_SITE, _H_SEO]),
    _n("locksmith", "خدمات فنی و ساختمان", "کلیدسازی", "Locksmiths",
       ["locksmiths", "24 hour locksmiths", "car key replacement", "safe opening"],
       ["کلیدسازی", "کلیدسازی شبانه‌روزی", "تعویض قفل"],
       ["locksmith"], radius=8000, depth=5,
       digital="high", ticket="low", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_ADS]),
    _n("appliance-repair", "خدمات فنی و ساختمان", "تعمیر لوازم خانگی", "Appliance Repair",
       ["appliance repair", "washing machine repair", "refrigerator repair", "dishwasher repair"],
       ["تعمیر لوازم خانگی", "تعمیر یخچال", "تعمیر لباسشویی", "تعمیر جاروبرقی"],
       ["appliance"], radius=15000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE, _P_FEW_REVIEWS], hooks=[_H_SITE, _H_BOOK]),
    _n("security-systems", "خدمات فنی و ساختمان", "دوربین مداربسته و دزدگیر", "Security Systems",
       ["cctv installation", "security camera companies", "alarm systems", "access control"],
       ["دوربین مداربسته", "نصب دزدگیر", "سیستم اعلام حریق", "کنترل تردد"],
       ["cctv", "دوربین"], digital="high", ticket="high",
       pains=[_P_NO_SITE], hooks=[_H_SITE, _H_SEO]),
    _n("windows-doors", "خدمات فنی و ساختمان", "درب و پنجره دوجداره", "Windows & Doors",
       ["double glazed windows", "window installers", "door manufacturers", "aluminum windows"],
       ["پنجره دوجداره", "درب و پنجره upvc", "آلومینیوم سازی"],
       ["upvc", "window"], radius=20000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_SEO]),

    # ═══════════════ خودرو ═══════════════
    _n("car-repair", "خودرو", "تعمیرگاه خودرو", "Car Repair & Mechanics",
       ["auto repair shops", "car mechanics", "garages", "car service centers"],
       ["تعمیرگاه خودرو", "مکانیکی", "صافکاری و نقاشی خودرو", "تعویض روغنی"],
       ["mechanic", "garage"], radius=12000, depth=6,
       digital="high", ticket="medium",
       pains=[_P_NO_BOOKING, _P_FEW_REVIEWS], hooks=[_H_BOOK, _H_SEO]),
    _n("car-wash", "خودرو", "کارواش و دیتیلیگ", "Car Wash & Detailing",
       ["car wash", "auto detailing", "ceramic coating", "car valeting"],
       ["کارواش", "صفرشویی خودرو", "سرامیک خودرو", "روشویی"],
       ["detailing", "carwash"], radius=8000, depth=6,
       digital="high", ticket="low", pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_SOCIAL, _H_BOOK]),
    _n("tire-shop", "خودرو", "لاستیک و آپاراتی", "Tire Shops",
       ["tire shops", "tyre fitting", "wheel alignment", "tire repair"],
       ["لاستیک فروشی", "آپاراتی", "تنظیم فرمان و بالانس"],
       ["tire"], radius=10000, depth=5,
       digital="medium", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_BOOK]),
    _n("car-dealer", "خودرو", "نمایشگاه خودرو", "Car Dealerships",
       ["car dealerships", "used car dealers", "car showrooms", "auto dealers"],
       ["نمایشگاه خودرو", "خرید و فروش ماشین", "بنگاه ماشین"],
       ["car dealer"], radius=15000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),

    # ═══════════════ حقوقی، مالی و املاک ═══════════════
    _n("lawyer", "حقوقی و مالی", "وکیل و دفتر حقوقی", "Lawyers & Law Firms",
       ["lawyers", "law firms", "attorneys", "legal consultants"],
       ["وکیل دادگستری", "دفتر حقوقی", "مشاوره حقوقی", "وکیل خانواده"],
       ["law firm", "attorney"], digital="high", ticket="high",
       pains=[_P_NO_SITE, "در جست‌وجوی گوگل برای حوزه تخصصی خودش دیده نمی‌شود"],
       hooks=[_H_SITE, _H_SEO]),
    _n("notary", "حقوقی و مالی", "دفتر اسناد رسمی", "Notary Offices",
       ["notary public", "notary offices", "notary services"],
       ["دفتر اسناد رسمی", "محضر", "دفترخانه"],
       ["notary"], digital="medium", ticket="medium",
       pains=[_P_NO_BOOKING, _P_MANUAL], hooks=[_H_BOOK, _H_SITE]),
    _n("accountant", "حقوقی و مالی", "حسابداری و مالیات", "Accountants & Tax Advisors",
       ["accountants", "accounting firms", "tax advisors", "bookkeeping services", "payroll services"],
       ["حسابداری", "دفتر حسابداری", "مشاور مالیاتی", "خدمات مالی و مالیاتی"],
       ["accounting", "tax"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),
    _n("insurance", "حقوقی و مالی", "بیمه", "Insurance Agencies",
       ["insurance agencies", "insurance brokers", "car insurance", "life insurance agents"],
       ["نمایندگی بیمه", "کارگزاری بیمه", "بیمه شخص ثالث", "بیمه عمر"],
       ["insurance"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_CRM, _H_ADS]),
    _n("real-estate", "حقوقی و مالی", "املاک و مستغلات", "Real Estate Agencies",
       ["real estate agencies", "real estate agents", "property management", "realty offices"],
       ["بنگاه املاک", "مشاور املاک", "دفتر املاک", "مدیریت ساختمان"],
       ["real estate", "amlak"], radius=8000, depth=8,
       digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_MANUAL, _P_FEW_REVIEWS], hooks=[_H_CRM, _H_SITE]),

    # ═══════════════ آموزش و ورزش ═══════════════
    _n("gym", "آموزش و ورزش", "باشگاه ورزشی", "Gyms & Fitness Centers",
       ["gyms", "fitness centers", "crossfit boxes", "pilates studios", "sports clubs"],
       ["باشگاه ورزشی", "باشگاه بدنسازی", "فیتنس", "پیلانتر", "کراس فیت"],
       ["gym", "sport"], radius=8000, depth=8,
       digital="high", ticket="medium",
       pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_SOCIAL, _H_BOOK]),
    _n("yoga", "آموزش و ورزش", "یوگا و مدیتیشن", "Yoga & Meditation Studios",
       ["yoga studios", "yoga classes", "meditation centers"],
       ["استودیو یوگا", "کلاس یوگا", "مدیتیشن"],
       ["yoga"], radius=8000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_BOOKING, _P_NO_INSTA], hooks=[_H_BOOK, _H_SOCIAL]),
    _n("swimming", "آموزش و ورزش", "استخر و شنا", "Swimming Pools & Classes",
       ["swimming pools", "swimming schools", "swim classes", "aquatic centers"],
       ["استخر", "آموزش شنا", "استخر سرپوشیده"],
       ["swimming"], radius=10000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_BOOKING], hooks=[_H_BOOK, _H_SITE]),
    _n("martial-arts", "آموزش و ورزش", "ورزش‌های رزمی", "Martial Arts Schools",
       ["martial arts schools", "karate dojos", "boxing gyms", "mma gyms", "taekwondo clubs"],
       ["باشگاه رزمی", "کاراته", "تکواندو", "بوکس", "کشتی"],
       ["martial arts"], radius=10000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA], hooks=[_H_SOCIAL, _H_BOOK]),
    _n("language-school", "آموزش و ورزش", "آموزشگاه زبان", "Language Institutes",
       ["language schools", "english institutes", "language academies", "ielts preparation"],
       ["آموزشگاه زبان", "کلاس زبان انگلیسی", "آمادگی آیلتس", "زبان آلمانی"],
       ["language institute"], radius=10000, depth=6,
       digital="high", ticket="medium",
       pains=[_P_NO_SITE, _P_NO_BOOKING], hooks=[_H_SITE, _H_BOOK]),
    _n("driving-school", "آموزش و ورزش", "آموزشگاه رانندگی", "Driving Schools",
       ["driving schools", "driving lessons", "driving instructors"],
       ["آموزشگاه رانندگی", "گواهینامه رانندگی"],
       ["driving school"], radius=12000, depth=5,
       digital="medium", ticket="low", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_BOOK]),
    _n("tutoring", "آموزش و ورزش", "تدریس خصوصی و کنکور", "Tutoring & Test Prep",
       ["tutoring centers", "private tutors", "test prep centers", "math tutors"],
       ["تدریس خصوصی", "کلاس کنکور", "آموزشگاه درسی", "معلم خصوصی"],
       ["tutoring"], radius=10000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_ADS]),
    _n("preschool", "آموزش و ورزش", "مهدکودک و پیش‌دبستانی", "Preschools & Kindergartens",
       ["preschools", "kindergartens", "daycares", "nursery schools"],
       ["مهد کودک", "پیش دبستانی", "خانه کودک"],
       ["kindergarten", "preschool"], radius=8000, depth=6,
       digital="high", ticket="medium",
       pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SITE, _H_SOCIAL]),
    _n("music-school", "آموزش و ورزش", "آموزشگاه موسیقی و هنر", "Music & Art Schools",
       ["music schools", "music academies", "art classes", "painting workshops"],
       ["آموزشگاه موسیقی", "کلاس گیتار", "کلاس پیانو", "آموزش نقاشی"],
       ["music school"], radius=10000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA, _P_NO_BOOKING], hooks=[_H_SOCIAL, _H_BOOK]),

    # ═══════════════ رویداد، هنر و رسانه ═══════════════
    _n("photographer", "رویداد و رسانه", "عکاسی و فیلمبرداری", "Photographers & Videographers",
       ["photographers", "wedding photographers", "portrait studios", "videographers", "photo studios"],
       ["عکاسی", "آتلیه عکاسی", "عکاس عروسی", "فیلمبرداری", "آتلیه کودک"],
       ["photography"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SITE, _H_SOCIAL]),
    _n("event-planner", "رویداد و رسانه", "برگزاری مراسم و جشن", "Event Planners",
       ["event planners", "wedding planners", "party organizers", "event decorators"],
       ["برگزاری جشن", "تشریفات مجالس", "برنامه‌ریزی عروسی", "دکور مراسم"],
       ["event"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SOCIAL, _H_SITE]),
    _n("wedding-hall", "رویداد و رسانه", "تالار پذیرایی و باغ تالار", "Wedding Halls & Banquet",
       ["wedding halls", "banquet halls", "event venues", "wedding gardens"],
       ["تالار پذیرایی", "باغ تالار", "سالن همایش", "تالار عروسی"],
       ["wedding hall"], radius=25000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE, _P_NO_BOOKING], hooks=[_H_SITE, _H_BOOK]),
    _n("florist", "رویداد و رسانه", "گل‌فروشی", "Florists",
       ["florists", "flower shops", "floral designers", "flower delivery"],
       ["گل فروشی", "گل‌آرایی", "سفارش گل"],
       ["florist"], radius=8000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA, _P_NO_SITE], hooks=[_H_SOCIAL, _H_SITE]),
    _n("print-shop", "رویداد و رسانه", "چاپ و تبلیغات محیطی", "Print Shops",
       ["print shops", "printing services", "large format printing", "signage makers"],
       ["چاپخانه", "چاپ دیجیتال", "چاپ بنر", "تابلو سازی", "چاپ افست"],
       ["printing"], radius=15000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_CRM]),

    # ═══════════════ خرده‌فروشی ═══════════════
    _n("clothing", "خرده‌فروشی", "پوشاک و لباس", "Clothing Stores",
       ["clothing stores", "fashion boutiques", "kids clothing", "sportswear shops"],
       ["فروشگاه پوشاک", "بوتیک", "لباس بچگانه", "لباس مجلسی"],
       ["clothing", "boutique"], radius=8000, depth=6,
       digital="high", ticket="medium", pains=[_P_NO_INSTA, _P_NO_SITE], hooks=[_H_SOCIAL, _H_SITE]),
    _n("shoes", "خرده‌فروشی", "کفش و کیف", "Shoe & Bag Stores",
       ["shoe stores", "sneaker shops", "leather goods", "bag stores"],
       ["کفش فروشی", "کیف و کفش", "کتانی"],
       ["shoes"], radius=8000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA], hooks=[_H_SOCIAL]),
    _n("jewelry", "خرده‌فروشی", "طلا و جواهر", "Jewelry Stores",
       ["jewelry stores", "gold shops", "jewelers", "watch stores"],
       ["طلا فروشی", "جواهرات", "ساعت فروشی"],
       ["jewelry", "gold"], radius=8000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SITE, _H_SOCIAL]),
    _n("furniture", "خرده‌فروشی", "مبلمان و دکوراسیون منزل", "Furniture Stores",
       ["furniture stores", "sofa shops", "mattress stores", "home decor shops"],
       ["مبل فروشی", "فروشگاه مبلمان", "تشک و سرویس خواب", "لوازم خانگی"],
       ["furniture"], radius=15000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_ADS]),
    _n("mobile-shop", "خرده‌فروشی", "موبایل و لوازم دیجیتال", "Mobile & Electronics Stores",
       ["mobile phone shops", "electronics stores", "computer stores", "phone repair shops"],
       ["فروشگاه موبایل", "لوازم جانبی موبایل", "فروشگاه کامپیوتر", "تعمیرات موبایل"],
       ["mobile", "digital"], radius=8000, depth=6,
       digital="high", ticket="medium", pains=[_P_NO_SITE, _P_NO_INSTA], hooks=[_H_SITE, _H_SOCIAL]),
    _n("supermarket", "خرده‌فروشی", "سوپرمارکت و هایپر", "Supermarkets & Groceries",
       ["supermarkets", "grocery stores", "hypermarkets", "mini markets"],
       ["سوپرمارکت", "هایپرمارکت", "میوه و تره بار", "خواربار فروشی"],
       ["grocery"], radius=6000, depth=6,
       digital="medium", ticket="low",
       pains=["سفارش آنلاین و ارسال ندارد"], hooks=["فروشگاه آنلاین و ارسال محلی", _H_CRM]),
    _n("hardware", "خرده‌فروشی", "ابزارآلات و یراق", "Hardware Stores",
       ["hardware stores", "tool shops", "building materials", "paint stores"],
       ["ابزار فروشی", "یراق آلات", "مصالح ساختمانی", "رنگ فروشی"],
       ["hardware"], radius=15000, depth=5,
       digital="medium", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_CRM]),
    _n("cosmetics-shop", "خرده‌فروشی", "لوازم آرایشی و بهداشتی", "Cosmetics Stores",
       ["cosmetics stores", "beauty supply shops", "perfume stores", "skincare shops"],
       ["لوازم آرایشی", "فروشگاه عطر", "محصولات بهداشتی"],
       ["cosmetics", "perfume"], radius=8000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA], hooks=[_H_SOCIAL, _H_SITE]),
    _n("optical-shop", "خرده‌فروشی", "عینک‌فروشی", "Optical Stores",
       ["optical stores", "eyewear shops", "opticians", "sunglasses stores"],
       ["عینک فروشی", "بینایی سنجی", "عینک طبی"],
       ["optical"], radius=8000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE, _P_NO_BOOKING], hooks=[_H_SITE, _H_BOOK]),
    _n("pet-shop", "خرده‌فروشی", "حیوانات خانگی و پت‌شاپ", "Pet Shops",
       ["pet shops", "pet stores", "pet supplies", "pet grooming"],
       ["پت شاپ", "لوازم حیوانات خانگی", "گرومینگ سگ و گربه"],
       ["pet"], radius=10000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA, _P_NO_SITE], hooks=[_H_SOCIAL, _H_SITE]),
    _n("carpet", "خرده‌فروشی", "فرش و قالی", "Carpet Stores",
       ["carpet stores", "rug shops", "handmade carpet dealers", "carpet cleaning"],
       ["فرش فروشی", "قالی", "فروش فرش دستباف"],
       ["carpet", "rug"], radius=15000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_ADS]),
    _n("sports-goods", "خرده‌فروشی", "لوازم ورزشی", "Sports Equipment Stores",
       ["sports equipment stores", "sporting goods", "fitness equipment shops", "bicycle shops"],
       ["لوازم ورزشی", "دوچرخه فروشی", "تجهیزات بدنسازی"],
       ["sports"], radius=10000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA], hooks=[_H_SOCIAL, _H_SITE]),
    _n("bookstore", "خرده‌فروشی", "کتاب‌فروشی و لوازم‌التحریر", "Bookstores & Stationery",
       ["bookstores", "book shops", "stationery stores", "office supplies"],
       ["کتاب فروشی", "لوازم تحریر", "شهر کتاب"],
       ["bookstore"], radius=8000, depth=5,
       digital="medium", ticket="low", pains=[_P_NO_INSTA], hooks=[_H_SOCIAL]),
    _n("toy-store", "خرده‌فروشی", "اسباب‌بازی و سیسمونی", "Toy & Baby Stores",
       ["toy stores", "baby stores", "kids shops", "baby clothing"],
       ["اسباب بازی فروشی", "سیسمونی", "لوازم نوزاد"],
       ["toys"], radius=8000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_INSTA, _P_NO_SITE], hooks=[_H_SOCIAL, _H_SITE]),

    # ═══════════════ سفر و اقامت ═══════════════
    _n("hotel", "سفر و اقامت", "هتل و اقامتگاه", "Hotels & Accommodation",
       ["hotels", "boutique hotels", "hostels", "guest houses", "apart hotels"],
       ["هتل", "هتل آپارتمان", "مهمانپذیر", "اقامتگاه بوم‌گردی"],
       ["hotel", "lodging"], radius=20000, depth=5,
       digital="high", ticket="high",
       pains=["رزرو مستقیم ندارد و به OTA کمیسیون می‌دهد", _P_FEW_REVIEWS],
       hooks=["موتور رزرو مستقیم روی سایت خودش", _H_SEO]),
    _n("travel-agency", "سفر و اقامت", "آژانس مسافرتی و تور", "Travel Agencies & Tours",
       ["travel agencies", "tour operators", "tourism companies", "visa services"],
       ["آژانس مسافرتی", "تور گردشگری", "خدمات ویزا", "بلیط هواپیما"],
       ["travel agency", "tour"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),

    # ═══════════════ فناوری و خدمات حرفه‌ای ═══════════════
    _n("web-agency", "فناوری و خدمات حرفه‌ای", "شرکت طراحی سایت و دیجیتال", "Web & Digital Agencies",
       ["web design agencies", "digital marketing agencies", "web development companies", "seo agencies"],
       ["طراحی سایت", "شرکت دیجیتال مارکتینگ", "سئو", "آژانس تبلیغاتی"],
       ["web design", "agency"], digital="high", ticket="high",
       pains=["برای خودش لید کم دارد"], hooks=["همکاری و ارجاع مشتری", _H_CRM]),
    _n("software", "فناوری و خدمات حرفه‌ای", "شرکت نرم‌افزاری و IT", "Software & IT Companies",
       ["software companies", "it services", "computer repair", "network support", "saas companies"],
       ["شرکت نرم افزاری", "خدمات شبکه", "پشتیبانی کامپیوتر", "شرکت فناوری اطلاعات"],
       ["software", "it"], digital="medium", ticket="high",
       pains=["نیاز به متخصص و همکاری پروژه‌ای دارد"], hooks=["همکاری B2B", _H_CRM]),
    _n("law-consulting", "فناوری و خدمات حرفه‌ای", "مشاوره کسب‌وکار و مهاجرت", "Business & Migration Consulting",
       ["business consultants", "immigration consultants", "startup studios", "management consulting"],
       ["مشاوره کسب و کار", "مشاوره مهاجرت", "ثبت شرکت", "مشاور مدیریت"],
       ["consulting"], digital="high", ticket="high",
       pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),
    _n("coworking", "فناوری و خدمات حرفه‌ای", "فضای کار اشتراکی", "Coworking Spaces",
       ["coworking spaces", "shared offices", "serviced offices"],
       ["فضای کار اشتراکی", "دفتر کار اشتراکی", "کوافیکینگ"],
       ["coworking"], digital="high", ticket="medium",
       pains=[_P_NO_BOOKING], hooks=[_H_BOOK, _H_SOCIAL]),
    _n("recruitment", "فناوری و خدمات حرفه‌ای", "کاریابی و استخدام", "Recruitment Agencies",
       ["recruitment agencies", "staffing agencies", "job agencies", "headhunters"],
       ["کاریابی", "موسسه استخدام", "اعزام نیروی کار"],
       ["recruitment"], digital="high", ticket="medium",
       pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),

    # ═══════════════ صنعت و B2B ═══════════════
    _n("manufacturer", "صنعت و B2B", "کارخانه و تولیدی", "Manufacturers",
       ["manufacturers", "factories", "industrial production", "food factories"],
       ["کارخانه", "تولیدی", "کارگاه تولیدی", "واحد صنعتی"],
       ["factory", "manufacturing"], radius=25000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),
    _n("wholesaler", "صنعت و B2B", "عمده‌فروشی و بنکداری", "Wholesalers",
       ["wholesalers", "distributors", "wholesale markets", "b2b suppliers"],
       ["عمده فروشی", "پخش", "بنکداری", "تامین کننده"],
       ["wholesale", "distribution"], radius=20000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),
    _n("logistics", "صنعت و B2B", "حمل‌ونقل و لجستیک", "Logistics & Freight",
       ["logistics companies", "freight forwarding", "shipping companies", "warehouses"],
       ["شرکت حمل و نقل", "باربری بین شهری", "انبار و لجستیک", "ترابری"],
       ["logistics", "freight"], radius=25000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_CRM]),
    _n("packaging", "صنعت و B2B", "بسته‌بندی و چاپ صنعتی", "Packaging Companies",
       ["packaging companies", "box manufacturers", "label printing", "packaging supplies"],
       ["بسته بندی", "جعبه سازی", "کارتن سازی", "چاپ لیبل"],
       ["packaging"], radius=25000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_CRM]),
    _n("industrial-equipment", "صنعت و B2B", "تجهیزات صنعتی", "Industrial Equipment Suppliers",
       ["industrial equipment suppliers", "machinery dealers", "spare parts suppliers"],
       ["تجهیزات صنعتی", "ماشین آلات صنعتی", "قطعات یدکی صنعتی"],
       ["industrial"], radius=25000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_CRM]),

    # ═══════════════ خدمات محلی ═══════════════
    _n("laundry", "خدمات محلی", "خشکشویی و لباسشویی", "Laundry & Dry Cleaning",
       ["laundry services", "dry cleaners", "carpet cleaners", "ironing services"],
       ["خشکشویی", "قالی شویی", "لباسشویی صنعتی"],
       ["laundry"], radius=8000, depth=5,
       digital="high", ticket="low", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_BOOK, _H_SITE]),
    _n("tailor", "خدمات محلی", "خیاطی و مزون", "Tailors & Alterations",
       ["tailors", "alteration services", "custom tailoring", "dressmakers"],
       ["خیاطی", "مزون", "تعمیر لباس"],
       ["tailor"], radius=6000, depth=5,
       digital="high", ticket="low", pains=[_P_NO_INSTA], hooks=[_H_SOCIAL, _H_BOOK]),
    _n("gardening", "خدمات محلی", "فضای سبز و باغبانی", "Landscaping & Gardening",
       ["landscaping companies", "gardeners", "lawn care", "tree services", "greenhouse suppliers"],
       ["خدمات فضای سبز", "باغبانی", "طراحی حیاط و ویلا", "گلخانه"],
       ["landscaping", "gardening"], radius=20000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_ADS]),
    _n("pest-control", "خدمات محلی", "سم‌پاشی و دفع آفات", "Pest Control",
       ["pest control", "exterminators", "termite treatment", "rodent control"],
       ["سمپاشی", "دفع آفات", "مبارزه با موش و سوسک"],
       ["pest control"], radius=15000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE, _H_ADS]),
    _n("childcare", "خدمات محلی", "خدمات کودک و پرستاری", "Childcare & Nursing",
       ["nanny services", "childcare centers", "elderly care", "home nursing"],
       ["خدمات پرستاری", "مراقبت از سالمند", "پرستار کودک", "خدمات در منزل"],
       ["childcare", "nursing"], radius=12000, depth=5,
       digital="high", ticket="medium", pains=[_P_NO_SITE, _P_MANUAL], hooks=[_H_SITE, _H_BOOK]),
    _n("funeral", "خدمات محلی", "خدمات ترحیم و خاکسپاری", "Funeral Services",
       ["funeral homes", "funeral services", "cemeteries"],
       ["خدمات ترحیم", "تشییع و تدفین", "غسالخانه"],
       ["funeral"], radius=20000, depth=4,
       digital="medium", ticket="medium", pains=[_P_NO_SITE], hooks=[_H_SITE]),
    _n("car-rental", "خدمات محلی", "اجاره خودرو و تجهیزات", "Rental Services",
       ["car rental", "equipment rental", "party equipment rental", "van rental"],
       ["اجاره خودرو", "کرایه ماشین", "اجاره تجهیزات", "اجاره ون و مینی‌بوس"],
       ["rental"], radius=15000, depth=5,
       digital="high", ticket="high", pains=[_P_NO_BOOKING, _P_NO_SITE], hooks=[_H_BOOK, _H_SITE]),
]

BY_ID: dict[str, Niche] = {n.id: n for n in CATALOG}
GROUPS: list[str] = sorted({n.group for n in CATALOG})


def all_niches() -> list[dict]:
    return [n.to_dict() for n in sorted(CATALOG, key=lambda x: (x.group, x.fa))]


def get(niche_id: str) -> Niche | None:
    return BY_ID.get(_normalize_id(niche_id))


def _normalize_id(value: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "-", (value or "").strip().lower()).strip("-")


def custom_niche(query: str, lang: str = "fa") -> Niche:
    """ساخت صنف سفارشی از هر عبارت آزاد کاربر — پوشش «همه اصناف» بدون محدودیت فهرست."""
    clean = (query or "").strip()
    slug = _normalize_id(re.sub(r"[^\w\-]+", "-", clean)) or "custom"
    is_fa = bool(re.search(r"[\u0600-\u06FF]", clean))
    fa_kw = [clean] if is_fa else []
    en_kw = [clean] if not is_fa else []
    return Niche(
        id=f"custom-{slug[:40]}",
        group="سفارشی",
        fa=clean if is_fa else clean.title(),
        en=clean if not is_fa else clean,
        keywords_en=en_kw or [clean],
        keywords_fa=fa_kw or [],
        aliases=[clean],
        radius=12000,
        depth=6,
        digital_need="high",
        ticket="medium",
        pains=[_P_NO_SITE, _P_FEW_REVIEWS],
        hooks=[_H_SITE, _H_SEO],
        custom=True,
    )


def find_niche(query: str) -> Niche:
    """جست‌وجوی آزاد روی نام/مستعار/کلیدواژه‌ها؛ اگر نبود، صنف سفارشی می‌سازد."""
    q = _normalize(query or "")
    if not q:
        return CATALOG[0]
    if q in BY_ID:
        return BY_ID[q]

    best, best_score = None, 0.0
    for niche in CATALOG:
        haystacks = [
            _normalize(niche.id), _normalize(niche.fa), _normalize(niche.en),
            *[_normalize(a) for a in niche.aliases],
            *[_normalize(k) for k in niche.keywords_en],
            *[_normalize(k) for k in niche.keywords_fa],
        ]
        score = 0.0
        for hay in haystacks:
            if not hay:
                continue
            if hay == q:
                score = max(score, 1.0)
            elif q in hay or hay in q:
                score = max(score, 0.72 + 0.2 * min(len(q), len(hay)) / max(len(q), len(hay)))
            else:
                # همپوشانی توکن‌ها (برای عبارت‌های چندکلمه‌ای)
                q_tokens, h_tokens = set(q.split()), set(hay.split())
                if q_tokens & h_tokens:
                    overlap = len(q_tokens & h_tokens) / max(1, len(q_tokens))
                    score = max(score, 0.45 + 0.3 * overlap)
                ratio = SequenceMatcher(None, q, hay).ratio()
                score = max(score, ratio * 0.8)
        if score > best_score:
            best, best_score = niche, score
    if best and best_score >= 0.55:
        return best
    return custom_niche(query)


def expand_keywords(niche: Niche, city: str, lang: str = "en", limit: int = 6,
                    with_neighborhoods: bool = False, city_key: str | None = None) -> list[str]:
    """کلیدواژه‌های نهایی برای job — مکان داخل هر عبارت bake می‌شود (مطابق اسکیل کیت).

    مثال خروجی: ["dentists in Denver CO", "dental clinics in Denver CO", ...]
    """
    place = (city or "").strip()
    base = niche.keywords_fa if lang.startswith("fa") else niche.keywords_en
    if not base:
        base = niche.keywords_en or niche.keywords_fa or [niche.en]

    terms: list[str] = []
    for kw in base:
        if lang.startswith("fa") and re.search(r"[\u0600-\u06FF]", kw):
            terms.append(f"{kw} در {place}" if place else kw)
        else:
            terms.append(f"{kw} in {place}" if place else kw)

    if with_neighborhoods and city_key:
        for hood in NEIGHBORHOODS.get(city_key, [])[:4]:
            kw = base[0]
            if re.search(r"[\u0600-\u06FF]", kw):
                terms.append(f"{kw} {hood}")
            else:
                terms.append(f"{kw} in {hood}" + (f", {place}" if place else ""))

    # حذف تکراری با حفظ ترتیب
    seen, out = set(), []
    for t in terms:
        key = _normalize(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= max(1, limit):
            break
    return out
