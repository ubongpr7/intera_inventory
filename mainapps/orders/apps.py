from django.apps import AppConfig



class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mainapps.orders'
    def ready(self):
        import mainapps.orders.signals