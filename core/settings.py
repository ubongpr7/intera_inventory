
import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
import dj_database_url

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_SERVER = os.getenv('LOCAL_SERVER', 'False')=='True'


SECRET_KEY = os.getenv('SECRET_KEY')

if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set.")

DEBUG = os.getenv("DEBUG", "False") == "True"

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
    'dev.inventory.interaims.com',
]
_allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "").strip()
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

if LOCAL_SERVER:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': dj_database_url.config(
            default=os.getenv('DATABASE_URL'),
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
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
EMAIL_PORT = 465  
EMAIL_USE_SSL = True
EMAIL_USE_TLS = False
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
    






AUTHENTICATION_BACKENDS = [
    'social_core.backends.google.GoogleOAuth2',
    'social_core.backends.facebook.FacebookOAuth2',
    "djoser.auth_backends.LoginFieldBackend",

    'django.contrib.auth.backends.ModelBackend',
]


def _read_key_from_env(value_var: str, path_var: str) -> str | None:
    key_value = os.getenv(value_var)
    if key_value:
        return key_value.replace("\\n", "\n")

    key_path = os.getenv(path_var)
    if not key_path:
        return None

    try:
        with open(key_path, "r", encoding="utf-8") as key_file:
            return key_file.read()
    except OSError as exc:
        raise ImproperlyConfigured(f"Unable to read JWT key file '{key_path}': {exc}") from exc


JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "RS256")
JWT_VERIFYING_KEY = _read_key_from_env("JWT_PUBLIC_KEY", "JWT_PUBLIC_KEY_PATH")

if not JWT_ALGORITHM.upper().startswith(("RS", "ES")):
    raise ImproperlyConfigured(
        "Downstream services must use an asymmetric JWT algorithm (RS*/ES*) to verify identity-service tokens."
    )

if not JWT_VERIFYING_KEY:
    raise ImproperlyConfigured(
        "JWT_PUBLIC_KEY or JWT_PUBLIC_KEY_PATH must be set for downstream JWT verification."
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
AUTH_COOKIE_SECURE=False 
AUTH_COOKIE_HTTP_ONLY=True
AUTH_COOKIE_PATH='/'
AUTH_COOKIE_SAMESITE='None'
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTStatelessUserAuthentication',
    )
}

CORS_ALLOW_ALL_ORIGINS=os.getenv('CORS_ALLOW_ALL_ORIGINS', 'False')=='True'
CORS_ORIGIN_ALLOW_ALL=CORS_ALLOW_ALL_ORIGINS

CORS_ALLOW_CREDENTIALS=os.getenv('CORS_ALLOW_CREDENTIALS', 'True')=='True'

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
]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8001",
    "http://127.0.0.1:3000",
    'http://3.212.68.52:3000',
    "https://intera-inventory.vercel.app",
    "http://3.84.22.207:3000",
    'https://agentic-caller-gvlu.onrender.com',
    'https://intera-inventory.vercel.app',
    'https://dev.product.interaims.com',
    'https://dev.inventory.interaims.com',
    'https://dev.pos.interaims.com',
    'https://interaims.com',
    'https://www.interaims.com',
    ]

# Security / HTTPS.
# For local development (`DEBUG=True` or `LOCAL_SERVER=True`), force these off to avoid
# confusing localhost HTTPS redirects and missing cookies.
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "False") == "True"

SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if os.getenv("SECURE_PROXY_SSL_HEADER_ENABLED", "False") == "True"
    else None
)

SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False") == "True"
CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "False") == "True"

if DEBUG or LOCAL_SERVER:
    SECURE_SSL_REDIRECT = False
    SECURE_PROXY_SSL_HEADER = None
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
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
