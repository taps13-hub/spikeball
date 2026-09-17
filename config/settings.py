import getpass
import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from psycopg.conninfo import conninfo_to_dict

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before starting production.")
    SECRET_KEY = "development-only-not-for-production"

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
public_url = urlsplit(PUBLIC_BASE_URL)
if (
    not public_url.hostname
    or public_url.path
    or public_url.query
    or public_url.fragment
    or public_url.username
    or public_url.scheme not in (("http", "https") if DEBUG else ("https",))
):
    raise ImproperlyConfigured("PUBLIC_BASE_URL must be an origin (HTTPS in production).")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_ratelimit",
    "leaderboard",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "leaderboard.middleware.TrustedProxyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
        "leaderboard.email_features.context",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"

db = conninfo_to_dict(os.environ["DATABASE_URL"]) if os.getenv("DATABASE_URL") else {}
db.setdefault("connect_timeout", "5")
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": db.pop("dbname", os.getenv("PGDATABASE", "spikeball")),
    "USER": db.pop("user", os.getenv("PGUSER", getpass.getuser())),
    "PASSWORD": db.pop("password", os.getenv("PGPASSWORD", "")),
    "HOST": db.pop("host", os.getenv("PGHOST", "")),
    "PORT": db.pop("port", os.getenv("PGPORT", "")),
    "OPTIONS": db,
    "CONN_MAX_AGE": 60,
}}
AUTH_USER_MODEL = "leaderboard.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{validator}"}
    for validator in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
LANGUAGE_CODE = "en"
TIME_ZONE = os.getenv("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
CACHES = {"default": {
    "BACKEND": "leaderboard.cache.LockedDatabaseCache",
    "LOCATION": "rate_limit_cache",
    "OPTIONS": {"MAX_ENTRIES": 10000},
}}
# This PostgreSQL-only adapter supplies the atomic operations ratelimit requires.
SILENCED_SYSTEM_CHECKS = ["django_ratelimit.W001"]
EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_FEATURES_ENABLED = os.getenv("EMAIL_FEATURES_ENABLED", "1") == "1"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "spikeball@localhost")
INVITATION_DAYS = int(os.getenv("INVITATION_DAYS", "7"))
PASSWORD_RESET_TIMEOUT = 3600
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_REFERRER_POLICY = "same-origin"
TRUST_PROXY_CLIENT_IP = os.getenv("TRUST_PROXY_CLIENT_IP", "0") == "1"
if os.getenv("TRUST_PROXY_HTTPS", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_tokens": {"()": "leaderboard.logging.RedactTokens"}},
    "handlers": {"console": {
        "class": "logging.StreamHandler", "filters": ["redact_tokens"],
    }},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
