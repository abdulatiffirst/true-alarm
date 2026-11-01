-- True Alarm: начальная схема.
-- Применяется командой:  python -m scripts.apply_migrations
-- Все даты хранятся как timestamptz (момент времени, внутри базы это UTC).

create schema if not exists extensions;
create extension if not exists postgis with schema extensions;

-- ---------------------------------------------------------------------------
-- Проблема: одна конкретная ситуация, одна метка на карте.
-- ---------------------------------------------------------------------------
create table public.problems (
    id                   uuid primary key default gen_random_uuid(),
    category             text not null
                         constraint problems_category_check check (category in ('waste', 'infrastructure')),
    location             extensions.geography(Point, 4326) not null,
    -- 'open' показывается на карте; 'hidden' скрыта администратором (механизм добавим позже).
    status               text not null default 'open'
                         constraint problems_status_check check (status in ('open', 'hidden')),
    first_report_at      timestamptz not null default now(),
    last_report_at       timestamptz not null default now(),
    report_count         integer not null default 1,
    independent_senders  integer not null default 1,
    hidden_at            timestamptz,
    constraint problems_counts_check check (independent_senders >= 1 and report_count >= independent_senders),
    constraint problems_dates_check  check (last_report_at >= first_report_at),
    constraint problems_hidden_check check ((status = 'hidden') = (hidden_at is not null))
);

create index problems_location_gix on public.problems using gist (location);
create index problems_status_idx   on public.problems (status);

-- ---------------------------------------------------------------------------
-- Независимые отправители проблемы (по одному ряду на псевдоним).
-- ---------------------------------------------------------------------------
create table public.problem_senders (
    problem_id       uuid not null references public.problems (id) on delete cascade,
    sender_hash      text not null
                     constraint problem_senders_hash_check check (sender_hash ~ '^[0-9a-f]{64}$'),
    first_report_at  timestamptz not null default clock_timestamp(),
    primary key (problem_id, sender_hash)
);

-- ---------------------------------------------------------------------------
-- Заявка: одно сообщение с одной фотографией.
-- Строка остаётся навсегда, даже когда файл фотографии удалён.
-- ---------------------------------------------------------------------------
create table public.reports (
    id            uuid primary key default gen_random_uuid(),
    -- Порядок принятия внутри проблемы; разрешает совпадение времени.
    seq           bigint generated always as identity,
    problem_id    uuid not null references public.problems (id) on delete cascade,
    sender_hash   text not null
                  constraint reports_hash_check check (sender_hash ~ '^[0-9a-f]{64}$'),
    -- SHA-256 исходного принятого файла. Остаётся после удаления файла.
    photo_sha256  text not null
                  constraint reports_photo_sha256_check check (photo_sha256 ~ '^[0-9a-f]{64}$'),
    photo_path    text,
    thumb_path    text,
    has_file      boolean generated always as (photo_path is not null) stored,
    created_at    timestamptz not null default clock_timestamp(),
    constraint reports_photo_sha256_key unique (photo_sha256),
    constraint reports_thumb_needs_photo check (thumb_path is null or photo_path is not null),
    constraint reports_sender_fk foreign key (problem_id, sender_hash)
        references public.problem_senders (problem_id, sender_hash) on delete cascade
);

create index reports_problem_latest_idx on public.reports (problem_id, created_at desc, seq desc);
create index reports_live_photos_idx    on public.reports (problem_id, created_at desc, seq desc)
    where photo_path is not null;
create index reports_sender_recent_idx  on public.reports (sender_hash, created_at desc);
create unique index reports_photo_path_key on public.reports (photo_path) where photo_path is not null;

-- ---------------------------------------------------------------------------
-- Очередь удаления файлов из нашего хранилища (с повторными попытками).
-- ---------------------------------------------------------------------------
create table public.storage_cleanup (
    id               bigint generated always as identity primary key,
    object_path      text not null unique,
    reason           text not null
                     constraint storage_cleanup_reason_check check (reason in ('over_limit', 'orphan', 'admin_erase')),
    attempts         integer not null default 0,
    next_attempt_at  timestamptz not null default now(),
    last_error       text,
    created_at       timestamptz not null default now()
);

create index storage_cleanup_due_idx on public.storage_cleanup (next_attempt_at);

-- ---------------------------------------------------------------------------
-- История проблемы только растёт: счётчики не уменьшаются, дата первого
-- сообщения не меняется. Удаление фотографий этих полей не затрагивает.
-- ---------------------------------------------------------------------------
create function public.problems_protect_history() returns trigger
language plpgsql as $$
begin
    if new.report_count < old.report_count
       or new.independent_senders < old.independent_senders
       or new.first_report_at <> old.first_report_at then
        raise exception 'report_count, independent_senders and first_report_at must not decrease or change';
    end if;
    return new;
end;
$$;

create trigger problems_protect_history
    before update on public.problems
    for each row execute function public.problems_protect_history();

-- ---------------------------------------------------------------------------
-- Доступ: все обращения идут через наш сервер. Публичные ключи Supabase
-- (anon / authenticated) не должны видеть эти таблицы.
-- ---------------------------------------------------------------------------
alter table public.problems        enable row level security;
alter table public.problem_senders enable row level security;
alter table public.reports         enable row level security;
alter table public.storage_cleanup enable row level security;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format(
                'revoke all on public.problems, public.problem_senders, public.reports, public.storage_cleanup from %I', r);
        end if;
    end loop;
end;
$$;
