-- ============================================================================
--  LeadGen — اسکیمای Supabase/Postgres (لایهٔ دادهٔ ابری نسخهٔ عملیاتی)
-- ----------------------------------------------------------------------------
--  این فایل را یک‌بار روی پروژهٔ Supabase اجرا کنید:
--    • داشبورد Supabase → SQL Editor → چسباندن کامل فایل → Run
--    • یا:  psql "$SUPABASE_DB_URL" -f supabase/schema.sql
--    • یا از راه API:  POST /rest/v1/rpc/... (بعد از اجرا در دسترس است)
--
--  طراحی:
--    • جدول‌ها آینهٔ همان ساختار SQLite برنامه‌اند (runs/leads) تا هیچ منطقی
--      دو بار نوشته نشود؛ به‌علاوهٔ `jobs` (صف اجراها برای ورکر) و `deliveries`
--      (گزارش تحویل ایمیل‌ها).
--    • جست‌وجوی قوی و facets داخل تابع SQL `search_leads` پیاده شده تا یک
--      round-trip از Vercel/Codespace کافی باشد (دقیقاً همان معنای نسخهٔ SQLite:
--      AND بین توکن‌ها، OR بین ۱۶ فیلد، شعاع دایره‌ای هاورسین، مرتب‌سازی سفید).
--    • RLS روشن و بدون policy است: یعنی فقط کلید service_role (که سمت سرور
--      نگه داشته می‌شود) دادهٔ لیدها را می‌بیند و کلید anon/عمومی هیچ دسترسی
--      ندارد. دادهٔ تماس مردم شخصی است — آن را عمومی نکنید.
-- ============================================================================

create extension if not exists pgcrypto;
do $$ begin
  create extension if not exists pg_trgm;      -- برای ایندکس جست‌وجوی متنی (اختیاری)
exception when others then null; end $$;

-- ── اجراها ──────────────────────────────────────────────────────────────────
create table if not exists public.runs (
    id           text primary key,
    created_at   double precision not null default extract(epoch from now()),
    niche_id     text,
    niche_fa     text,
    city         text,
    city_key     text,
    country      text,
    lat          text,
    lon          text,
    keywords     jsonb not null default '[]'::jsonb,
    depth        integer,
    radius       integer,
    grid         integer,
    lang         text,
    profile      text,
    socials      boolean default false,
    emails       boolean default false,
    engine       text,
    live         boolean,
    status       text default 'pending',
    total        integer default 0,
    hot          integer default 0,
    warm         integer default 0,
    cold         integer default 0,
    avg_score    double precision default 0,
    job_ids      jsonb not null default '[]'::jsonb,
    analytics    jsonb,
    error        text,
    progress     jsonb not null default '{}'::jsonb,
    province     text,
    district     text,
    street       text,
    area_label   text,
    area_level   text,
    updated_at   timestamptz not null default now()
);
create index if not exists runs_created_idx on public.runs (created_at desc);
create index if not exists runs_status_idx  on public.runs (status);

-- ── لیدها ───────────────────────────────────────────────────────────────────
create table if not exists public.leads (
    id            text primary key,
    run_id        text not null references public.runs (id) on delete cascade,
    place_id      text,
    title         text,
    category      text,
    city          text,
    address       text,
    phone         text,
    emails        text,
    email_primary text,
    website       text,
    domain        text,
    maps_link     text,
    plus_code     text,
    location      text,
    niche_id      text,
    niche_fa      text,
    instagram     text,
    facebook      text,
    linkedin      text,
    latitude      text,
    longitude     text,
    rating        double precision,
    reviews       integer,
    price_range   text,
    completeness  integer,
    has_website   integer default 0,
    has_email     integer default 0,
    has_phone     integer default 0,
    has_social    integer default 0,
    score         integer default 0,
    segment       text,
    gaps          jsonb not null default '[]'::jsonb,
    tags          jsonb not null default '[]'::jsonb,
    channel       text,
    pitch         text,
    score_factors jsonb not null default '[]'::jsonb,
    signals       jsonb not null default '{}'::jsonb,
    input_id      text,
    place_status  text,
    status        text not null default 'new',
    note          text not null default '',
    created_at    double precision default extract(epoch from now())
);
create index if not exists leads_run_idx      on public.leads (run_id);
create index if not exists leads_segment_idx  on public.leads (segment);
create index if not exists leads_status_idx   on public.leads (status);
create index if not exists leads_niche_idx    on public.leads (niche_fa);
create index if not exists leads_score_idx    on public.leads (score desc);
create index if not exists leads_title_idx    on public.leads (title);

-- ── صف اجراها (ورکر در Codespace/Actions کارها را برمی‌دارد) ────────────────
create table if not exists public.jobs (
    id           text primary key,
    run_id       text,
    status       text not null default 'pending',   -- pending | running | done | failed
    priority     integer not null default 0,
    attempts     integer not null default 0,
    max_attempts integer not null default 3,
    worker       text,
    request      jsonb not null default '{}'::jsonb,
    last_error   text,
    created_at   double precision not null default extract(epoch from now()),
    started_at   double precision,
    finished_at  double precision,
    updated_at   timestamptz not null default now()
);
create index if not exists jobs_queue_idx on public.jobs (status, priority desc, created_at);

-- ── گزارش تحویل (ایمیل/خروجی) ───────────────────────────────────────────────
create table if not exists public.deliveries (
    id         text primary key,
    kind       text not null default 'digest',      -- digest | draft | export | webhook
    channel    text not null default 'email',
    target     text,
    subject    text,
    status     text not null default 'sent',        -- sent | failed | skipped
    detail     jsonb not null default '{}'::jsonb,
    created_at double precision not null default extract(epoch from now())
);
create index if not exists deliveries_kind_idx on public.deliveries (kind, created_at desc);

-- ── امنیت: فقط service_role ─────────────────────────────────────────────────
alter table public.runs       enable row level security;
alter table public.leads      enable row level security;
alter table public.jobs       enable row level security;
alter table public.deliveries enable row level security;
-- عمدتا هیچ policy ساخته نمی‌شود: کلید service_role از RLS عبور می‌کند و
-- کلید anon/عمومی هیچ ردیفی نمی‌بیند. اگر خواستید کاربرِ لاگین‌کرده (Supabase Auth)
-- فقط لیدهای خودش را ببیند، policy مخصوص آن را جدا اضافه کنید.

-- ── توابع کمکی ──────────────────────────────────────────────────────────────
create or replace function public.num(value text)
returns double precision language sql immutable as $$
    select case when value ~ '^-?[0-9]+(\.[0-9]+)?$' then value::double precision end
$$;

create or replace function public.haversine_m(lat1 double precision, lon1 double precision,
                                              lat2 double precision, lon2 double precision)
returns double precision language sql immutable as $$
    select 2 * 6371000.0 * asin(sqrt(
        power(sin(radians(lat2 - lat1) / 2.0), 2)
        + cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2.0), 2)))
$$;

-- همان ۱۶ فیلدی که نسخهٔ SQLite برای جست‌وجو استفاده می‌کند
create or replace function public.lead_search_text(l public.leads)
returns text language sql immutable as $$
    select lower(concat_ws(' ',
        coalesce(l.title, ''), coalesce(l.category, ''), coalesce(l.niche_fa, ''),
        coalesce(l.address, ''), coalesce(l.city, ''), coalesce(l.phone, ''),
        coalesce(l.emails, ''), coalesce(l.email_primary, ''), coalesce(l.website, ''),
        coalesce(l.domain, ''), coalesce(l.tags::text, ''), coalesce(l.pitch, ''),
        coalesce(l.gaps::text, ''), coalesce(l.plus_code, ''), coalesce(l.input_id, ''),
        coalesce(l.note, '')))
$$;

do $$ begin
    create index if not exists leads_search_trgm_idx
        on public.leads using gin (public.lead_search_text(public.leads) gin_trgm_ops);
exception when others then null; end $$;

-- ── جست‌وجوی قوی (آینهٔ storage.search_leads) ───────────────────────────────
create or replace function public.search_leads(params jsonb default '{}'::jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
    v_q          text;
    v_tokens     text[] := array[]::text[];
    v_limit      integer := 50;
    v_offset     integer := 0;
    v_sort       text;
    v_dir        text;
    v_lat        double precision;
    v_lon        double precision;
    v_radius     double precision := 0;
    v_niche_ids  text[];
    v_facets     jsonb;
    v_total      integer;
begin
    v_q      := nullif(btrim(coalesce(params->>'q', '')), '');
    v_limit  := least(greatest(coalesce((params->>'limit')::integer, 50), 1), 1000);
    v_offset := greatest(coalesce((params->>'offset')::integer, 0), 0);
    v_sort   := coalesce(params->>'sort', 'score');
    if v_sort not in ('score', 'reviews', 'rating', 'title', 'created_at', 'completeness') then
        v_sort := 'score';
    end if;
    v_dir    := case when lower(coalesce(params->>'order', 'desc')) = 'asc' then 'asc' else 'desc' end;
    v_lat    := public.num(params->>'lat');
    v_lon    := public.num(params->>'lon');
    v_radius := coalesce(public.num(params->>'radius_m'), 0);

    if v_q is not null then
        select coalesce(array_agg(t), array[]::text[]) into v_tokens
        from (
            select btrim(piece) as t
            from unnest(regexp_split_to_array(v_q, '[\s،,;]+')) as piece
            where length(btrim(piece)) >= 2
            limit 8
        ) as tokens;
    end if;

    if params ? 'niche_ids' and jsonb_typeof(params->'niche_ids') = 'array' then
        select coalesce(array_agg(value #>> '{}'), array[]::text[]) into v_niche_ids
        from jsonb_array_elements(params->'niche_ids');
    end if;

    -- نتیجهٔ نهایی: total + صفحهٔ جاری + facets (همه از یک مجموعهٔ فیلترشده)
    return (
        with base as (
            select l.*,
                   r.city                                            as run_city,
                   r.niche_fa                                        as run_niche,
                   coalesce(nullif(btrim(r.area_label), ''), r.city) as run_area,
                   r.created_at                                      as run_created,
                   case when v_lat is not null and v_lon is not null and v_radius > 0
                             and public.num(l.latitude) is not null
                             and public.num(l.longitude) is not null
                        then public.haversine_m(v_lat, v_lon,
                                                public.num(l.latitude), public.num(l.longitude))
                   end                                               as distance_m
            from public.leads l
            left join public.runs r on r.id = l.run_id
            where true
              and (nullif(btrim(coalesce(params->>'run_id', '')), '') is null
                   or l.run_id = btrim(params->>'run_id'))
              and (nullif(btrim(coalesce(params->>'segment', '')), '') is null
                   or l.segment = btrim(params->>'segment'))
              and (nullif(btrim(coalesce(params->>'status', '')), '') is null
                   or l.status = btrim(params->>'status'))
              and (nullif(btrim(coalesce(params->>'channel', '')), '') is null
                   or l.channel = btrim(params->>'channel'))
              and (nullif(btrim(coalesce(params->>'gap', '')), '') is null
                   or jsonb_exists(l.gaps, btrim(params->>'gap')))
              and (nullif(btrim(coalesce(params->>'niche_id', '')), '') is null
                   or l.niche_id = btrim(params->>'niche_id')
                   or l.category ilike '%' || btrim(params->>'niche_id') || '%'
                   or l.title    ilike '%' || btrim(params->>'niche_id') || '%')
              and (nullif(btrim(coalesce(params->>'niche_fa', '')), '') is null
                   or l.niche_fa = btrim(params->>'niche_fa')
                   or l.category = btrim(params->>'niche_fa')
                   or l.niche_fa ilike '%' || btrim(params->>'niche_fa') || '%')
              and (v_niche_ids is null or cardinality(v_niche_ids) = 0
                   or l.niche_id = any (v_niche_ids))
              and (nullif(btrim(coalesce(params->>'has_phone', '')), '') is null
                   or coalesce(l.has_phone, 0) =
                      (case when (params->>'has_phone')::boolean then 1 else 0 end))
              and (nullif(btrim(coalesce(params->>'has_email', '')), '') is null
                   or coalesce(l.has_email, 0) =
                      (case when (params->>'has_email')::boolean then 1 else 0 end))
              and (nullif(btrim(coalesce(params->>'has_website', '')), '') is null
                   or coalesce(l.has_website, 0) =
                      (case when (params->>'has_website')::boolean then 1 else 0 end))
              and (nullif(btrim(coalesce(params->>'has_social', '')), '') is null
                   or coalesce(l.has_social, 0) =
                      (case when (params->>'has_social')::boolean then 1 else 0 end))
              and (nullif(btrim(coalesce(params->>'has_instagram', '')), '') is null
                   or (case when (params->>'has_instagram')::boolean
                            then coalesce(btrim(l.instagram), '') <> ''
                            else coalesce(btrim(l.instagram), '') = '' end))
              and (public.num(params->>'min_rating') is null
                   or coalesce(l.rating, 0) >= public.num(params->>'min_rating'))
              and (public.num(params->>'min_reviews') is null
                   or coalesce(l.reviews, 0) >= public.num(params->>'min_reviews'))
              and (public.num(params->>'min_score') is null
                   or coalesce(l.score, 0) >= public.num(params->>'min_score'))
              -- پیش‌فیلتر مربعی (سریع) برای شعاع؛ دایرهٔ دقیق در CTE بعدی
              and (v_radius <= 0 or v_lat is null or v_lon is null
                   or (public.num(l.latitude) is not null and public.num(l.longitude) is not null
                       and public.num(l.latitude) between
                             v_lat - (v_radius / 111000.0) and v_lat + (v_radius / 111000.0)
                       and public.num(l.longitude) between
                             v_lon - (v_radius / (111000.0 * greatest(0.05, abs(cos(radians(v_lat))))))
                         and v_lon + (v_radius / (111000.0 * greatest(0.05, abs(cos(radians(v_lat))))))))
              -- هر توکن باید در یکی از ۱۶ فیلد باشد (AND بین توکن‌ها، OR بین فیلدها)
              and (cardinality(v_tokens) = 0
                   or (select bool_and(public.lead_search_text(l) like '%' ||
                                       replace(replace(lower(t), '%', '\%'), '_', '\_') || '%')
                       from unnest(v_tokens) as t))
        ),
        hits as (
            select b.* from base b
            where v_radius <= 0 or v_lat is null or v_lon is null
                  or (b.distance_m is not null and b.distance_m <= v_radius)
        )
        select jsonb_build_object(
            'total', (select count(*)::integer from hits),
            'leads', coalesce((
                select jsonb_agg(to_jsonb(page))
                from (
                    select h.* from hits h
                    order by
                        case when v_dir = 'desc' and v_sort <> 'title' then
                            case v_sort
                                when 'score'        then coalesce(h.score, 0)::double precision
                                when 'reviews'      then coalesce(h.reviews, 0)::double precision
                                when 'rating'       then coalesce(h.rating, 0)::double precision
                                when 'completeness' then coalesce(h.completeness, 0)::double precision
                                when 'created_at'   then coalesce(h.created_at, 0)::double precision
                                else coalesce(h.score, 0)::double precision
                            end
                        end desc nulls last,
                        case when v_dir = 'asc' and v_sort <> 'title' then
                            case v_sort
                                when 'score'        then coalesce(h.score, 0)::double precision
                                when 'reviews'      then coalesce(h.reviews, 0)::double precision
                                when 'rating'       then coalesce(h.rating, 0)::double precision
                                when 'completeness' then coalesce(h.completeness, 0)::double precision
                                when 'created_at'   then coalesce(h.created_at, 0)::double precision
                                else coalesce(h.score, 0)::double precision
                            end
                        end asc nulls last,
                        case when v_sort = 'title' and v_dir = 'desc' then h.title end desc nulls last,
                        case when v_sort = 'title' and v_dir = 'asc'  then h.title end asc nulls last,
                        h.title asc
                    limit v_limit offset v_offset
                ) page
            ), '[]'::jsonb),
            'facets', (select jsonb_build_object(
                'segment', coalesce((select jsonb_object_agg(k, c) from (
                    select coalesce(nullif(btrim(h.segment), ''), 'na') as k, count(*)::integer as c
                    from hits h group by 1) s), '{}'::jsonb),
                'status', coalesce((select jsonb_object_agg(k, c) from (
                    select coalesce(nullif(btrim(h.status), ''), 'new') as k, count(*)::integer as c
                    from hits h group by 1) s), '{}'::jsonb),
                'niche', coalesce((select jsonb_object_agg(k, c) from (
                    select k, c from (
                        select coalesce(nullif(btrim(h.niche_fa), ''),
                                        nullif(btrim(h.niche_id), ''), '—') as k,
                               count(*)::integer as c
                        from hits h group by 1 order by c desc, k asc limit 24) top
                )), '{}'::jsonb),
                'contact', jsonb_build_object(
                    'has_phone',   (select count(*)::integer from hits h where coalesce(h.has_phone, 0) = 1),
                    'has_email',   (select count(*)::integer from hits h where coalesce(h.has_email, 0) = 1),
                    'has_website', (select count(*)::integer from hits h where coalesce(h.has_website, 0) = 1),
                    'has_social',  (select count(*)::integer from hits h where coalesce(h.has_social, 0) = 1)),
                'runs', coalesce((select jsonb_object_agg(k, c) from (
                    select coalesce(nullif(btrim(h.run_id), ''), '—') as k, count(*)::integer as c
                    from hits h group by 1) s), '{}'::jsonb),
                'total',   (select count(*)::integer from hits),
                'counted', (select count(*)::integer from hits),
                'partial', false,
                'score', jsonb_build_object(
                    'min', coalesce((select min(h.score) from hits h), 0)::integer,
                    'max', coalesce((select max(h.score) from hits h), 0)::integer,
                    'avg', coalesce((select round(avg(h.score)::numeric, 1)
                                     from hits h where h.score is not null), 0)::numeric))
            )
        )
    );
end
$$;

-- ── صف اجراها ───────────────────────────────────────────────────────────────
-- ورکر (داخل Codespace یا GitHub Actions) با claim_job یک کار را اتمی برمی‌دارد؛
-- FOR UPDATE SKIP LOCKED یعنی دو ورکر هرگز یک کار را دوباره اجرا نمی‌کنند.
create or replace function public.enqueue_job(p_run_id text, p_request jsonb,
                                              p_priority integer default 0)
returns text
language plpgsql
as $$
declare
    v_id text := coalesce(nullif(btrim(p_run_id), ''),
                          'job-' || substr(gen_random_uuid()::text, 1, 12));
begin
    insert into public.jobs (id, run_id, request, priority, status)
    values (v_id, p_run_id, coalesce(p_request, '{}'::jsonb), coalesce(p_priority, 0), 'pending')
    on conflict (id) do update
        set request    = excluded.request,
            priority   = excluded.priority,
            status     = 'pending',
            attempts   = 0,
            last_error = null,
            updated_at = now();
    return v_id;
end
$$;

create or replace function public.claim_job(p_worker text default 'worker',
                                            p_max_attempts integer default 3)
returns jsonb
language plpgsql
as $$
declare
    v public.jobs;
begin
    select * into v
    from public.jobs
    where status = 'pending' and attempts < coalesce(p_max_attempts, 3)
    order by priority desc, created_at asc
    for update skip locked
    limit 1;

    if not found then
        return null;
    end if;

    update public.jobs
       set status = 'running', worker = p_worker, attempts = attempts + 1,
           started_at = extract(epoch from now()), updated_at = now()
     where id = v.id;

    v.status := 'running';
    v.worker := p_worker;
    v.attempts := v.attempts + 1;
    v.started_at := extract(epoch from now());
    return to_jsonb(v);
end
$$;

-- پیشرفت اجرا را هم‌زمان روی job و run می‌نویسد (رابط از runs.progress می‌خواند)
create or replace function public.heartbeat_job(p_id text, p_progress jsonb default null,
                                                p_status text default null)
returns boolean
language plpgsql
as $$
declare
    v_run text;
    v_found boolean := false;
begin
    update public.jobs
       set updated_at = now(),
           -- فقط وضعیت‌های معتبر کار؛ payload پیشرفت «working»/«enrich» می‌فرستد
           -- و پیش‌تر همین باعث می‌شد کار از شمارش و بازپس‌گیری صف بیرون بیفتد.
           status = case when p_status in ('pending', 'running', 'done', 'failed')
                         then p_status else status end
     where id = p_id;
    get diagnostics v_found = row_count;

    select run_id into v_run from public.jobs where id = p_id;
    if v_run is not null and (p_progress is not null or p_status is not null) then
        update public.runs
           set progress = coalesce(progress, '{}'::jsonb) || coalesce(p_progress, '{}'::jsonb),
               status   = coalesce(p_progress->>'status', p_status, status),
               error    = coalesce(p_progress->>'error', error),
               updated_at = now()
         where id = v_run;
    end if;
    return v_found;
end
$$;

create or replace function public.finish_job(p_id text, p_status text default 'done',
                                             p_error text default null)
returns jsonb
language plpgsql
as $$
declare
    v public.jobs;
    v_run text;
    v_final text;
begin
    select * into v from public.jobs where id = p_id;
    if not found then
        return null;
    end if;

    if p_status = 'failed' and v.attempts < v.max_attempts then
        -- تلاش مجدد: کار به صف برمی‌گردد (ورکر بعدی دوباره برمی‌دارد)
        update public.jobs
           set status = 'pending', last_error = p_error, worker = null, updated_at = now()
         where id = p_id;
        v_final := 'pending';
    else
        update public.jobs
           set status = p_status, last_error = p_error,
               finished_at = extract(epoch from now()), updated_at = now()
         where id = p_id;
        v_final := p_status;
    end if;

    v_run := v.run_id;
    if v_run is not null then
        update public.runs
           set status = case when v_final = 'pending' then 'queued' else v_final end,
               error  = p_error,
               progress = coalesce(progress, '{}'::jsonb) || jsonb_build_object(
                   'status', case when v_final = 'pending' then 'queued' else v_final end,
                   'stage', v_final,
                   'message', case
                       when v_final = 'done'    then 'پایان کار'
                       when v_final = 'pending' then 'تلاش ' || v.attempts ||
                            ' ناموفق بود — دوباره در صف' ||
                            coalesce(': ' || nullif(btrim(p_error), ''), '')
                       else 'اجرا ناموفق شد (' || v.attempts || ' از ' || v.max_attempts ||
                            ' تلاش)' || coalesce(': ' || nullif(btrim(p_error), ''), '')
                   end,
                   'ts', extract(epoch from now())),
               updated_at = now()
         where id = v_run;
    end if;

    v.status := v_final;
    v.last_error := p_error;
    return to_jsonb(v);
end
$$;

-- ورکرِ مرده (Codespace خاموش شد) → کارها بعد از این مدت به صف برمی‌گردند
create or replace function public.requeue_stale_jobs(p_seconds double precision default 1800)
returns integer
language plpgsql
as $$
declare
    v_count integer := 0;
begin
    with stale as (
        select id, attempts, max_attempts
        from public.jobs
        where status in ('running', 'working')
          and coalesce(started_at, extract(epoch from now()))
              < extract(epoch from now()) - greatest(coalesce(p_seconds, 1800), 30)
    ), fixed as (
        update public.jobs j
           set status = case when s.attempts >= s.max_attempts then 'failed' else 'pending' end,
               worker = null,
               last_error = coalesce(j.last_error, 'ورکر پاسخ نداد — دوباره به صف برگشت'),
               updated_at = now()
          from stale s
         where j.id = s.id
        returning j.id
    )
    select count(*) into v_count from fixed;

    update public.runs r
       set status = 'queued', updated_at = now()
      from public.jobs j
     where j.run_id = r.id and j.status = 'pending' and r.status = 'running';

    return coalesce(v_count, 0);
end
$$;

create or replace function public.queue_stats()
returns jsonb
language sql
stable
as $$
    select jsonb_build_object(
        'pending', (select count(*)::integer from public.jobs where status = 'pending'),
        -- 'working' وضعیت قدیمیِ پیشرفت بود که در ستون کار نوشته می‌شد؛
        -- تا پاک‌سازی کامل، همان «در حال اجرا» شمرده می‌شود.
        'running', (select count(*)::integer from public.jobs where status in ('running', 'working')),
        'done',    (select count(*)::integer from public.jobs where status = 'done'),
        'failed',  (select count(*)::integer from public.jobs where status = 'failed'),
        'oldest_wait_s', coalesce((select round(max(extract(epoch from now()) - created_at)::numeric, 0)::integer
                                   from public.jobs where status = 'pending'), 0),
        'workers', coalesce((select jsonb_agg(distinct worker) from public.jobs
                             where status in ('running', 'working') and worker is not null),
                            '[]'::jsonb));
$$;

-- ── نوشتن/به‌روزرسانی اجرا ──────────────────────────────────────────────────
-- `jsonb_populate_record` نوع هر ستون را خودش درست می‌کند و کلیدهای داده‌نشده را
-- دست‌نخورده می‌گذارد (merge) — پس یک payload ناقص، دادهٔ قبلی را پاک نمی‌کند.
create or replace function public.save_run(payload jsonb)
returns text
language plpgsql
as $$
declare
    v_id text := payload->>'id';
begin
    if v_id is null then
        raise exception 'save_run: کلید «id» در payload لازم است';
    end if;
    -- jsonb_populate_record برای کلیدهای غایب NULL می‌سازد و NULL صریح، پیش‌فرض ستون را
    -- دور می‌زند؛ پس ستون‌های NOT NULL را اینجا پر می‌کنیم تا payload ناقص هم کار کند.
    insert into public.runs
    select * from jsonb_populate_record(null::public.runs,
        payload || jsonb_build_object(
            'created_at', coalesce((payload->>'created_at')::double precision,
                                   extract(epoch from now())),
            'keywords',   coalesce(payload->'keywords', '[]'::jsonb),
            'job_ids',    coalesce(payload->'job_ids', '[]'::jsonb),
            'progress',   coalesce(payload->'progress', '{}'::jsonb),
            'status',     coalesce(payload->>'status', 'pending'),
            'updated_at', now()))
    on conflict (id) do nothing;
    perform public.patch_run(v_id, payload);
    return v_id;
end
$$;

create or replace function public.patch_run(p_id text, payload jsonb)
returns boolean
language plpgsql
as $$
declare
    v public.runs;
begin
    select * into v from public.runs where id = p_id;
    if not found then
        return false;
    end if;
    v := jsonb_populate_record(v, payload);
    update public.runs t
       set niche_id = v.niche_id, niche_fa = v.niche_fa, city = v.city, city_key = v.city_key,
           country = v.country, lat = v.lat, lon = v.lon, keywords = v.keywords,
           depth = v.depth, radius = v.radius, grid = v.grid, lang = v.lang, profile = v.profile,
           socials = v.socials, emails = v.emails, engine = v.engine, live = v.live,
           status = v.status, total = v.total, hot = v.hot, warm = v.warm, cold = v.cold,
           avg_score = v.avg_score, job_ids = v.job_ids, analytics = v.analytics,
           error = v.error, progress = v.progress, province = v.province, district = v.district,
           street = v.street, area_label = v.area_label, area_level = v.area_level,
           updated_at = now()
     where t.id = p_id;
    return true;
end
$$;

create or replace function public.delete_run(p_run_id text)
returns boolean
language plpgsql
as $$
declare
    v_count integer := 0;
begin
    delete from public.jobs where run_id = p_run_id;
    delete from public.leads where run_id = p_run_id;
    delete from public.runs where id = p_run_id;
    get diagnostics v_count = row_count;
    return v_count > 0;
end
$$;

-- ── آمار کلی داشبورد ────────────────────────────────────────────────────────
create or replace function public.dashboard_stats()
returns jsonb
language sql
stable
as $$
    select jsonb_build_object(
        'runs',       (select count(*)::integer from public.runs),
        'leads',      (select count(*)::integer from public.leads),
        'contacted',  (select count(*)::integer from public.leads where status <> 'new'),
        'won',        (select count(*)::integer from public.leads where status = 'won'),
        'with_email', (select count(*)::integer from public.leads where coalesce(has_email, 0) = 1),
        'with_phone', (select count(*)::integer from public.leads where coalesce(has_phone, 0) = 1),
        'with_site',  (select count(*)::integer from public.leads where coalesce(has_website, 0) = 1),
        'hot',        (select count(*)::integer from public.leads where segment = 'hot'),
        'queue',      public.queue_stats(),
        'latest_runs', coalesce((select jsonb_agg(row_to_json(x)) from (
                            select id, niche_fa, city, area_label, status, total, hot, created_at
                            from public.runs order by created_at desc limit 5) x), '[]'::jsonb));
$$;

-- ── ثبت تحویل (ایمیل/خروجی) ────────────────────────────────────────────────
create or replace function public.log_delivery(p_kind text, p_channel text, p_target text,
                                               p_subject text, p_status text,
                                               p_detail jsonb default '{}'::jsonb)
returns text
language plpgsql
as $$
declare
    v_id text := 'dlv-' || substr(gen_random_uuid()::text, 1, 12);
begin
    insert into public.deliveries (id, kind, channel, target, subject, status, detail)
    values (v_id, coalesce(p_kind, 'digest'), coalesce(p_channel, 'email'), p_target,
            p_subject, coalesce(p_status, 'sent'), coalesce(p_detail, '{}'::jsonb));
    return v_id;
end
$$;

-- آخرین digest موفق — تا ایمیل تکراری در یک روز نرود
create or replace function public.last_delivery(p_kind text default 'digest')
returns jsonb
language sql
stable
as $$
    select to_jsonb(d) from public.deliveries d
    where d.kind = coalesce(p_kind, 'digest') and d.status = 'sent'
    order by d.created_at desc limit 1
$$;

-- ── دسترسی‌ها ───────────────────────────────────────────────────────────────
-- service_role به‌صورت پیش‌فرض همهٔ این‌ها را دارد؛ اگر نقش دیگری (مثلاً ورکر
-- با کلید محدود) می‌خواهید، این بخش را باز کنید:
-- grant usage on schema public to authenticated;
-- grant execute on function public.search_leads(jsonb), public.dashboard_stats() to authenticated;
