import logging

logger = logging.getLogger('faq')

class FAQPublicRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'faq_public':
            return 'faq_public_db'
        elif model._meta.app_label == 'faq_corp':
            return 'faq_corp_db'
        return 'default'

    def db_for_write(self, model, **hints):
        if model._meta.app_label == 'faq_public':
            return 'faq_public_db'
        elif model._meta.app_label == 'faq_corp':
            return 'faq_corp_db'
        return 'default'

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._meta.app_label == 'faq_public' or obj2._meta.app_label == 'faq_public':
            return True
        elif obj1._meta.app_label == 'faq_corp' or obj2._meta.app_label == 'faq_corp':
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'faq_public':
            return db == 'faq_public_db'
        elif app_label == 'faq_corp':
            return db == 'faq_corp_db'
        return db == 'default'
