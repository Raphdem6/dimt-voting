from pathlib import Path
import os
import secrets
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv


def env_flag(name, default="0"):
    return os.environ.get(name, default) == "1"


# ----------------------------------------------------------------- core / secrets
DEBUG = env_flag("DJANGO_DEBUG", "0")          # OFF by default. For local development: DJANGO_DEBUG=1


def _secret_key():
    """Use DJANGO_SECRET_KEY if set, otherwise generate one once and keep it in .secret_key"""
    if os.environ.get("DJANGO_SECRET_KEY"):
        return os.environ["DJANGO_SECRET_KEY"]
    f = BASE_DIR / ".secret_key"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(64))
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text().strip()


SECRET_KEY = _secret_key()
ALLOWED_HOSTS = ['.onrender.com']
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_ORIGINS", "").split(",") if o.strip()]

# Secret admin address (must end with "/"). The usual /admin/ is a trap that blocks and blacklists visitors.
ADMIN_URL = os.environ.get("DJANGO_ADMIN_URL", "dimtmwelection/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "voting",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "voting.security.GuardMiddleware",            # admin trap, IP blocks, brute-force limits, security headers
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "voting.context.site",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
# Production example (PostgreSQL):
# DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "dimt_voting",
#              "USER": "...", "PASSWORD": "...", "HOST": "localhost", "PORT": "5432"}}

# Rate-limit counters live in the cache. File-based so all server workers share them.
if TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
                          "LOCATION": str(BASE_DIR / ".cache")}}

# Only applies to staff accounts (students log in with name + ID number, no password)
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Blantyre"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ----------------------------------------------------------------- sessions / cookies / headers
SESSION_COOKIE_AGE = 60 * 30            # students are logged out after 30 minutes of inactivity
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

# Set DJANGO_HTTPS=1 once the site is served over HTTPS (strongly recommended for real elections)
if env_flag("DJANGO_HTTPS"):
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
# Set BEHIND_PROXY=1 when nginx/Apache sits in front. The proxy MUST set X-Forwarded-For and X-Forwarded-Proto.
BEHIND_PROXY = env_flag("BEHIND_PROXY")
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ----------------------------------------------------------------- app limits
# Block a flagged device by IP address as well as by browser cookie. Turn OFF (DJANGO_BLOCK_BY_IP=0) if many
# students share one public IP (campus Wi-Fi), otherwise one person could block everyone on that network.
BLOCK_BY_IP = env_flag("DJANGO_BLOCK_BY_IP", "1")
MAX_FAILED_LOGINS = 5                   # per student ID before temporary lock
LOCKOUT_MINUTES = 15
LOGIN_IP_LIMIT = 12                     # failed logins per device (IP) per window
ADMIN_LOGIN_LIMIT = 5                   # failed admin logins per IP per window
RATE_WINDOW_SECONDS = 15 * 60
MAX_ROLL_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024

LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "WARNING"}}
