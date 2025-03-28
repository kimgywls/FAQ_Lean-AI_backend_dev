import logging
from rest_framework_simplejwt.authentication import JWTAuthentication
from .models import Corp_User

# 로거 설정
logger = logging.getLogger('faq')

class CorpUserJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        #logger.debug("Auth.py - Authenticate method called")
        return super().authenticate(request)

    def get_user(self, validated_token):
        try:
            user_id = validated_token.get("user_id")
            #logger.debug(f"Auth.py - Extracted user_id from token: {user_id}")
            user = Corp_User.objects.get(user_id=user_id)
            #logger.debug(f"Auth.py - Authenticated User: {user}")
            return user
        except Corp_User.DoesNotExist:
            #logger.error(f"Auth.py - No user found with user_id: {user_id}")
            return None