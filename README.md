# Nordvet data engineering case

A local, rerunnable extract from the VetOne API. The pipeline fetches clinic
records and appointments for a date range, follows appointment pagination, and
upserts the complete records into SQLite.

## Requirements

- Python 3.9 or newer (the pipeline and tests were verified with Python 3.9.12
  and Python 3.10.5)
- VetOne API client ID and client secret from the case brief

Install dependencies from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

The working environment on the author's Windows machine uses Python 3.9.12 from
Anaconda because its Python 3.10 installation is under a Windows user path with
non-ASCII characters. That Anaconda environment also needs its `Library\bin`
directory available for native DLL loading; the local workaround is inside the
ignored `.venv` and is not part of this repository. A regular Python
installation should use the commands above without that machine-specific
workaround.

Put the credentials in your local `.env` file. Do not commit that file, access
tokens, or downloaded data. `.env` is ignored by Git, and the default SQLite
database is written under the ignored `data/` directory.

## Run

By default, the pipeline requests yesterday's appointments:

```powershell
python -m src.pipeline
```

Choose a specific appointment date range and/or database path:

```powershell
python -m src.pipeline --from-date 2026-10-04 --to-date 2026-10-04
python -m src.pipeline --from-date 2026-10-01 --to-date 2026-10-04 --database data/nordvet.sqlite3
```

For the initial load, run:

```powershell
python -m src.pipeline --initial-load
```

This loads from 2026-07-03 through the current local date, one calendar day per
API request. 2026-07-03 is the earliest appointment date observed in an initial
API probe, not an API guarantee that no earlier records exist. The initial load
fetches clinics once and saves each completed day before requesting the next;
the terminal progress bar shows which day is being loaded and advances after
each day is saved. If a later day fails, rerunning the command safely upserts
the already loaded data.

For other ranges, the pipeline splits the inclusive date range into
two-calendar-day API requests by default:

```powershell
python -m src.pipeline --from-date 2026-10-01 --to-date 2026-10-04
```

Override the ordinary window size with `--interval-days N`; use small windows
if the API starts rate-limiting requests.

The API token is requested when needed and sent as a bearer token. The client
retries a request once after an HTTP 401 by obtaining a fresh token, and retries
HTTP 429/5xx responses up to three times (honoring a numeric `Retry-After`
header, when provided). API errors are surfaced rather than silently ignored.
The appointment request includes `include_deleted=true` and follows
`next_cursor` until all pages are read.

## Daily scheduling (Windows example)

The pipeline is a single run, not a scheduler. This Windows example uses Task
Scheduler rather than cron; the cron-equivalent for a daily run at 07:00 is:

```cron
0 7 * * *
```

For a laptop demo, the optional PowerShell scripts register that daily
schedule with Windows Task Scheduler:

```powershell
.\scripts\register_daily_task.ps1 -At "07:00"
```

Registration is opt-in and runs as the current Windows user. It checks for the
local `.venv` and `.env` before registering. The task runs only while that user
is logged in; if the computer is asleep, Task Scheduler starts it when the
computer wakes. The `-StartWhenAvailable` setting does not backfill every
missed day, so use an explicit `--from-date`/`--to-date` pipeline run to catch
up after an extended outage.

Each run writes combined output and an exit status to a timestamped file under
the ignored `logs/` directory. A nonzero pipeline exit code is preserved so
Task Scheduler can report a failure. The task ignores overlapping launches,
and reruns are safe because records are upserted by ID. Review the task's
Last Run Result and the newest log; this local example does not send alerts or
automatically retry a failed daily run.

Preview the registration without creating the task:

```powershell
.\scripts\register_daily_task.ps1 -At "07:00" -WhatIf
```

To remove it:

```powershell
Unregister-ScheduledTask -TaskName "Nordvet Daily VetOne Extract" -Confirm:$false
```

## Stored data

The database contains `clinics` and `appointments` tables. Each has queryable
key fields and a `raw_json` column preserving the complete API record, including
fields not listed in the documentation. Re-running the pipeline upserts records
by clinic or appointment ID, so changed appointment statuses replace older
values instead of creating duplicates.

The pipeline also rebuilds `clinics_full` and `appointments_full` from all
stored raw JSON records. Each full table has one column per API field, including
fields absent from some records (those cells are `NULL`), and uses the record's
`id` as its primary key. `clinics_full` is refreshed after clinic upserts;
`appointments_full` is refreshed after each successfully saved date window.

## Local dashboard

The Streamlit dashboard reads the local SQLite database in read-only mode and
does not call the VetOne API:

```powershell
python -m streamlit run dashboard.py --server.address 127.0.0.1
```

The interface uses a restrained Nordic-inspired palette, clean sans-serif
typography, and card-style summaries. The case brief does not specify official
Nordvet brand colors or fonts, so these are presentation choices rather than
an official brand system.

Below the date selector, it summarizes the five clinics with the most no-shows
and the five with the highest no-show rate over the trailing 30 days ending on
the selected date. Changing the date updates the summaries with the rest of the
dashboard. Outcome volume is shown beside the rate; there is no minimum-volume
threshold. It also provides daily, weekly, and
monthly clinic rankings ordered by highest no-show rate (with no-show count as
the tie-breaker), with a chart metric switch for no-show rate or count across
clinic, service, and vet comparison charts. Weekly/monthly network trend charts
for no-show counts and rates appear before the service and vet comparisons.
The no-show rate is
`no_show / (completed + no_show)`; cancelled, booked, and
deleted appointments are excluded from the rate denominator. Weeks start on
Monday. The selected date determines the daily, weekly, or monthly ranking
period. The trend section has a clinic selector to view one clinic or all
clinics. If yesterday has no loaded appointments, the dashboard selects the
latest available date and states that clearly. Trend data's first and last
periods may be partial depending on the data loaded.

The selected date and ranking period also filter service-type and vet-level
comparisons. Service comparisons cover the available service categories; vet
comparisons show the top 10 entities by the selected chart metric by default
and allow expansion to all vets. Outcome appointment volume is shown with each
rate. Vet comparisons are descriptive only: differences can reflect service
mix and case mix, so they should not be treated as standalone performance
rankings.

Inspect appointment status values before defining the no-show metric; the API
documentation does not specify the complete status vocabulary or which statuses
should count as no-shows. For example, with the SQLite CLI:

```sql
SELECT clinic_id, status, COUNT(*) AS appointment_count
FROM appointments
GROUP BY clinic_id, status
ORDER BY clinic_id, status;
```

## Assumptions and scope

- `from` and `to` filter appointment date in `YYYY-MM-DD` format. The default
  range is yesterday; explicit dates are recommended when backfilling.
- The API returns clinics either as a JSON list or in a `data` list, and
  appointments in a `data` list with `next_cursor` for pagination, as shown in
  the partner documentation.
- Clinic IDs use `id` or `clinic_id`; appointments use `id`, `clinic_id`,
  `scheduled_at`, and `status` as shown in the API example.
- The API's `include_deleted` parameter is enabled so the response can include
  deletion information if the API provides it. The pipeline preserves such
  records as returned; deletion semantics should be confirmed before using them
  to physically remove rows.
- The Python pipeline does not schedule itself or publish the dashboard; the
  Windows Task Scheduler script is an optional local wrapper. The case does
  not infer the no-show status mapping.

For Nordvet production, use a daily Azure Container Apps Job with a managed
identity/Key Vault-backed secret, a managed database instead of local SQLite,
and centralized logs plus failure alerts. Keep the extract idempotent, alert
on a nonzero exit, and provide an explicit backfill path. A Power BI model can
then consume the curated appointment and clinic tables.

## Tests

Run the offline unit tests without API credentials:

```powershell
python -m unittest discover -s tests
```