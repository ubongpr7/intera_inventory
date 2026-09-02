
import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
import dj_database_url
from core.security import (
    env_bool,
    is_production_environment,
    validate_notification_delivery_settings,
    validate_production_settings,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_SERVER = env_bool(os.getenv("LOCAL_SERVER"))
DEPLOYMENT_ENVIRONMENT = os.getenv("DJANGO_ENV") or os.getenv("DEPLOYMENT_ENV") or "development"
IS_PRODUCTION = is_production_environment(DEPLOYMENT_ENVIRONMENT)


def _split_csv_env(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name)
    if not value:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = os.getenv('SECRET_KEY')

if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set.")

DEBUG = env_bool(os.getenv("DEBUG"))

# Logging
# Django's default logging config won't show `logger.info(...)` from our modules unless you
# define `LOGGING`. This ensures Kafka consumers/producers log to stdout (Docker logs).
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DJANGO_LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", LOG_LEVEL).upper()
KAFKA_LOG_LEVEL = os.getenv("KAFKA_LOG_LEVEL", LOG_LEVEL).upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": DJANGO_LOG_LEVEL,
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console"],
            "level": DJANGO_LOG_LEVEL,
            "propagate": False,
        },
        "subapps.kafka": {
            "handlers": ["console"],
            "level": KAFKA_LOG_LEVEL,
            "propagate": False,
        },
    },
}

_default_allowed_hosts = [
    'localhost',
    '127.0.0.1',
    '10.0.2.2',
    'dev.inventory.interaims.com',
]
_allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "").strip()
if IS_PRODUCTION and not _allowed_hosts_env:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be explicitly configured when DJANGO_ENV is production.")
ALLOWED_HOSTS = (
    [host.strip() for host in _allowed_hosts_env.split(",") if host.strip()]
    if _allowed_hosts_env
    else _default_allowed_hosts
)


# ALLOWED_HOSTS = ['*']

# Application definition
DJ_DEFAULT_INSTALLED_APPS=[
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS=[
    'django_extensions',
     "rest_framework",
    "rest_framework.authtoken",
    'corsheaders',
    'whitenoise.runserver_nostatic',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'oauth2_provider',
    'drf_yasg',
    'djoser',
    'social_django',
    'schema_graph',
]
CORE_APPS = [
    'mainapps.company',
    'mainapps.content_type_linking_models',
    'mainapps.identity',
    'mainapps.inventory',
    'mainapps.kafka_reliability',
    'mainapps.orders',
    'mainapps.projections',
    'mainapps.stock',
]
INSTALLED_APPS=[
]
INSTALLED_APPS.extend(DJ_DEFAULT_INSTALLED_APPS) 
INSTALLED_APPS.extend(THIRD_PARTY_APPS) 
INSTALLED_APPS.extend(CORE_APPS) 


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'corsheaders.middleware.CorsMiddleware',
   
]


ROOT_URLCONF = 'core.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR/"templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

DATABASE_CONN_MAX_AGE = int(os.getenv("DATABASE_CONN_MAX_AGE", "600"))
DATABASE_CONN_HEALTH_CHECKS = os.getenv("DATABASE_CONN_HEALTH_CHECKS", "True") == "True"
DATABASE_CONNECT_TIMEOUT = int(os.getenv("DATABASE_CONNECT_TIMEOUT", "10"))

DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL'),
        conn_max_age=DATABASE_CONN_MAX_AGE,
        conn_health_checks=DATABASE_CONN_HEALTH_CHECKS,
    )
}
if DATABASE_CONNECT_TIMEOUT > 0:
    DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = DATABASE_CONNECT_TIMEOUT
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]




AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# S3 Configuration
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME')
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and AWS_STORAGE_BUCKET_NAME and AWS_S3_REGION_NAME:
    AWS_S3_CUSTOM_DOMAIN = "%s.s3.amazonaws.com" % AWS_STORAGE_BUCKET_NAME
    AWS_S3_CONNECT_TIMEOUT = 10
    AWS_S3_TIMEOUT = 60
    AWS_S3_FILE_OVERWRITE = True

    STORAGES = {
            "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
            "staticfiles": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
    }

# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

LOGIN_URL='/accounts/signin'
LOGIN_REDIRECT_URL='/accounts/signin/?next={url}'
DEFAULT_REDIEECT_URL='/'
STATIC_URL = '/static/'

STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')  

STATICFILES_DIRS=[os.path.join(BASE_DIR,'static')]

MEDIA_URL = '/media/'
MEDIAFILES_DIRS=[os.path.join(BASE_DIR,'media')]
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django_smtp_ssl.SSLEmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "465"))
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "True") == "True"
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "False") == "True"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
EMAIL_SUPPORT_EMAIL = os.getenv("EMAIL_SUPPORT_EMAIL", "support@interaims.com").strip()
EMAIL_NOREPLY_ADDRESS = os.getenv("EMAIL_NOREPLY_ADDRESS", "noreply@interaims.com").strip()
EMAIL_AGENT_ADDRESS = os.getenv("EMAIL_AGENT_ADDRESS", "intera-agent@interaims.com").strip()
EMAIL_SYSTEM_FROM_EMAIL = os.getenv("EMAIL_SYSTEM_FROM_EMAIL", f"Intera IMS <{EMAIL_NOREPLY_ADDRESS}>").strip()
EMAIL_AGENT_FROM_EMAIL = os.getenv("EMAIL_AGENT_FROM_EMAIL", f"Intera Agent <{EMAIL_AGENT_ADDRESS}>").strip()
EMAIL_DEFAULT_REPLY_TO = os.getenv("EMAIL_DEFAULT_REPLY_TO", EMAIL_SUPPORT_EMAIL).strip()
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    EMAIL_SYSTEM_FROM_EMAIL,
).strip()
EMAIL_BRAND_LOGO_URL = os.getenv("EMAIL_BRAND_LOGO_URL", "").strip()
EMAIL_BRAND_LOGO_LIGHT_URL = os.getenv("EMAIL_BRAND_LOGO_LIGHT_URL", "").strip()
EMAIL_BRAND_LOGO_DARK_URL = os.getenv("EMAIL_BRAND_LOGO_DARK_URL", "").strip()
EMAIL_BRAND_STATIC_LOGO_PATH = "images/logos/INTERA-EMAIL-LOGO-DARK.png"
EMAIL_SHARED_STATIC_BUCKET = os.getenv("EMAIL_SHARED_STATIC_BUCKET", os.getenv("AWS_STORAGE_BUCKET_NAME", "")).strip()
EMAIL_SHARED_STATIC_LOCATION = os.getenv(
    "EMAIL_SHARED_STATIC_LOCATION",
    os.getenv("AWS_STATIC_LOCATION", "assessment/static"),
).strip("/")
FRONTEND_SITE_URL = os.getenv("FRONTEND_SITE_URL", os.getenv("SITE_URL", "http://localhost:3000")).strip().rstrip("/")
NOTIFICATION_DOCUMENT_BASE_URL = os.getenv("NOTIFICATION_DOCUMENT_BASE_URL", "").strip().rstrip("/")
NOTIFICATION_DOCUMENT_SIGNING_SALT = os.getenv("NOTIFICATION_DOCUMENT_SIGNING_SALT", "inventory-notification-document-v1")
NOTIFICATION_DOCUMENT_URL_TTL_SECONDS = int(os.getenv("NOTIFICATION_DOCUMENT_URL_TTL_SECONDS", "900"))
PURCHASE_ORDER_EMAIL_DELIVERY_MODE = os.getenv("PURCHASE_ORDER_EMAIL_DELIVERY_MODE", "direct").strip().lower()
RETURN_ORDER_EMAIL_DELIVERY_MODE = os.getenv("RETURN_ORDER_EMAIL_DELIVERY_MODE", "direct").strip().lower()
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and AWS_STORAGE_BUCKET_NAME:
    AWS_STATIC_LOCATION = os.getenv("AWS_STATIC_LOCATION", "assessment/static")
    AWS_S3_CUSTOM_DOMAIN = "%s.s3.amazonaws.com" % AWS_STORAGE_BUCKET_NAME
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/{AWS_STATIC_LOCATION}/"
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
        "staticfiles": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {"location": AWS_STATIC_LOCATION},
        },
    }
if not EMAIL_BRAND_LOGO_URL and EMAIL_SHARED_STATIC_BUCKET:
    EMAIL_BRAND_LOGO_URL = (
        f"https://{EMAIL_SHARED_STATIC_BUCKET}.s3.amazonaws.com/"
        f"{EMAIL_SHARED_STATIC_LOCATION}/{EMAIL_BRAND_STATIC_LOGO_PATH}"
    )
elif not EMAIL_BRAND_LOGO_URL:
    EMAIL_BRAND_LOGO_URL = f"{STATIC_URL.rstrip('/')}/{EMAIL_BRAND_STATIC_LOGO_PATH}"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
    






AUTHENTICATION_BACKENDS = [
    'social_core.backends.google.GoogleOAuth2',
    'social_core.backends.facebook.FacebookOAuth2',
    "djoser.auth_backends.LoginFieldBackend",

    'django.contrib.auth.backends.ModelBackend',
]


def _read_key_from_env(value_var: str) -> str | None:
    key_value = os.getenv(value_var)
    if key_value:
        return key_value.replace("\\n", "\n")
    return None


JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "RS256")
JWT_VERIFYING_KEY = _read_key_from_env("JWT_PUBLIC_KEY")

if not JWT_ALGORITHM.upper().startswith(("RS", "ES")):
    raise ImproperlyConfigured(
        "Downstream services must use an asymmetric JWT algorithm (RS*/ES*) to verify identity-service tokens."
    )

if not JWT_VERIFYING_KEY:
    raise ImproperlyConfigured(
        "JWT_PUBLIC_KEY must be set for downstream JWT verification."
    )

# DJOSER CONFIGURATION
DJOSER = {
    'PASSWORD_RESET_CONFIRM_URL': 'accounts/password_reset/{uid}/{token}',
    'USERNAME_RESET_CONFIRM_URL': 'username/reset/confirm/{uid}/{token}',
    'ACTIVATION_URL': 'activate/{uid}/{token}',
    'SEND_ACTIVATION_EMAIL': True,
    'USER_CREATE_PASSWORD_RETYPE': True,
    'PASSWORD_RESET_CONFIRM_RETYPE': True,
    'LOGOUT_ON_PASSWORD_CHANGE': True,
    'TOKEN_MODEL': 'rest_framework.authtoken.models.Token',  
    'SOCIAL_AUTH_ALLOWED_REDIRECT_URIS': os.getenv('SOCIAL_AUTH_ALLOWED_REDIRECT_URIS', '').split(','),
}





SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=2),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=6),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': False,
    'ALGORITHM': JWT_ALGORITHM,
    'SIGNING_KEY': None,
    'VERIFYING_KEY': JWT_VERIFYING_KEY,
    # Treat empty strings as unset so we don't enforce/emit `aud`/`iss` with "".
    'AUDIENCE': os.getenv("JWT_AUDIENCE") or None,
    'ISSUER': os.getenv("JWT_ISSUER") or None,
    'JWK_URL': os.getenv("JWT_JWK_URL") or None,
    'LEEWAY': 0,

    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'USER_AUTHENTICATION_RULE': 'rest_framework_simplejwt.authentication.default_user_authentication_rule',

    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',

    'JTI_CLAIM': 'jti',

    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(minutes=5),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
}

AUTH_COOKIE='access'
AUTH_COOKIE_ACCESS_MAX_AGE=60*10
AUTH_COOKIE_REFRESH_MAX_AGE=60*60*24
AUTH_COOKIE_SECURE=env_bool(os.getenv("AUTH_COOKIE_SECURE"), default=IS_PRODUCTION)
AUTH_COOKIE_HTTP_ONLY=True
AUTH_COOKIE_PATH='/'
AUTH_COOKIE_SAMESITE=os.getenv("AUTH_COOKIE_SAMESITE", "Lax" if IS_PRODUCTION else "None")
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTStatelessUserAuthentication',
    )
}

CORS_ALLOW_ALL_ORIGINS=env_bool(os.getenv("CORS_ALLOW_ALL_ORIGINS"))
CORS_ORIGIN_ALLOW_ALL=CORS_ALLOW_ALL_ORIGINS

CORS_ALLOW_CREDENTIALS=env_bool(os.getenv("CORS_ALLOW_CREDENTIALS"), default=True)

CORS_ALLOW_METHODS = (
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
)
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'x-intera-authorization-context',
    'x-device-id',
]

_default_cors_allowed_origins = [
    "http://localhost:3000",
    "http://localhost:3005",
    "http://10.0.2.2:3000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3005",
    "http://10.0.2.2:8080",
    'https://interaims.com',
    'https://www.interaims.com',
    'https://dev.interaims.com'
]
CORS_ALLOWED_ORIGINS = _split_csv_env(
    "CORS_ALLOWED_ORIGINS",
    [] if IS_PRODUCTION else _default_cors_allowed_origins,
)
CSRF_TRUSTED_ORIGINS = _split_csv_env("CSRF_TRUSTED_ORIGINS", CORS_ALLOWED_ORIGINS)

# Security / HTTPS.
# For local development (`DEBUG=True` or `LOCAL_SERVER=True`), force these off to avoid
# confusing localhost HTTPS redirects and missing cookies.
SECURE_SSL_REDIRECT = env_bool(os.getenv("SECURE_SSL_REDIRECT"), default=IS_PRODUCTION)

SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool(os.getenv("SECURE_PROXY_SSL_HEADER_ENABLED"), default=IS_PRODUCTION)
    else None
)

SESSION_COOKIE_SECURE = env_bool(os.getenv("SESSION_COOKIE_SECURE"), default=IS_PRODUCTION)
CSRF_COOKIE_SECURE = env_bool(os.getenv("CSRF_COOKIE_SECURE"), default=IS_PRODUCTION)
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000" if IS_PRODUCTION else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(os.getenv("SECURE_HSTS_INCLUDE_SUBDOMAINS"), default=IS_PRODUCTION)
SECURE_HSTS_PRELOAD = env_bool(os.getenv("SECURE_HSTS_PRELOAD"), default=IS_PRODUCTION)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if DEBUG or LOCAL_SERVER:
    SECURE_SSL_REDIRECT = False
    SECURE_PROXY_SSL_HEADER = None
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    AUTH_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False

if IS_PRODUCTION:
    validate_production_settings(
        debug=DEBUG,
        local_server=LOCAL_SERVER,
        allowed_hosts=ALLOWED_HOSTS,
        cors_allow_all=CORS_ALLOW_ALL_ORIGINS,
        cors_allowed_origins=CORS_ALLOWED_ORIGINS,
        csrf_trusted_origins=CSRF_TRUSTED_ORIGINS,
        secure_ssl_redirect=SECURE_SSL_REDIRECT,
        session_cookie_secure=SESSION_COOKIE_SECURE,
        csrf_cookie_secure=CSRF_COOKIE_SECURE,
        auth_cookie_secure=AUTH_COOKIE_SECURE,
        hsts_seconds=SECURE_HSTS_SECONDS,
    )
    validate_notification_delivery_settings(
        purchase_order_mode=PURCHASE_ORDER_EMAIL_DELIVERY_MODE,
        return_order_mode=RETURN_ORDER_EMAIL_DELIVERY_MODE,
        document_base_url=NOTIFICATION_DOCUMENT_BASE_URL,
        signing_salt=NOTIFICATION_DOCUMENT_SIGNING_SALT,
    )
FILE_UPLOAD_TIMEOUT = 3600
DATA_UPLOAD_MAX_MEMORY_SIZE = 2147483648  # 2GB
FILE_UPLOAD_MAX_MEMORY_SIZE = 2147483648  # 2GB


"""
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis:6379/",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    }
}
"""

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')  
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
USE_L10N = True
USE_THOUSAND_SEPARATOR = True
