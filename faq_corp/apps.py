from django.apps import AppConfig


class FaqCorpConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'faq_corp'

    def ready(self):
        import faq_corp.signals 