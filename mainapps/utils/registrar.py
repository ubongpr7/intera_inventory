from django.contrib import admin

def register_models(registerable_models:list):
    for model in registerable_models:
        admin.site.register(model)