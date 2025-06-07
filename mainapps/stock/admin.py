from django.contrib import admin

from mainapps.utils.registrar import register_models
from .models import registerable_models
register_models(registerable_models)
