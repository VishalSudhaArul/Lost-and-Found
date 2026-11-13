# Uploads the existing local photos/evidence to Cloudinary (see upload_media_to_cloud command).
# Copy the "API environment variable" (cloudinary://...) from the Cloudinary dashboard first;
# it is read from the clipboard (or a hidden prompt), used for this run only, never shown or
# saved, and the clipboard is cleared afterwards. Local files are only read.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$url = ""
try { $url = (Get-Clipboard -Raw) } catch { $url = "" }
if ($url) { $url = $url.Trim() -replace '^CLOUDINARY_URL=', '' }
if ($url -notmatch '^cloudinary://') {
    $secure = Read-Host "Paste the Cloudinary API environment variable (cloudinary://...), hidden" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $url = ([Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)).Trim() -replace '^CLOUDINARY_URL=', '' }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
} else {
    Write-Host "Using the Cloudinary setting from the clipboard (not shown)."
}
if ($url -notmatch '^cloudinary://') { throw "No cloudinary:// value found. Copy it from the Cloudinary dashboard and run again." }
try {
    $env:CLOUDINARY_URL = $url
    Set-Location $root
    python manage.py upload_media_to_cloud
    python manage.py upload_media_to_cloud --apply
} finally {
    Remove-Item Env:CLOUDINARY_URL -ErrorAction SilentlyContinue
    $url = $null
    try { Set-Clipboard -Value " " } catch { }
}
