"""نقشهٔ صنف ایرانی → تگ‌های واقعی OpenStreetMap (برای Overpass API).

چرا این ماژول؟
    جست‌وجوی متنی Nominatim برای «دندانپزشکی» در تهران ۱ نتیجه و برای «dentist»
    ۴۰ نتیجه می‌دهد؛ حتی در بهترین حالت سقف ۴۰ نتیجه دارد. Overpass مستقیماً
    **برچسب‌ها** را می‌خواند و می‌تواند همهٔ کسب‌وکارهای یک شهر را در یک درخواست
    برگرداند. برای اینکه این کار دقیق باشد، به نقشهٔ «صنف → تگ» نیاز داریم.

ساختار هر صنف:
    tags:  نام تگ OSM → مقادیر مجاز (چند مقدار = regex)
    names: بخش‌هایی از نام فارسی که در OSM ایران زیاد دیده می‌شوند
           (فقط در جست‌وجوی شهر استفاده می‌شود؛ در سطح استان گران است)

این نقشه دستی و محافظه‌کارانه است: تنها تگ‌هایی که واقعاً در OSM استفاده می‌شوند.
"""
from __future__ import annotations

# برچسب فارسی دسته‌های OSM (برای ستون category و نمایش)
CATEGORY_FA: dict[str, str] = {
    "restaurant": "رستوران", "fast_food": "فست‌فود", "cafe": "کافه", "bakery": "نانوایی",
    "pastry": "قنادی", "confectionery": "قنادی", "ice_cream": "بستنی",
    "dentist": "دندانپزشکی", "doctors": "پزشک", "clinic": "درمانگاه", "pharmacy": "داروخانه",
    "hospital": "بیمارستان", "veterinary": "دامپزشکی", "laboratory": "آزمایشگاه",
    "physiotherapist": "فیزیوتراپی", "psychotherapist": "روان‌شناسی", "optician": "عینک‌فروشی",
    "optometrist": "بینایی‌سنجی", "alternative": "درمان مکمل", "nursing_home": "خانه سالمندان",
    "car_repair": "تعمیرگاه خودرو", "car": "نمایشگاه خودرو", "car_wash": "کارواش",
    "tyres": "لاستیک و آپاراتی", "car_parts": "لوازم یدکی", "fuel": "جایگاه سوخت",
    "car_rental": "اجاره خودرو", "motorcycle_rental": "اجاره موتور",
    "hairdresser": "آرایشگاه", "beauty": "سالن زیبایی", "massage": "ماساژ", "spa": "اسپا",
    "clothes": "پوشاک", "boutique": "بوتیک", "shoes": "کفش", "jewelry": "طلا و جواهر",
    "florist": "گل‌فروشی", "furniture": "مبلمان", "electronics": "لوازم برقی",
    "mobile_phone": "موبایل", "computer": "کامپیوتر", "hardware": "ابزار و یراق",
    "doityourself": "ابزار و تعمیرات", "supermarket": "سوپرمارکت", "convenience": "فروشگاه",
    "greengrocer": "میوه و تره‌بار", "butcher": "قصابی", "carpet": "فرش و قالی",
    "cosmetics": "لوازم آرایشی", "chemist": "عطاری", "sports": "لوازم ورزشی",
    "toys": "اسباب‌بازی", "baby_goods": "سیسمونی", "books": "کتاب‌فروشی",
    "stationery": "لوازم‌التحریر", "pet": "پت‌شاپ", "pet_grooming": "آرایش حیوانات",
    "photo": "عکاسی", "copyshop": "کپی و چاپ", "printing": "چاپ", "travel_agency": "آژانس مسافرتی",
    "estate_agent": "مشاور املاک", "insurance": "بیمه", "accountant": "حسابداری",
    "lawyer": "وکیل", "notary": "دفتر اسناد رسمی", "employment_agency": "کاریابی",
    "architect": "مهندسی و معماری", "it": "خدمات کامپیوتری", "consulting": "مشاوره",
    "company": "شرکت", "educational_institution": "آموزشگاه", "advertising_agency": "تبلیغات",
    "carpenter": "نجاری", "plumber": "لوله‌کشی", "electrician": "برق‌کاری", "painter": "نقاشی",
    "hvac": "تهویه و تاسیسات", "locksmith": "کلیدسازی", "tailor": "خیاطی",
    "roofer": "سقف و ایزوگام", "window_construction": "درب و پنجره", "gardener": "باغبانی",
    "cleaning": "خدمات نظافتی", "builder": "پیمانکاری", "printer": "چاپ",
    "gym": "باشگاه ورزشی", "fitness_centre": "باشگاه ورزشی", "sports_centre": "مجموعه ورزشی",
    "swimming_pool": "استخر", "driving_school": "آموزشگاه رانندگی",
    "language_school": "آموزشگاه زبان", "music_school": "آموزشگاه موسیقی",
    "school": "مدرسه", "college": "دانشکده", "university": "دانشگاه",
    "kindergarten": "مهدکودک", "childcare": "مهدکودک", "prep_school": "آموزشگاه کنکور",
    "hotel": "هتل", "guest_house": "اقامتگاه", "motel": "متل", "hostel": "هاستل",
    "apartment": "آپارتمان مبله", "events_venue": "تالار مراسم", "community_centre": "تالار",
    "banquet_hall": "تالار پذیرایی", "funeral_hall": "تالار ترحیم",
    "funeral_directors": "خدمات ترحیم", "laundry": "خشکشویی", "dry_cleaning": "خشکشویی",
    "copyshop ": "کپی", "wholesale": "عمده‌فروشی", "trade": "بازرگانی", "kitchen": "کابینت",
    "interior_decoration": "دکوراسیون", "doors": "درب", "windows": "پنجره",
    "security_systems": "دوربین و دزدگیر", "watch": "ساعت", "bag": "کیف",
    "pastry_shop": "قنادی", "musical_instrument": "ساز", "garden_centre": "گل و گیاه",
    "appliance": "لوازم خانگی", "vacuum_cleaner": "لوازم خانگی", "lighting": "روشنایی",
    "houseware": "لوازم خانه", "bed": "تشک و تخت", "paint": "رنگ",
    "telecommunication": "مخابرات", "coworking_space": "فضای کار اشتراکی",
}

# صنف‌ها بر اساس شناسهٔ داخلی `niches.py`
SPEC: dict[str, dict] = {
    # ── سلامت و پزشکی ────────────────────────────────────────────────────────
    "dentist": {"tags": {"amenity": ["dentist"], "healthcare": ["dentist"]},
                "names": ["دندانپزشک", "دندان‌پزشک", "دندان"]},
    "orthodontist": {"tags": {"healthcare": ["orthodontics"], "amenity": ["dentist"]},
                     "names": ["ارتودنسی", "سیم کشی دندان"]},
    "doctor": {"tags": {"amenity": ["doctors"], "healthcare": ["doctor", "general_practitioner"]},
               "names": ["مطب", "پزشک", "دکتر"]},
    "dermatologist": {"tags": {"amenity": ["clinic"], "healthcare": ["dermatology"]},
                      "names": ["پوست و مو", "لیزر", "درماتولوژی"]},
    "ophthalmologist": {"tags": {"healthcare": ["ophthalmology", "optometry"]},
                        "names": ["چشم پزشک", "چشم‌پزشک", "بینایی"]},
    "aesthetic-clinic": {"tags": {"amenity": ["clinic"], "healthcare": ["clinic"]},
                         "names": ["کلینیک زیبایی", "پوست و زیبایی", "لیزر مو"]},
    "physiotherapy": {"tags": {"healthcare": ["physiotherapist"], "amenity": ["clinic"]},
                      "names": ["فیزیوتراپی", "توانبخشی"]},
    "psychologist": {"tags": {"healthcare": ["psychotherapist"], "amenity": ["clinic"]},
                     "names": ["روانشناس", "روان‌شناس", "مشاوره روان"]},
    "lab": {"tags": {"healthcare": ["laboratory"], "amenity": ["laboratory"]},
            "names": ["آزمایشگاه", "تصویربرداری", "سونوگرافی"]},
    "pharmacy": {"tags": {"amenity": ["pharmacy"], "shop": ["chemist"]},
                 "names": ["داروخانه", "درمانگاه شبانه‌روزی"]},
    "vet": {"tags": {"amenity": ["veterinary"], "shop": ["pet"]},
            "names": ["دامپزشک", "کلینیک حیوانات", "پت شاپ", "پت‌شاپ"]},

    # ── آموزش و ورزش ─────────────────────────────────────────────────────────
    "driving-school": {"tags": {"amenity": ["driving_school"]},
                       "names": ["آموزشگاه رانندگی", "آموزش رانندگی"]},
    "language-school": {"tags": {"amenity": ["language_school"], "office": ["educational_institution"]},
                        "names": ["آموزشگاه زبان", "زبان انگلیسی", "آیلتس", "تافل"]},
    "music-school": {"tags": {"amenity": ["music_school"], "shop": ["musical_instrument"]},
                     "names": ["آموزشگاه موسیقی", "آموزش موسیقی", "موسیقی"]},
    "swimming": {"tags": {"leisure": ["swimming_pool"], "sport": ["swimming"]},
                 "names": ["استخر", "شنا"]},
    "gym": {"tags": {"leisure": ["fitness_centre", "sports_centre"], "amenity": ["gym"]},
            "names": ["باشگاه", "بدنسازی", "فیتنس", "کراس فیت", "کراس‌فیت"]},
    "tutoring": {"tags": {"amenity": ["prep_school"], "office": ["educational_institution"]},
                 "names": ["تدریس خصوصی", "کنکور", "آموزشگاه علمی", "کلاس تقویتی"]},
    "preschool": {"tags": {"amenity": ["kindergarten", "childcare"]},
                  "names": ["مهدکودک", "مهد کودک", "پیش دبستانی", "پیش‌دبستانی"]},
    "martial-arts": {"tags": {"sport": ["martial_arts", "judo", "karate", "taekwondo"]},
                     "names": ["رزمی", "کاراته", "جودو", "تکواندو", "ووشو", "بوکس"]},
    "yoga": {"tags": {"sport": ["yoga"], "leisure": ["fitness_centre"]},
             "names": ["یوگا", "مدیتیشن", "پیلاتس"]},

    # ── حقوقی و مالی ─────────────────────────────────────────────────────────
    "real-estate": {"tags": {"office": ["estate_agent"]},
                    "names": ["مشاور املاک", "املاک", "مسکن"]},
    "insurance": {"tags": {"office": ["insurance"]},
                  "names": ["بیمه"]},
    "accountant": {"tags": {"office": ["accountant"]},
                   "names": ["حسابداری", "حسابدار", "مالیات"]},
    "notary": {"tags": {"office": ["notary"]},
               "names": ["دفتر اسناد رسمی", "دفترخانه", "محضری"]},
    "lawyer": {"tags": {"office": ["lawyer"]},
               "names": ["وکیل", "دفتر حقوقی", "دادگستری"]},

    # ── خدمات فنی و ساختمان ─────────────────────────────────────────────────
    "moving": {"tags": {"office": ["moving_company"], "shop": ["moving_company"]},
               "names": ["اسباب کشی", "اسباب‌کشی", "باربری", "حمل بار"]},
    "electrician": {"tags": {"craft": ["electrician"]},
                    "names": ["برقکار", "برق کاری", "برق‌کاری"]},
    "appliance-repair": {"tags": {"craft": ["electronics_repair"], "shop": ["appliance", "vacuum_cleaner"]},
                         "names": ["تعمیر لوازم خانگی", "تعمیر لباسشویی", "تعمیر یخچال", "تعمیرکار"]},
    "hvac": {"tags": {"craft": ["hvac"], "shop": ["hvac"]},
             "names": ["تهویه", "پکیج", "کولر گازی", "چیلر", "اسپلیت"]},
    "cleaning": {"tags": {"shop": ["cleaning"], "office": ["cleaning"], "craft": ["cleaning"]},
                 "names": ["نظافت", "خانه تکانی", "خدمات منزل"]},
    "windows-doors": {"tags": {"craft": ["window_construction"], "shop": ["doors", "windows"]},
                      "names": ["درب و پنجره", "یو پی وی سی", "upvc", "دو جداره"]},
    "security-systems": {"tags": {"shop": ["security_systems", "electronics"]},
                         "names": ["دوربین مداربسته", "دزدگیر", "کرکره برقی"]},
    "roofing": {"tags": {"craft": ["roofer"], "shop": ["roofing"]},
                "names": ["ایزوگام", "سقف", "آسفالت پشت بام"]},
    "interior-design": {"tags": {"shop": ["interior_decoration", "furniture"], "office": ["interior_design"]},
                        "names": ["طراحی داخلی", "دکوراسیون", "کابینت"]},
    "plumber": {"tags": {"craft": ["plumber"]},
                "names": ["لوله کش", "لوله‌کش", "تاسیسات", "لوله بازکن"]},
    "architect": {"tags": {"office": ["architect"]},
                  "names": ["مهندس", "معماری", "دفتر فنی"]},
    "painter": {"tags": {"craft": ["painter"], "shop": ["paint"]},
                "names": ["نقاشی ساختمان", "رنگ آمیزی", "پیمانکار نقاشی"]},
    "contractor": {"tags": {"office": ["construction_company"], "craft": ["builder"]},
                   "names": ["پیمانکاری", "ساخت و ساز", "ساختمانی"]},
    "carpenter": {"tags": {"craft": ["carpenter"], "shop": ["kitchen", "furniture"]},
                  "names": ["کابینت", "نجاری", "ام دی اف", "MDF"]},
    "locksmith": {"tags": {"craft": ["locksmith"], "shop": ["locksmith"]},
                  "names": ["کلید سازی", "کلیدسازی", "قفل"]},

    # ── خدمات محلی ───────────────────────────────────────────────────────────
    "car-rental": {"tags": {"amenity": ["car_rental"], "shop": ["car_rental"]},
                   "names": ["اجاره خودرو", "اجاره ماشین", "اجاره ون"]},
    "funeral": {"tags": {"shop": ["funeral_directors"], "amenity": ["funeral_hall"]},
                "names": ["ترحیم", "خاکسپاری", "بهشت زهرا", "غسالخانه"]},
    "childcare": {"tags": {"amenity": ["childcare", "kindergarten"]},
                  "names": ["پرستار کودک", "نگهداری کودک", "مهد"]},
    "laundry": {"tags": {"shop": ["laundry", "dry_cleaning"]},
                "names": ["خشکشویی", "لباسشویی", "اتو"]},
    "tailor": {"tags": {"craft": ["tailor"], "shop": ["tailor"]},
               "names": ["خیاطی", "مزون", "دوخت"]},
    "pest-control": {"tags": {"craft": ["pest_control"], "shop": ["pest_control"]},
                     "names": ["سم پاشی", "سم‌پاشی", "دفع آفات", "سوسک"]},
    "gardening": {"tags": {"shop": ["garden_centre"], "craft": ["gardener"]},
                  "names": ["باغبانی", "فضای سبز", "گیاه"]},

    # ── خرده‌فروشی ───────────────────────────────────────────────────────────
    "hardware": {"tags": {"shop": ["hardware", "doityourself", "trade"]},
                 "names": ["ابزار", "یراق", "پیچ و مهره"]},
    "toy-store": {"tags": {"shop": ["toys", "baby_goods"]},
                  "names": ["اسباب بازی", "اسباب‌بازی", "سیسمونی"]},
    "pet-shop": {"tags": {"shop": ["pet", "pet_grooming"]},
                 "names": ["پت شاپ", "پت‌شاپ", "لوازم حیوانات"]},
    "supermarket": {"tags": {"shop": ["supermarket", "convenience", "greengrocer"]},
                    "names": ["سوپرمارکت", "هایپر", "بقالی", "فروشگاه مواد غذایی"]},
    "jewelry": {"tags": {"shop": ["jewelry", "watches"]},
                "names": ["طلا", "جواهر", "زرگری"]},
    "optical-shop": {"tags": {"shop": ["optician"], "healthcare": ["optometry"]},
                     "names": ["عینک", "بینایی سنجی"]},
    "carpet": {"tags": {"shop": ["carpet"]},
               "names": ["فرش", "قالی", "موکت"]},
    "cosmetics-shop": {"tags": {"shop": ["cosmetics", "chemist"]},
                       "names": ["آرایشی", "بهداشتی", "لوازم آرایش"]},
    "sports-goods": {"tags": {"shop": ["sports"]},
                     "names": ["لوازم ورزشی", "پوشاک ورزشی"]},
    "furniture": {"tags": {"shop": ["furniture", "interior_decoration", "bed"]},
                  "names": ["مبل", "مبلمان", "دکوراسیون منزل"]},
    "mobile-shop": {"tags": {"shop": ["mobile_phone", "electronics", "computer"]},
                    "names": ["موبایل", "گوشی", "لپ تاپ", "کامپیوتر"]},
    "clothing": {"tags": {"shop": ["clothes", "boutique", "fabric"]},
                 "names": ["پوشاک", "لباس", "مانتو", "بوتیک"]},
    "bookstore": {"tags": {"shop": ["books", "stationery"]},
                  "names": ["کتاب", "لوازم التحریر", "لوازم‌التحریر", "نوشت افزار"]},
    "shoes": {"tags": {"shop": ["shoes", "bag"]},
              "names": ["کفش", "کیف", "دمپایی"]},

    # ── خودرو ────────────────────────────────────────────────────────────────
    "car-repair": {"tags": {"shop": ["car_repair"], "craft": ["car_repair"]},
                   "names": ["تعمیرگاه", "اتوسرویس", "مکانیکی", "صافکاری"]},
    "tire-shop": {"tags": {"shop": ["tyres"]},
                  "names": ["لاستیک", "پنچرگیری", "آپاراتی", "رینگ"]},
    "car-dealer": {"tags": {"shop": ["car", "caravan", "motorcycle"]},
                   "names": ["نمایشگاه خودرو", "اتومبیل", "خودرو"]},
    "car-wash": {"tags": {"amenity": ["car_wash"], "shop": ["car_wash"]},
                 "names": ["کارواش", "دیتیلینگ", "شستشوی خودرو"]},

    # ── رویداد و رسانه ───────────────────────────────────────────────────────
    "event-planner": {"tags": {"office": ["event_management"], "shop": ["event_management"]},
                      "names": ["تشریفات", "برگزاری مراسم", "تدارکات مراسم"]},
    "wedding-hall": {"tags": {"amenity": ["events_venue", "banquet_hall", "community_centre"]},
                     "names": ["تالار", "باغ تالار", "تالار پذیرایی"]},
    "photographer": {"tags": {"shop": ["photo"], "craft": ["photographer"], "office": ["photographer"]},
                     "names": ["عکاسی", "فیلمبرداری", "آتلیه"]},
    "print-shop": {"tags": {"shop": ["copyshop", "printing"], "craft": ["printer"]},
                   "names": ["چاپ", "تبلیغات", "بنر", "فلکس"]},
    "florist": {"tags": {"shop": ["florist"]},
                "names": ["گل فروشی", "گل‌فروشی", "گل آرایی"]},

    # ── زیبایی ───────────────────────────────────────────────────────────────
    "barbershop": {"tags": {"shop": ["hairdresser"]},
                   "names": ["آرایشگاه مردانه", "باربر", "پیرایش مردانه"]},
    "spa": {"tags": {"leisure": ["spa"], "shop": ["massage"]},
            "names": ["ماساژ", "اسپا", "سونای"]},
    "hair-salon": {"tags": {"shop": ["hairdresser", "beauty"]},
                   "names": ["سالن زیبایی", "آرایشگاه زنانه", "پیرایش"]},
    "nail-salon": {"tags": {"shop": ["beauty"]},
                   "names": ["ناخن", "مژه", "اکستنشن"]},

    # ── سفر و اقامت ──────────────────────────────────────────────────────────
    "travel-agency": {"tags": {"shop": ["travel_agency"], "office": ["travel_agent"]},
                      "names": ["آژانس مسافرتی", "تور", "زیارتی"]},
    "hotel": {"tags": {"tourism": ["hotel", "guest_house", "motel", "hostel", "apartment"]},
              "names": ["هتل", "اقامتگاه", "مهمانپذیر", "سوئیت"]},

    # ── صنعت و B2B ───────────────────────────────────────────────────────────
    "packaging": {"tags": {"shop": ["packaging"], "craft": ["packaging"]},
                  "names": ["بسته بندی", "بسته‌بندی", "کارتن"]},
    "industrial-equipment": {"tags": {"shop": ["trade", "industrial"], "office": ["industrial"]},
                             "names": ["تجهیزات صنعتی", "ماشین آلات", "ابزار صنعتی"]},
    "logistics": {"tags": {"office": ["logistics", "moving_company"]},
                  "names": ["حمل و نقل", "باربری", "لجستیک", "انبار"]},
    "wholesaler": {"tags": {"shop": ["wholesale", "trade"]},
                   "names": ["عمده فروشی", "بنکدار", "پخش"]},
    "manufacturer": {"tags": {"man_made": ["works"], "building": ["industrial", "factory"]},
                     "names": ["کارخانه", "تولیدی", "صنایع"]},

    # ── غذا و نوشیدنی ────────────────────────────────────────────────────────
    "restaurant": {"tags": {"amenity": ["restaurant"]},
                   "names": ["رستوران", "کباب", "غذاخوری", "سفره خانه"]},
    "fast-food": {"tags": {"amenity": ["fast_food"]},
                  "names": ["فست فود", "فست‌فود", "پیتزا", "ساندویچ", "برگر"]},
    "cafe": {"tags": {"amenity": ["cafe", "ice_cream"]},
             "names": ["کافه", "قهوه", "بستنی", "آبمیوه"]},
    "bakery": {"tags": {"shop": ["bakery", "pastry", "confectionery"]},
               "names": ["نانوایی", "قنادی", "شیرینی", "نان"]},
    "catering": {"tags": {"craft": ["caterer"], "shop": ["catering"]},
                 "names": ["کیترینگ", "کترینگ", "پذیرایی"]},

    # ── فناوری و خدمات حرفه‌ای ──────────────────────────────────────────────
    "web-agency": {"tags": {"office": ["it", "web_design"], "shop": ["computer"]},
                   "names": ["طراحی سایت", "وب", "دیجیتال مارکتینگ", "سئو"]},
    "software": {"tags": {"office": ["it", "software"], "shop": ["computer"]},
                 "names": ["نرم افزار", "نرم‌افزار", "برنامه نویسی", "IT"]},
    "coworking": {"tags": {"amenity": ["coworking_space"], "office": ["coworking"]},
                  "names": ["فضای کار اشتراکی", "کار اشتراکی", "همکار"]},
    "law-consulting": {"tags": {"office": ["consulting", "lawyer", "immigration"]},
                       "names": ["مشاوره کسب و کار", "مهاجرت", "مشاوره"]},
    "recruitment": {"tags": {"office": ["employment_agency"]},
                    "names": ["کاریابی", "استخدام", "جذب نیرو"]},
}

# کلیدواژه‌های انگلیسی → شناسهٔ صنف (برای ورودی‌های نامشخص و جست‌وجوی انگلیسی)
TERM_TO_NICHE: dict[str, str] = {
    "dentist": "dentist", "dental clinic": "dentist", "restaurant": "restaurant",
    "fast food": "fast-food", "cafe": "cafe", "coffee shop": "cafe", "bakery": "bakery",
    "pastry shop": "bakery", "confectionery": "bakery", "clinic": "doctor",
    "doctors": "doctor", "medical centre": "doctor", "hospital": "doctor",
    "pharmacy": "pharmacy", "veterinary": "vet", "optician": "optical-shop",
    "physiotherapist": "physiotherapy", "psychotherapist": "psychologist",
    "hairdresser": "hair-salon", "beauty salon": "hair-salon", "spa": "spa",
    "massage": "spa", "gym": "gym", "fitness centre": "gym", "sports centre": "gym",
    "swimming pool": "swimming", "music school": "music-school",
    "language school": "language-school", "school": "tutoring", "college": "tutoring",
    "educational institution": "tutoring", "kindergarten": "preschool",
    "car repair": "car-repair", "car wash": "car-wash", "tyre": "tire-shop",
    "car": "car-dealer", "estate agent": "real-estate", "insurance": "insurance",
    "accountant": "accountant", "lawyer": "lawyer", "notary": "notary",
    "supermarket": "supermarket", "convenience store": "supermarket",
    "grocery": "supermarket", "clothes": "clothing", "boutique": "clothing",
    "shoes": "shoes", "jewelry": "jewelry", "florist": "florist",
    "furniture": "furniture", "electronics": "mobile-shop", "mobile phone": "mobile-shop",
    "computer": "software", "books": "bookstore", "stationery": "bookstore",
    "carpet": "carpet", "toys": "toy-store", "pet": "pet-shop", "photo": "photographer",
    "copyshop": "print-shop", "printing": "print-shop", "travel agency": "travel-agency",
    "hotel": "hotel", "guest house": "hotel", "plumber": "plumber",
    "electrician": "electrician", "painter": "painter", "carpenter": "carpenter",
    "hvac": "hvac", "locksmith": "locksmith", "tailor": "tailor", "laundry": "laundry",
    "dry cleaning": "laundry", "hardware": "hardware", "doityourself": "hardware",
    "houseware": "mobile-shop", "sports": "sports-goods", "driving school": "driving-school",
    "car rental": "car-rental", "car parts": "car-repair", "fuel": "car-repair",
    "events venue": "wedding-hall", "coworking space": "coworking",
    "employment agency": "recruitment", "it": "web-agency", "consulting": "law-consulting",
    "company": "law-consulting", "advertising agency": "print-shop",
}

# فیلترهای «همهٔ کسب‌وکارها» — یک درخواست، کل لایهٔ تجاری شهر
ALL_BUSINESS_TAGS: list[tuple[str, tuple[str, ...] | None]] = [
    ("shop", None),
    ("craft", None),
    ("office", ("company", "estate_agent", "insurance", "accountant", "lawyer", "notary",
                "architect", "it", "consulting", "employment_agency", "travel_agent",
                "advertising_agency", "educational_institution", "moving_company",
                "logistics", "coworking", "web_design", "software", "event_management")),
    ("amenity", ("restaurant", "fast_food", "cafe", "bakery", "dentist", "doctors", "clinic",
                 "pharmacy", "veterinary", "car_repair", "car_wash", "car_rental", "bank",
                 "fuel", "kindergarten", "childcare", "driving_school", "language_school",
                 "music_school", "prep_school", "coworking_space", "ice_cream",
                 "events_venue", "banquet_hall", "funeral_hall", "marketplace")),
    ("healthcare", None),
    ("leisure", ("fitness_centre", "sports_centre", "swimming_pool", "spa")),
    ("tourism", ("hotel", "guest_house", "motel", "hostel", "apartment")),
    ("sport", ("martial_arts", "yoga")),
]

# چیزهایی که هرگز «کسب‌وکار» نیستند (حتی اگر تگ تجاری داشته باشند)
EXCLUDE_TAGS: tuple[tuple[str, str], ...] = (
    ("amenity", "parking"), ("amenity", "parking_space"), ("amenity", "bicycle_parking"),
    ("amenity", "bench"), ("amenity", "shelter"), ("amenity", "waste_basket"),
    ("amenity", "toilets"), ("amenity", "drinking_water"), ("amenity", "fountain"),
    ("amenity", "clock"), ("amenity", "atm"), ("amenity", "vending_machine"),
    ("amenity", "post_box"), ("amenity", "telephone"), ("amenity", "bus_station"),
    ("amenity", "shelter"), ("shop", "vacant"), ("shop", "no"),
    ("office", "government"), ("office", "administrative"), ("office", "diplomatic"),
    ("office", "political_party"), ("office", "religion"), ("office", "research"),
    ("tourism", "attraction"), ("tourism", "artwork"), ("tourism", "museum"),
    ("tourism", "viewpoint"), ("tourism", "information"), ("leisure", "park"),
    ("leisure", "garden"), ("leisure", "playground"), ("leisure", "pitch"),
    ("historic", "*"), ("boundary", "*"), ("highway", "*"), ("railway", "*"),
    ("place", "*"),
)

_ORDER_KEYS = ("amenity", "shop", "craft", "office", "healthcare", "tourism",
               "leisure", "sport", "man_made", "building")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _tag_block(key: str, values: tuple[str, ...] | list[str] | None) -> str:
    if not values:
        return f'["{key}"]'
    if len(values) == 1:
        return f'["{key}"="{_escape(values[0])}"]'
    return f'["{key}"~"^({"|".join(_escape(v) for v in values)})$"]'


def filters_for_niche(niche_id: str, *, with_names: bool = False) -> list[str]:
    """بلوک‌های فیلتر Overpass برای یک صنف (بدون پرانتز bbox)."""
    spec = SPEC.get(niche_id)
    if not spec:
        return []
    blocks = [_tag_block(key, values) for key, values in spec.get("tags", {}).items()]
    names = [n for n in spec.get("names", []) if n]
    if with_names and names and blocks:
        pattern = "|".join(_escape(n) for n in names)
        blocks.append(f'["name"~"{pattern}",i]')
    return blocks


def filters_for_terms(terms: list[str], *, with_names: bool = False) -> list[str]:
    """از اصطلاح‌های انگلیسی موتور (osm_terms) به فیلتر Overpass."""
    blocks: list[str] = []
    for term in terms:
        niche_id = TERM_TO_NICHE.get(str(term or "").strip().lower())
        if not niche_id:
            continue
        for block in filters_for_niche(niche_id, with_names=with_names):
            if block not in blocks:
                blocks.append(block)
    return blocks


def all_business_filters() -> list[str]:
    return [_tag_block(key, values) for key, values in ALL_BUSINESS_TAGS]


def is_excluded(tags: dict) -> bool:
    for key, value in EXCLUDE_TAGS:
        if value == "*":
            if tags.get(key):
                return True
        elif str(tags.get(key) or "") == value:
            return True
    return False


def _kind_of(tags: dict) -> tuple[str, str]:
    for key in _ORDER_KEYS:
        value = str(tags.get(key) or "").strip()
        if value and value not in ("yes", "no"):
            return key, value
    for key in _ORDER_KEYS:
        value = str(tags.get(key) or "").strip()
        if value:
            return key, ""
    return "", ""


def niche_for_tags(tags: dict) -> str:
    """تگ‌های OSM → شناسهٔ صنف داخلی (برای جاروی «همهٔ کسب‌وکارها»)."""
    for key, value in _ORDER_KEYS:
        if not tags.get(key):
            continue
        break
    best = ""
    for niche_id, spec in SPEC.items():
        for key, values in spec.get("tags", {}).items():
            if str(tags.get(key) or "") in values:
                best = niche_id
                break
        if best:
            break
    if best:
        return best
    name = str(tags.get("name") or tags.get("name:fa") or "")
    if name:
        for niche_id, spec in SPEC.items():
            if any(part and part in name for part in spec.get("names", ())):
                return niche_id
    return ""


def describe(tags: dict) -> str:
    """برچسب فارسی دسته از تگ‌ها (برای ستون category وقتی صنف مشخص نیست)."""
    key, value = _kind_of(tags)
    if value in CATEGORY_FA:
        return CATEGORY_FA[value]
    if key == "shop" and value:
        return CATEGORY_FA.get(value, "فروشگاه")
    if key == "office":
        return CATEGORY_FA.get(value, "دفتر")
    return CATEGORY_FA.get(value, value or "")


def spec_coverage() -> dict:
    """آمار پوشش نقشه (برای /api/health و آزمون‌ها)."""
    return {
        "niches": len(SPEC),
        "filters": sum(len(v.get("tags", {})) for v in SPEC.values()),
        "all_business": len(ALL_BUSINESS_TAGS),
    }
