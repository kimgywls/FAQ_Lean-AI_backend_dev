# utils.py
from slack_sdk.webhook import WebhookClient
from .models import BillingKey, PaymentHistory
import requests
from datetime import datetime
from rest_framework.exceptions import ValidationError
from django.conf import settings
import requests
import logging

logger = logging.getLogger('faq')

def send_slack_notification(message):
    """
    Slack 채널로 메시지를 전송하는 함수.
    """
    from django.conf import settings  # settings에서 SLACK_WEBHOOK_URL 가져오기
    slack_webhook_url = getattr(settings, 'SLACK_WEBHOOK_URL', None)
    if not slack_webhook_url:
        logger.error("SLACK_WEBHOOK_URL이 설정되지 않았습니다.")
        return

    try:
        webhook = WebhookClient(slack_webhook_url)
        response = webhook.send(text=message)
        if response.status_code != 200:
            logger.error(f"Slack 메시지 전송 실패: {response.status_code}, {response.body}")
    except Exception as e:
        logger.error(f"Slack 메시지 전송 중 오류 발생: {str(e)}")


def get_portone_access_token():
    '''
    포트원 API 토큰 발급
    '''
    try:
        response = requests.post(
            "https://api.iamport.kr/users/getToken",
            json={
                "imp_key": settings.PORTONE_IMP_KEY,
                "imp_secret": settings.PORTONE_IMP_SECRET,
            },
        )
        response.raise_for_status()
        access_token = response.json()["response"]["access_token"]
        return access_token
    except requests.exceptions.RequestException as e:
        raise Exception(f"포트원 API 토큰 발급 실패: {str(e)}")
    
def verify_payment(imp_uid, access_token):
    """
    포트원 결제 검증
    """
    url = f"https://api.iamport.kr/payments/{imp_uid}"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()['response']
    return None



def format_card_number(raw_card_number):
    """
    카드 번호를 4자리 단위로 '-'를 추가하고 앞 4자리만 표시, 나머지는 '*'로 마스킹
    """
    if not raw_card_number or len(raw_card_number) < 4:
        return "카드 정보 없음"

    # 숫자만 필터링
    digits_only = [char if char.isdigit() else '*' for char in raw_card_number]

    # 앞 4자리 노출, 나머지는 '*'
    masked_digits = digits_only[:4] + ['*' if char.isdigit() else char for char in digits_only[4:]]

    # 4자리마다 '-' 추가
    formatted_card_number = "-".join(
        ["".join(masked_digits[i:i+4]) for i in range(0, len(masked_digits), 4)]
    )

    return formatted_card_number


def get_card_info(user):
    """
    유저의 `customer_uid`를 사용하여 포트원(PortOne)에서 카드 정보를 조회하는 함수
    """
    billing_key = BillingKey.objects.filter(user=user, is_active=True).first()
    if not billing_key:
        return {"card_name": "Unknown Bank", "card_number": "카드 정보 조회 실패"}

    try:
        access_token = get_portone_access_token()
        response = requests.get(
            f"https://api.iamport.kr/subscribe/customers/{billing_key.customer_uid}",
            headers={"Authorization": access_token},
        )
        response.raise_for_status()
        card_data = response.json().get("response", {})

        raw_card_number = card_data.get("card_number", "카드 정보 없음")
        formatted_card_number = format_card_number(raw_card_number)  # 카드 번호 포맷 적용

        return {
            "card_name": card_data.get("card_name", "Unknown Bank"),
            "card_number": formatted_card_number,
        }

    except requests.exceptions.RequestException as e:
        return {"card_name": "Unknown Bank", "card_number": "카드 정보 조회 실패"}


def schedule_payments_for_user(user):
    """
    ✅ 유저의 예약된 결제를 스케줄링하는 함수
    - 기존 PaymentHistory에서 status='scheduled'인 결제 내역을 가져와 새 스케줄 등록
    - 새로운 BillingKey 정보를 사용하여 예약 결제 생성
    - IAMPORT (포트원) API를 사용하여 새로운 결제 스케줄 등록
    """
    billing_key = BillingKey.objects.filter(user=user, is_active=True).first()
    if not billing_key:
        raise ValidationError("활성화된 BillingKey가 없습니다.")

    access_token = get_portone_access_token()

    scheduled_payments = PaymentHistory.objects.filter(user=user, status='scheduled')
    if not scheduled_payments.exists():
        print("🔹 예약된 결제가 없습니다. 스케줄링할 필요 없음.")
        return None

    print(f"🔄 {user.username}님의 새로운 결제 스케줄 생성 시작")

    schedules = []
    current_timestamp = int(datetime.now().timestamp())

    for i, payment in enumerate(scheduled_payments):
        schedule_date = payment.scheduled_at
        merchant_uid = f"scheduled_{user.username}_{current_timestamp}_{i+1}"

        schedule = {
            "merchant_uid": merchant_uid,
            "schedule_at": int(schedule_date.timestamp()),
            "amount": float(payment.amount),
            "name": payment.merchant_name,
            "buyer_email": user.email,
            "buyer_name": user.name,
            "buyer_tel": user.phone,
        }
        schedules.append(schedule)

    # 포트원 API에 새로운 스케줄 등록 요청
    schedule_url = "https://api.iamport.kr/subscribe/payments/schedule"
    schedule_data = {
        "customer_uid": billing_key.customer_uid,
        "schedules": schedules
    }
    
    schedule_response = requests.post(
        schedule_url,
        json=schedule_data,
        headers={"Authorization": access_token}
    )
    schedule_result = schedule_response.json()

    if schedule_result.get("code") == 0:
        print("✅ 새로운 결제 스케줄 등록 성공")

        # 새로운 결제 스케줄 정보를 DB에 업데이트
        for i, new_schedule in enumerate(schedule_result.get("response", [])):
            scheduled_payments[i].merchant_uid = new_schedule.get("merchant_uid")
            scheduled_payments[i].billing_key = billing_key
            scheduled_payments[i].save()

        return schedule_result

    else:
        raise ValidationError(f"새로운 스케줄 등록 실패: {schedule_result.get('message')}")
