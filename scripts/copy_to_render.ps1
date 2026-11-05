# Copies the local PostgreSQL data into the Render database (see copy_to_render.py).
# The Render EXTERNAL connection string is taken from the clipboard (click "copy" next to
# "External Database URL" in Render first) or, if the clipboard doesn't hold one, from a hidden
# prompt. It is passed to the copy for this run only, never shown or saved, and the clipboard is
# cleared afterwards.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$url = ""
try { $url = (Get-Clipboard -Raw) } catch { $url = "" }
if ($url) { $url = $url.Trim() }
if ($url -notmatch '^postgres(ql)?://') {
    $secure = Read-Host "Paste the Render database EXTERNAL connection string (hidden), then press Enter" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $url = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
} else {
    Write-Host "Using the Render connection string from the clipboard (not shown)."
}
if ($url -notmatch '^postgres(ql)?://') { throw "No postgres:// connection string found. Copy the External Database URL in Render and run again." }
try {
    $env:RENDER_DATABASE_URL = $url
    python (Join-Path $root "scripts\copy_to_render.py")
} finally {
    Remove-Item Env:RENDER_DATABASE_URL -ErrorAction SilentlyContinue
    $url = $null
    try { Set-Clipboard -Value " " } catch { }
}
