
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = os.getenv('SECRET_KEY')

DEBUG = True

ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    'dev.inventory.destinybuilders.africa',
     'inventory-service:8000'
    'common-service:8000',
    'product-service:8000',
    '16.16.25.132'



]


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
    'tinymce',
    'drf_yasg',
    'djoser',
    'social_django',
    'schema_graph',
    # 'mcp_server',
]
CORE_APPS = [
    'mainapps.company',
    'mainapps.content_type_linking_models',
    'mainapps.inventory',
    'mainapps.orders',
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

"""
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

"""
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
    }
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

EMAIL_BACKEND = 'django_smtp_ssl.SSLEmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 465  
EMAIL_USE_SSL = True
EMAIL_USE_TLS = False
EMAIL_HOST_USER = "ubongpr7@gmail.com"
EMAIL_HOST_PASSWORD = "nmcmiwlgwdrwesef"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
    






AUTHENTICATION_BACKENDS = [
    'social_core.backends.google.GoogleOAuth2',
    'social_core.backends.facebook.FacebookOAuth2',
    "djoser.auth_backends.LoginFieldBackend",

    'django.contrib.auth.backends.ModelBackend',
]
import os
from datetime import timedelta

# DJOSER CONFIGURATION
DJOSER = {
    'PASSWORD_RESET_CONFIRM_URL': 'accounts/password_reset/{uid}/{token}',
    'USERNAME_RESET_CONFIRM_URL': 'username/reset/confirm/{uid}/{token}',
    'ACTIVATION_URL': 'activate/{uid}/{token}',
    'SEND_ACTIVATION_EMAIL': True,
    'USER_CREATE_PASSWORD_RETYPE': True,
    'PASSWORD_RESET_CONFIRM_RETYPE': True,
    'LOGOUT_ON_PASSWORD_CHANGE': True,
    # 'EMAIL_FRONTEND_DOMAIN':'localhost:3000',
    # 'EMAIL_FRONTEND_PROTOCOL':'http',
    'TOKEN_MODEL': 'rest_framework.authtoken.models.Token',  

    'SOCIAL_AUTH_ALLOWED_REDIRECT_URIS': os.getenv('SOCIAL_AUTH_ALLOWED_REDIRECT_URIS', '').split(','),
}


# DJANGO_MCP_SERVER_NAME = "IMS Inventory Service"
# DJANGO_MCP_INSTRUCTIONS = "Provides tools for managing inentory in the IMS"
# DJANGO_MCP_AUTHENTICATION_CLASSES=["rest_framework_simplejwt.authentication.JWTStatelessUserAuthentication"]



SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=3),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=90),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': False,
    'ALGORITHM': 'HS256',
    'VERIFYING_KEY': None,
    'AUDIENCE': None,
    'ISSUER': None,
    'JWK_URL': None,
    'LEEWAY': 0,
    "TOKEN_OBTAIN_SERIALIZER": "mainapps.accounts.api.serializers.MyTokenObtainPairSerializer",

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
        # 'mainapps.accounts.authentication.AccountJWTAuthentication',
        'rest_framework_simplejwt.authentication.JWTStatelessUserAuthentication',
    )
}

CORS_ALLOW_ALL_ORIGINS=True
CORS_ORIGIN_ALLOW_ALL=True

CORS_ALLOW_CREDENTIALS=True
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
    'X-profile-id',  
    'X-user-id'
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
    'https://dev.product.destinybuilders.africa',
    'https://dev.inventory.destinybuilders.africa',
    'https://dev.pos.destinybuilders.africa',
    'https://pos-app-snowy.vercel.app',
]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True

CSRF_COOKIE_SECURE = True
FILE_UPLOAD_TIMEOUT = 3600
DATA_UPLOAD_MAX_MEMORY_SIZE = 2147483648  # 2GB
FILE_UPLOAD_MAX_MEMORY_SIZE = 2147483648  # 2GB

TINYMCE_DEFAULT_CONFIG = {
    'height': 360,
    'width': 800,
    'cleanup_on_startup': True,
    'custom_undo_redo_levels': 20,
    'selector': 'textarea',
    'theme': 'modern',
}

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

# SERVICES
USER_SERVICE_URL=os.getenv('USER_SERVICE_URL')
COMMON_SERVICE_URL=os.getenv('COMMON_SERVICE_URL')
PRODUCT_SERVICE_URL=os.getenv('PRODUCT_SERVICE_URL')

