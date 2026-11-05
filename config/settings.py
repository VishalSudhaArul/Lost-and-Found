"""
Django settings for the Campus Lost & Found Platform (EduRev P09, Track P).

All secrets and environment-specific values come from environment variables
(optionally loaded from a local, untracked ``.env`` file). See ``.env.example``.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real env vars always win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        # Development only: a throwaway key so the project boots. Sessions are
        # invalidated on every restart, which is acceptable for local work.
        import secrets as _secrets
        SECRET_KEY = "dev-insecure-" + _secrets.token_urlsafe(40)
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is False.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
# On Render the service's own hostname (e.g. lost-and-found.onrender.com) is provided automatically.
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    if RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    if f"https://{RENDER_EXTERNAL_HOSTNAME}" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django.contrib.humanize",
    "lostfound",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves collected static files in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "lostfound.middleware.DatabaseUnavailableMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "lostfound.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database -------------------------------------------------------------
# EduRev P09 Track P: PostgreSQL is the only supported database (pg_trgm, full-text
# search, GIN indexes and append-only triggers depend on it). There is deliberately no
# SQLite fallback: if PostgreSQL is unreachable, `lostfound.E002` reports it at startup.
DB_BACKEND = env("DB_BACKEND", "postgres").lower()
if DB_BACKEND != "postgres":
    raise ImproperlyConfigured(f"DB_BACKEND={DB_BACKEND!r} is not supported: this project runs on PostgreSQL only. "
                               "Set DB_BACKEND=postgres in .env.")


def _postgres_from_url(url):
    """postgres://user:password@host:port/dbname?sslmode=require  ->  Django settings (no extra package)."""
    from urllib.parse import parse_qs, unquote, urlparse
    url = (url or "").strip()
    parts = urlparse(url)
    if parts.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured(f"DATABASE_URL scheme '{parts.scheme}' is not supported: must be a postgres:// or postgresql:// URL (PostgreSQL only).")
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    return {
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or 5432),
        "SSLMODE": query.get("sslmode"),
    }


def _resolve_sslmode(db_conf, get_env=os.environ.get):
    """Render PostgreSQL SSL requirements:
    Cloud databases on Render (and remote PostgreSQL instances) require or expect SSL.
    If POSTGRES_SSLMODE or ?sslmode=... was not explicitly specified, default to 'require'
    when running on Render or when connecting to a remote host (e.g. *.render.com).
    For local development (localhost / 127.0.0.1), keep SSL disabled.
    """
    sslmode = get_env("POSTGRES_SSLMODE") or db_conf.pop("SSLMODE", None)
    db_conf.pop("SSLMODE", None)
    if not sslmode:
        host = db_conf.get("HOST", "")
        if host and host not in ("localhost", "127.0.0.1"):
            if get_env("RENDER") or host.endswith(".render.com"):
                sslmode = "require"
    return sslmode


_db_url = env("DATABASE_URL")
if _db_url:
    _db_url = _db_url.strip()

_db = _postgres_from_url(_db_url) if _db_url else {
    "NAME": env("POSTGRES_DB", "lostfound"),
    "USER": env("POSTGRES_USER", "lostfound"),
    "PASSWORD": env("POSTGRES_PASSWORD", ""),
    "HOST": env("POSTGRES_HOST", "localhost"),
    "PORT": env("POSTGRES_PORT", "5432"),
    "SSLMODE": None,
}
_db_options = {"connect_timeout": int(env("POSTGRES_CONNECT_TIMEOUT", "5"))}
_sslmode = _resolve_sslmode(_db, env)
if _sslmode:
    _db_options["sslmode"] = _sslmode

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        **_db,
        "CONN_MAX_AGE": int(env("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": _db_options,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"

LANGUAGE_CODE = "en-in"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# --- Static & media -------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Compressed, cache-busted static files served by WhiteNoise. Requires `collectstatic`, so it is
# switched on by STATIC_MANIFEST=True in production (render.yaml) and off for local work and tests.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if env_bool("STATIC_MANIFEST", False) else
                    "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_URL = "media/"
# Public, processed (EXIF-stripped) item photos only.
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
# Proof photos, signatures, disposal evidence: never served by MEDIA_URL,
# only through permission-checked views.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "private_media")))
# Serve the public photos in MEDIA_ROOT from Django itself (production on a single Render
# instance with a persistent disk). PRIVATE_MEDIA_ROOT is never served this way.
SERVE_MEDIA = env_bool("SERVE_MEDIA", DEBUG)

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
MAX_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o640

# --- Caching (used by rate limiting) -------------------------------------
# LocMem is per-process. For multi-process production deployments switch to a
# shared cache (e.g. DatabaseCache: CACHE_BACKEND=database, then createcachetable).
if env("CACHE_BACKEND", "locmem") == "database":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "lostfound_cache"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Public board pages are cached (redacted DTOs only); any Item/Location change invalidates them.
BOARD_CACHE_SECONDS = int(env("BOARD_CACHE_SECONDS", "60"))

# --- Application settings --------------------------------------------------
FIELD_ENCRYPTION_KEYS = env_list("FIELD_ENCRYPTION_KEYS")
ALLOWED_EMAIL_DOMAINS = [d.lower().lstrip("@") for d in env_list("ALLOWED_EMAIL_DOMAINS")]
# Lost & Found team and administrator accounts can be limited to official domains.
ALLOWED_STAFF_EMAIL_DOMAINS = [d.lower().lstrip("@") for d in env_list("ALLOWED_STAFF_EMAIL_DOMAINS")]
# Staff sign-in second step: a one-time code emailed to the account owner.
STAFF_CODE_TTL_SECONDS = int(env("STAFF_CODE_TTL_SECONDS", "600"))
STAFF_CODE_MAX_ATTEMPTS = int(env("STAFF_CODE_MAX_ATTEMPTS", "5"))
# Account-activation and password-reset links expire after this many seconds (3 days).
PASSWORD_RESET_TIMEOUT = int(env("PASSWORD_RESET_TIMEOUT", str(3 * 24 * 3600)))


def _float_or_none(name):
    value = env(name)
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a number.")


CAMPUS_MAP = {
    "center_lat": _float_or_none("CAMPUS_MAP_CENTER_LAT"),
    "center_lng": _float_or_none("CAMPUS_MAP_CENTER_LNG"),
    "zoom": int(env("CAMPUS_MAP_ZOOM", "17")),
    "tile_url": env("CAMPUS_MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
    "attribution": env("CAMPUS_MAP_ATTRIBUTION", "&copy; OpenStreetMap contributors"),
}

LOGIN_RATE_LIMIT = (5, 15 * 60)       # failures, window seconds
CLAIM_RATE_LIMIT = (5, 60 * 60)       # new claims per user per hour
VERIFY_RATE_LIMIT = (10, 60 * 60)     # verification submissions per user per hour
OTP_REVEAL_RATE_LIMIT = (5, 60 * 60)  # handover code generations per user per hour

# --- Security ---------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    if env_bool("DJANGO_BEHIND_PROXY", bool(env("RENDER"))):  # Render terminates HTTPS at its proxy
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
# SMTP delivery (used when EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend).
# For Gmail: EMAIL_HOST=smtp.gmail.com, EMAIL_PORT=587, EMAIL_USE_TLS=True and a Google "App password".
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "25"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
# Google shows App passwords in groups of four ("abcd efgh ijkl mnop"); the spaces are not part of it.
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "").replace(" ", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "15"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "webmaster@localhost")
# Every system email (reset links, sign-in codes, activation, claim/handover notices) goes
# from this one platform account through lostfound.services.mailer.
EMAIL_NOTIFICATIONS = env_bool("EMAIL_NOTIFICATIONS", True)
SITE_URL = env("SITE_URL", f"https://{RENDER_EXTERNAL_HOSTNAME}" if RENDER_EXTERNAL_HOSTNAME else "http://127.0.0.1:8000")
# HTTPS email API for hosts that block SMTP ports (Render free web services):
# EMAIL_BACKEND=lostfound.mail_backends.BrevoEmailBackend with BREVO_API_KEY.
BREVO_API_KEY = env("BREVO_API_KEY", "").strip()
# Or a Google Apps Script web app that sends from your own Gmail (free): see docs/apps-script/Code.gs.
APPS_SCRIPT_MAIL_URL = env("APPS_SCRIPT_MAIL_URL", "").strip()
APPS_SCRIPT_MAIL_SECRET = env("APPS_SCRIPT_MAIL_SECRET", "").strip()
# Delivery selected but its credential missing: keep printing emails in the console instead of failing.
EMAIL_SMTP_INCOMPLETE = EMAIL_BACKEND.endswith("smtp.EmailBackend") and not EMAIL_HOST_PASSWORD
EMAIL_API_INCOMPLETE = ((EMAIL_BACKEND.endswith("BrevoEmailBackend") and not BREVO_API_KEY) or
                        (EMAIL_BACKEND.endswith("AppsScriptEmailBackend")
                         and not (APPS_SCRIPT_MAIL_URL and APPS_SCRIPT_MAIL_SECRET)))
if EMAIL_SMTP_INCOMPLETE or EMAIL_API_INCOMPLETE:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# Never print emails in production: they contain sign-in codes and reset links, and the console is
# the hosting provider's log. With DEBUG off, an unconfigured or console backend refuses to send
# (the failure is logged without the message) unless EMAIL_ALLOW_CONSOLE=True is set explicitly.
EMAIL_DELIVERY_DISABLED = (not DEBUG and EMAIL_BACKEND.endswith("console.EmailBackend")
                           and not env_bool("EMAIL_ALLOW_CONSOLE", False))
if EMAIL_DELIVERY_DISABLED:
    EMAIL_BACKEND = "lostfound.mail_backends.UnconfiguredEmailBackend"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"lostfound": {"handlers": ["console"], "level": env("APP_LOG_LEVEL", "INFO"), "propagate": False}},
}
