# payment_urls.py
from django.urls import path
from ..views import (
    BillingKeySaveView,
    BillingKeyChangeView,
    PaymentHistoryView,
    CardInfoView,
    BillingKeyDeleteView,  
    CancelPaymentScheduleView,
    PaymentWebhookView
)

urlpatterns = [
    path('billing-key-save/', BillingKeySaveView.as_view(), name='billing_key_save'),
    path('billing-key-change/', BillingKeyChangeView.as_view(), name='billing_key_change'),
    path('billing-key-delete/', BillingKeyDeleteView.as_view(), name='billing_key_delete'),
    path('payment-history/', PaymentHistoryView.as_view(), name='payment_history'),
    path('card-info/', CardInfoView.as_view(), name='card_info'),
    path('schedule-cancel-payment/', CancelPaymentScheduleView.as_view(), name='schedule_cancel_payment'),
    path('payment-webhook/', PaymentWebhookView.as_view(), name='payment_webhook'),
]
