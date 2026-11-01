-- ОПАСНО. ТОЛЬКО ДЛЯ РАЗРАБОТКИ.
-- Удаляет ВСЕ таблицы True Alarm вместе с данными, чтобы затем применить
-- миграции с нуля. Запускайте только если в базе лежат тестовые данные.
-- Файлы в Supabase Storage этот скрипт не трогает.

drop table if exists public.reports cascade;
drop table if exists public.problem_senders cascade;
drop table if exists public.storage_cleanup cascade;
drop table if exists public.problems cascade;
drop table if exists public.schema_migrations cascade;
drop function if exists public.problems_protect_history() cascade;
