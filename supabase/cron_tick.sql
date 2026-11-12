-- ─────────────────────────────────────────────────────────────────────────────
-- تپش خودکار صف از داخل خودِ Supabase (بدون مرورگر، بدون GitHub Actions)
--
-- چرا؟ روی Vercel پروسهٔ همیشه‌روشن نداریم. GitHub Actions هم گاهی کرونِ
-- زمان‌بندی‌شده را با تأخیر (یا اصلاً) اجرا می‌کند. اینجا خودِ پایگاه‌داده هر
-- ۲ دقیقه یک درخواست به `GET /api/worker/tick` می‌زند؛ پس صف حتی وقتی هیچ‌کس
-- مرورگرش را باز نکرده تمام می‌شود.
--
-- پیش‌نیاز: افزونه‌های `pg_cron` و `pg_net` (روی Supabase یک‌بار نصب می‌شوند).
-- اجرا: SQL Editor → این فایل را کامل Run کنید. دوباره‌اجرا بی‌خطر است
-- (زمان‌بندی قبلی جایگزین می‌شود).
--
-- ⚠ اگر دامنهٔ اپ عوض شد، فقط خط `APP_URL` را تغییر بدهید و دوباره اجرا کنید.
-- ─────────────────────────────────────────────────────────────────────────────

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

-- زمان‌بندی قدیمی (اگر بود) پاک می‌شود تا تکرارش با نام یکسان درست انجام شود
do $$
begin
  if exists (select 1 from cron.job where jobname = 'leadgen-queue-tick') then
    perform cron.unschedule('leadgen-queue-tick');
  end if;
  if exists (select 1 from cron.job where jobname = 'leadgen-net-cleanup') then
    perform cron.unschedule('leadgen-net-cleanup');
  end if;
end $$;

-- هر ۲ دقیقه یک تپش. «تایم‌اوت ۳ ثانیه» عمدی است: اتصال HTTP زودتر رها می‌شود
-- ولی تابع Vercel تا آخر کار می‌دود (اجرا در پایگاه‌داده ثبت می‌شود).
select cron.schedule(
  'leadgen-queue-tick',
  '*/2 * * * *',
  $job$
    select net.http_get(
      url := 'https://leadgen-studio-umber.vercel.app/api/worker/tick?budget=45&src=cron',
      timeout_milliseconds := 3000
    );
  $job$
);

-- پاک‌سازی روزانهٔ پاسخ‌های نگه‌داشته‌شده (جدول pg_net بی‌نهایت رشد نکند)
select cron.schedule(
  'leadgen-net-cleanup',
  '17 4 * * *',
  $job$
    delete from net._http_response where created < now() - interval '3 days';
  $job$
);

-- بررسی سلامت زمان‌بندی:
--   select jobid, jobname, schedule, active from cron.job;
--   select id, status_code, timed_out, left(content, 120) from net._http_response
--     order by id desc limit 5;
--   select * from cron.job_run_details order by start_time desc limit 10;
