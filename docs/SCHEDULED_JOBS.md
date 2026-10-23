# Scheduled jobs

| Command | Suggested schedule | What it does |
|---|---|---|
| `python manage.py refresh_matches` | nightly, e.g. 02:00 | recomputes match suggestions for all open lost reports and drops stale ones |
| `python manage.py check_ageing` | daily, e.g. 06:00 | warns security before retention ends, moves expired unclaimed items to disposal review, escalates overdue items to campus administrators |

Both are idempotent (notifications are de-duplicated), so running them twice is harmless.

## Windows Task Scheduler
Create `run_job.bat` in the project folder:
```bat
@echo off
cd /d "D:\LOST AND FOUND"
call venv\Scripts\activate
python manage.py %1 >> logs\jobs.log 2>&1
```
Then (Command Prompt, once):
```bat
mkdir logs
schtasks /Create /TN "LostFound refresh_matches" /TR "\"D:\LOST AND FOUND\run_job.bat\" refresh_matches" /SC DAILY /ST 02:00
schtasks /Create /TN "LostFound check_ageing" /TR "\"D:\LOST AND FOUND\run_job.bat\" check_ageing" /SC DAILY /ST 06:00
```

## Linux (cron)
```
0 2 * * * cd /srv/lostfound && venv/bin/python manage.py refresh_matches >> logs/jobs.log 2>&1
0 6 * * * cd /srv/lostfound && venv/bin/python manage.py check_ageing   >> logs/jobs.log 2>&1
```

## Key rotation
Put the new Fernet key first in `FIELD_ENCRYPTION_KEYS` (old keys after it), run `python manage.py rotate_encryption_keys`, then remove the old keys.
