# One-time PostgreSQL setup for Campus Lost & Found (Windows, PostgreSQL 13+).
# Creates (or updates the password of) the role and database named in .env.
# You are asked for the PostgreSQL *superuser* password; it is used for this run only and not stored.
# The app password is read from .env and never printed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw ".env not found. Copy .env.example to .env first." }

$cfg = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $k = $k.Trim()
    if (-not $cfg.ContainsKey($k)) { $cfg[$k] = $v.Trim().Trim('"').Trim("'") }  # first value wins, like Django
}
foreach ($k in 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD') {
    if (-not $cfg[$k]) { throw "$k is missing in .env" }
}
$pgHost = if ($cfg['POSTGRES_HOST']) { $cfg['POSTGRES_HOST'] } else { 'localhost' }
$port = if ($cfg['POSTGRES_PORT']) { $cfg['POSTGRES_PORT'] } else { '5432' }
foreach ($name in $cfg['POSTGRES_DB'], $cfg['POSTGRES_USER']) {
    if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "Use only letters, digits and _ in POSTGRES_DB / POSTGRES_USER." }
}

$psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
if (-not $psql) {
    $psql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $psql) { throw "psql.exe not found. Install PostgreSQL or add its bin folder to PATH." }

$super = Read-Host "PostgreSQL superuser name" -Default 'postgres'
if (-not $super) { $super = 'postgres' }
$secure = Read-Host "Password for PostgreSQL user '$super'" -AsSecureString
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

$db = $cfg['POSTGRES_DB']; $user = $cfg['POSTGRES_USER']
$pw = $cfg['POSTGRES_PASSWORD'].Replace("'", "''")
$sql = @"
SELECT format('CREATE ROLE %I LOGIN CREATEDB PASSWORD %L', '$user', '$pw') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$user') \gexec
SELECT format('ALTER ROLE %I LOGIN CREATEDB PASSWORD %L', '$user', '$pw') \gexec
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', '$db', '$user') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$db') \gexec
\c $db
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SELECT 'ready' AS status, current_database() AS database, (SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm') AS pg_trgm;
"@
try {
    $sql | & $psql -X -q -v ON_ERROR_STOP=1 -h $pgHost -p $port -U $super -d postgres
    if ($LASTEXITCODE -ne 0) { throw "psql failed (exit $LASTEXITCODE)." }
    Write-Host "PostgreSQL role '$user' and database '$db' are ready. Next: python manage.py migrate"
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
