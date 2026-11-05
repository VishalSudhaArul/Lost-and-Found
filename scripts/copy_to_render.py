"""Copy the local PostgreSQL database to a NEW, empty Render PostgreSQL database, then verify it.

    python scripts/copy_to_render.py

* Reads the local connection from .env (POSTGRES_*). Never modifies the local database.
* Writes a timestamped backup of the local database to backups/ first (git-ignored).
* Asks for the Render database's EXTERNAL connection string with a hidden prompt; it is
  passed to pg_restore through environment variables and never printed or saved.
* Restores with --clean --if-exists (replaces only what the app's own migrations created in
  the new database), then compares the row count of every table and checks pg_trgm and the
  append-only triggers. Prints table names and counts only.
"""
import getpass
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import psycopg

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    cfg = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg.setdefault(k.strip(), v.strip().strip('"').strip("'"))  # first value wins, like Django
    return cfg


def pg_tool(name):
    found = shutil.which(name)
    if found:
        return found
    hits = sorted(glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{name}.exe"), reverse=True)
    if not hits:
        sys.exit(f"{name} not found; install the PostgreSQL client tools.")
    return hits[0]


def counts(conninfo):
    with psycopg.connect(**conninfo, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
        tables = [r[0] for r in cur.fetchall()]
        out = {}
        for t in tables:
            cur.execute(f'SELECT count(*) FROM "{t}"')
            out[t] = cur.fetchone()[0]
        cur.execute("SELECT extname FROM pg_extension ORDER BY 1")
        ext = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1")
        trig = [r[0] for r in cur.fetchall()]
    return out, ext, trig


def main():
    cfg = load_env()
    local = {"host": cfg.get("POSTGRES_HOST", "localhost"), "port": cfg.get("POSTGRES_PORT", "5432"),
             "user": cfg["POSTGRES_USER"], "password": cfg["POSTGRES_PASSWORD"], "dbname": cfg["POSTGRES_DB"]}

    backup = ROOT / "backups" / f"pg-local-before-render-copy-{datetime.now():%Y%m%d-%H%M%S}.dump"
    backup.parent.mkdir(exist_ok=True)
    env = {**os.environ, "PGPASSWORD": local["password"]}
    print("1/4 Backing up the local database (read-only)...")
    r = subprocess.run([pg_tool("pg_dump"), "-Fc", "-h", local["host"], "-p", str(local["port"]), "-U", local["user"],
                        "-f", str(backup), local["dbname"]], env=env, capture_output=True, text=True)
    if r.returncode != 0 or not backup.exists() or backup.stat().st_size == 0:
        sys.exit(f"pg_dump failed (exit {r.returncode}); nothing was copied.")
    print(f"    backup: {backup} ({backup.stat().st_size:,} bytes)")

    # Set by scripts/copy_to_render.ps1 from a hidden PowerShell prompt (never echoed or saved).
    url = os.environ.pop("RENDER_DATABASE_URL", "").strip()
    if url:
        print("2/4 Using the connection string from the hidden prompt.")
    else:
        url = getpass.getpass("2/4 Paste the Render database EXTERNAL connection string (hidden): ").strip()
    p = urlparse(url)
    if p.scheme not in ("postgres", "postgresql") or not p.hostname:
        sys.exit("That is not a postgres:// connection string. Nothing was copied.")
    remote = {"host": p.hostname, "port": str(p.port or 5432), "user": unquote(p.username or ""),
              "password": unquote(p.password or ""), "dbname": unquote(p.path.lstrip("/"))}
    sslmode = parse_qs(p.query).get("sslmode", ["require"])[-1]

    print("3/4 Restoring into the Render database...")
    renv = {**os.environ, "PGHOST": remote["host"], "PGPORT": remote["port"], "PGUSER": remote["user"],
            "PGPASSWORD": remote["password"], "PGDATABASE": remote["dbname"], "PGSSLMODE": sslmode}
    r = subprocess.run([pg_tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-privileges",
                        "-d", remote["dbname"], str(backup)], env=renv, capture_output=True, text=True)
    errors = [ln for ln in r.stderr.splitlines() if "error" in ln.lower()]
    print(f"    pg_restore exit {r.returncode}; {len(errors)} error line(s)")
    for ln in errors[:15]:
        print("     ", ln.replace(remote["password"], "******") if remote["password"] else ln)

    print("4/4 Verifying...")
    src, _, _ = counts(local)
    dst, ext, trig = counts({**remote, "sslmode": sslmode})
    bad = 0
    for t in sorted(src):
        mark = "OK  " if dst.get(t) == src[t] else "DIFF"
        bad += mark == "DIFF"
        print(f"    {mark} {t:40} local {src[t]:>5}  render {dst.get(t, 'missing')!s:>7}")
    print(f"    extensions on Render: {ext}")
    print(f"    triggers on Render:   {trig}")
    ok = bad == 0 and "pg_trgm" in ext and len(trig) >= 3
    print("RESULT:", "all tables match, pg_trgm and append-only triggers present" if ok else f"{bad} table(s) differ - check above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
