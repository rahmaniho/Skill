-- ============================================================================
--  LeadGen — «دیدبان شهر» (city watchlist)
-- ----------------------------------------------------------------------------
--  یک دیدبان = یک منطقه (شهر یا استان) + یک صنف + بازهٔ تکرار.
--  هر بار که دیدبان «می‌رسد»، سرور یک اجرای واقعی در صف می‌گذارد و در پایان
--  فقط کسب‌وکارهایی که در هیچ اجرای قبلی نبوده‌اند ایمیل می‌شوند.
--
--  اجرا:
--    • Supabase → SQL Editor → این فایل را کامل Run کنید (دوباره‌اجرا بی‌خطر است)
--    • یا:  psql "$SUPABASE_DB_URL" -f supabase/watchlist.sql
--
--  نکته: جدول آینهٔ همان ساختار SQLite است (leadgen/storage.py) تا منطق
--  برنامه دو بار نوشته نشود. امنیت مثل بقیهٔ جدول‌ها: RLS روشن و بدون policy،
--  یعنی فقط service_role (سمت سرور) دسترسی دارد.
-- ============================================================================

create table if not exists public.watchlist (
    id            text primary key,
    created_at    double precision not null default extract(epoch from now()),
    province      text not null default '',
    city          text not null default '',
    city_key      text not null default '',
    niche_query   text not null default '',
    all_niches    boolean not null default false,
    interval_days integer not null default 7 check (interval_days between 1 and 365),
    active        boolean not null default true,
    next_run_at   double precision not null default extract(epoch from now()),
    last_run_at   double precision,
    last_run_id   text not null default '',
    last_new      integer not null default 0,
    note          text not null default ''
);

-- «کدام دیدبان الان رسیده است؟» — پرکاربردترین کوئری (هر تیک/کرون).
create index if not exists watchlist_due_idx
    on public.watchlist (active, next_run_at);

create index if not exists watchlist_city_idx
    on public.watchlist (city_key);

alter table public.watchlist enable row level security;

-- هیچ policy‌ای عمداً ساخته نمی‌شود: anon/authenticated دسترسی ندارند،
-- service_role همه‌کاره است. (دادهٔ منطقه/صنف حساس نیست، ولی یکدست می‌مانیم.)

-- ── نمای کمکی: دیدبان‌های رسیده (برای بررسی دستی در SQL Editor) ──────────────
create or replace view public.watchlist_due as
select id, city, province, niche_query, all_niches, interval_days,
       to_timestamp(next_run_at) as next_run_at,
       to_timestamp(last_run_at) as last_run_at,
       last_new
  from public.watchlist
 where active
   and next_run_at <= extract(epoch from now())
 order by next_run_at asc;

-- بررسی سلامت:
--   select count(*) filter (where active) as active, count(*) filter (where active and next_run_at <= extract(epoch from now())) as due
--     from public.watchlist;
--   select * from public.watchlist_due;
