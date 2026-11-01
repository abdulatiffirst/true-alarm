# True Alarm — project review notes

## Database migration status

The existing Supabase database already contains the core application tables and records the migration `001_initial_schema.sql` in `public.schema_migrations`.

Running `python -m scripts.apply_migrations` returned `Новых миграций нет.`, which confirms that the migration runner recognizes the initial migration as applied. Do not delete or recreate existing tables to resolve the earlier `DuplicateTable` error.

## Important safety notes

- Do not run `scripts/reset_dev_database.sql` against the database containing real reports.
- Back up the Supabase database before applying any future schema migration.
- Keep the Supabase Storage bucket and existing photo objects intact.
- Keep `.env` local. This package intentionally does not include secrets; configure it from `.env.example`.

## Checks performed during this review

- Python source files compile successfully with `compileall`.
- `app/static/app.js` passes `node --check` in the review environment.
- The map marker code uses `problem.latitude` and `problem.longitude`.
- Full pytest execution was not possible in the review environment because project dependencies could not be installed (network access unavailable). Run tests locally after installing `requirements.txt` and `requirements-dev.txt`.

## Local commands

Install dependencies:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Check migration status:

```powershell
python -m scripts.apply_migrations
```

Run tests:

```powershell
python -m pytest -q
```

Start the web application:

```powershell
python -m uvicorn app.main:app --reload
```
