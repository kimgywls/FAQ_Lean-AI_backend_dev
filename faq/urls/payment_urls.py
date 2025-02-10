from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ..views.payment_views import SubscriptionViewSet
from ..views import (
    PaymentHistoryView,
    PaymentCompleteMobileView,
    PaymentChangeCompleteMobileView,
    PaymentWebhookView
)

# ✅ Router 생성 및 Subscription ViewSet 등록
router = DefaultRouter()
router.register(r'subscription', SubscriptionViewSet, basename='subscription')

urlpatterns = [
    # ✅ 기존 개별 API 엔드포인트들
    path('payment-history/', PaymentHistoryView.as_view(), name='payment_history'),
    path('payment-webhook/', PaymentWebhookView.as_view(), name='payment_webhook'),
    path('payment-complete/', PaymentCompleteMobileView.as_view(), name='payment_complete'),
    path('payment-change-complete/', PaymentChangeCompleteMobileView.as_view(), name='payment_change_complete'),

    # ✅ ViewSet 기반 라우트 추가
    path('', include(router.urls)),  # 이 줄을 추가하여 ViewSet의 엔드포인트들을 포함
]

'''
GET	    /api/subscription/{id}/	    구독 정보 조회
POST	/api/subscription/	        구독 신청/갱신
DELETE	/api/subscription/{id}/	    구독 해지

'''