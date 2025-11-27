from django.apps import AppConfig


class MysiteConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mysite'

    def ready(self):
        """Import signals when app is ready"""
        import mysite.signals

