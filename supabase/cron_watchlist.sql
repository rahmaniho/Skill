-- ============================================================================
--  LeadGen — تیک روزانهٔ «دیدبان شهر» از داخل خودِ Supabase
-- ----------------------------------------------------------------------------
--  چرا؟ روی Vercel پروسهٔ همیشه‌روشن نداریم. این کرون هر روز صبح یک درخواست
--  به `POST /api/watchlist/tick` می‌زند؛ سرور دیدبان‌های رسیده را اجرا می‌کند،
--  اسکراب کامل منطقه را انجام می‌دهد و فقط «تازه‌ها» را ایمیل می‌کند.
--
--  پیش‌نیاز: افزونه‌های `pg_cron` و `pg_net` (اگر فایل cron_tick.sql را قبلاً
--  اجرا کرده‌اید، از همان‌ها استفاده می‌شود — نیازی به نصب دوباره نیست).
--
--  اجرا: SQL Editor → این فایل را کامل Run کنید. دوباره‌اجرا بی‌خطر است
--  (زمان‌بندی قبلی با همان نام جایگزین می‌شود).
--
--  ⚠ اگر دامنهٔ اپ عوض شد، فقط خط `APP_URL` را تغییر بدهید و دوباره اجرا کنید.
-- ============================================================================

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

do $$
begin
  if exists (select 1 from cron.job where jobname = 'leadgen-watchlist-daily') then
    perform cron.unschedule('leadgen-watchlist-daily');
  end if;
end $$;

-- هر روز ساعت ۰۶:۱۰ UTC (≈ ۰۹:۴۰ تهران) — بعد از موج شبانهٔ ایمیل‌ها.
-- «تایم‌اوت ۳ ثانیه» عمدی است: اتصال HTTP زودتر رها می‌شود ولی تابع Vercel
-- تا آخر کار می‌دود و اجرا (و ایمیل تازه‌ها) در پایگاه‌داده ثبت می‌شود.
select cron.schedule(
  'leadgen-watchlist-daily',
  '10 6 * * *',
  $job$
    select net.http_post(
      url := 'https://leadgen-studio-umber.vercel.app/api/watchlist/tick?limit=5&src=cron',
      headers := '{"Content-Type": "application/json"}'::jsonb,
      body := '{}'::jsonb,
      timeout_milliseconds := 3000
    );
  $job$
);

-- بررسی سلامت زمان‌بندی:
--   select jobid, jobname, schedule, active from cron.job where jobname like 'leadgen-%';
--   select id, status_code, timed_out, left(content, 160) from net._http_response
--     order by id desc limit 5;
