from django.apps import AppConfig


class ContentTypeLinkingModelsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'content_type_linking_models'



class ContentTypeLinkingModelsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mainapps.content_type_linking_models'
    def ready(self):
        import mainapps.content_type_linking_models.signals