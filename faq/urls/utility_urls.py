# utility_urls.py
from django.urls import path
from ..views import (
    GenerateQrCodeView, 
    QrCodeImageView,
    RequestServiceView, 
    StatisticsView,
    CustomerUIDView, 
    BillingKeyRegisterView,
    BillingKeyChangeView,
    PaymentHistoryView
)

urlpatterns = [
    path('generate-qr-code/', GenerateQrCodeView.as_view(), name='generate_qr_code'),
    path('qrCodeImage/', QrCodeImageView.as_view(), name='qr_code_image'),
    path('request-service/', RequestServiceView.as_view(), name='request_data'),
    path('statistics/', StatisticsView.as_view(), name='statistics'),
    path('customer-uid/', CustomerUIDView.as_view(), name='customer_uid'),
    path('billing-key-register/', BillingKeyRegisterView.as_view(), name='billing_key_register'),
    path('billing-key-change/', BillingKeyChangeView.as_view(), name='billing_key_change'),
    path('payment-history/', PaymentHistoryView.as_view(), name='payment_history'),
]
