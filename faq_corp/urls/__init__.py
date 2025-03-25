# __init__.py
from .auth_urls import urlpatterns as auth_urls
from .corp_urls import urlpatterns as corp_urls
from .complaint_urls import urlpatterns as complaint_urls
from .user_urls import urlpatterns as user_urls
from .utility_urls import urlpatterns as utility_urls

urlpatterns = auth_urls + corp_urls + complaint_urls + user_urls + utility_urls
