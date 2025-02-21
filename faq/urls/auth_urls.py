# auth_urls.py
from django.urls import path
from ..views import (
    SignupView, LoginView, UsernameCheckView,
    SendVerificationCodeView, VerifyCodeView,
    PasswordResetView, DeactivateAccountView,
    OAuthLoginAPIView, SocialSignupView, OAuthJWTTokenView

)

urlpatterns = [
    path('signup/', SignupView.as_view(), name='signup'),
    path('login/', LoginView.as_view(), name='login'),
    path('check-username/', UsernameCheckView.as_view(), name='check_username'),
    path('send-code/', SendVerificationCodeView.as_view(), name='send_code'),
    path('verify-code/', VerifyCodeView.as_view(), name='verify_code'),        
    path('reset-password/', PasswordResetView.as_view(), name='reset_password'),
    path('deactivate-account/', DeactivateAccountView.as_view(), name='deactivate_account'),
    path("oauth-token/", OAuthLoginAPIView.as_view(), name="oauth_token"),
    path("social-signup/", SocialSignupView.as_view(), name="social_signup"),
    path("oauth-jwt-token/", OAuthJWTTokenView.as_view(), name="oauth_jwt_token"),    
    
]