import os
import sys
import django
import requests
from django.db import transaction
from django.utils import timezone

# ✅ Django 프로젝트의 루트 디렉토리 경로 추가
sys.path.append("/home/hjkim0213/dev/FAQ_Lean-AI_backend_dev")
sys.path.append("/home/hjkim0213/dev/FAQ_Lean-AI_backend_dev/faq_backend")

# ✅ Django 환경 설정 (절대경로 지정)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "faq_backend.settings")

# ✅ Django 초기화
django.setup() 

from faq.utils import get_portone_access_token
from faq.models import BillingKey, Subscription, PaymentHistory

 
def deactivate_expired_billing_keys():
    today = timezone.now().date()
    expired_keys = BillingKey.objects.filter(deactivation_date__lte=today, is_active=True)

    if not expired_keys.exists():
        print("✅ 비활성화할 BillingKey가 없습니다.")
        return

    access_token = get_portone_access_token()

    with transaction.atomic():
        for key in expired_keys:
            cancel_url = "https://api.iamport.kr/subscribe/payments/unschedule"
            cancel_response = requests.post(
                cancel_url,
                json={"customer_uid": key.customer_uid},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            print(f"🛑 포트원 예약 결제 취소 응답: {cancel_response.json()}")

            canceled_payments = PaymentHistory.objects.filter(
                user=key.user,
                billing_key=key,
                status="scheduled"
            ).update(status="canceled")

            print(f"🛑 {canceled_payments}개의 예약된 결제 취소 완료")

            # BillingKey 비활성화
            key.is_active = False
            key.deactivation_date = None  # deactivation_date 초기화
            key.save()
            print(f"✅ BillingKey {key.customer_uid} 비활성화 완료.")
            
            # 사용자의 billing_key 필드를 NULL로 설정
            user = key.user
            if user.billing_key == key:
                user.billing_key = None
                user.save(update_fields=["billing_key"])
                print(f"✅ {user.username}의 billing_key 필드가 NULL로 변경되었습니다.")

            # 구독 정보 비활성화
            subscription = Subscription.objects.filter(user=key.user, is_active=True).first()
            if subscription:
                subscription.is_active = False
                subscription.save()
                print(f"✅ {key.user.username}의 구독이 비활성화되었습니다.")


if __name__ == "__main__":
    deactivate_expired_billing_keys()
