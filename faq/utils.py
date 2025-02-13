# utils.py
import requests, logging
from django.conf import settings
from django.utils import timezone
from slack_sdk.webhook import WebhookClient
from dateutil.relativedelta import relativedelta
from rest_framework.exceptions import ValidationError
from .models import BillingKey, PaymentHistory

# 로깅 설정
logger = logging.getLogger("faq")


def send_slack_notification(message):
    """
    Slack 채널로 메시지를 전송하는 함수.

    :param message: 전송할 메시지
    """
    from django.conf import settings  # settings에서 SLACK_WEBHOOK_URL 가져오기

    slack_webhook_url = getattr(settings, "SLACK_WEBHOOK_URL", None)
    if not slack_webhook_url:
        logger.error("SLACK_WEBHOOK_URL이 설정되지 않았습니다.")
        return

    try:
        webhook = WebhookClient(slack_webhook_url)
        response = webhook.send(text=message)
        if response.status_code != 200:
            logger.error(
                f"Slack 메시지 전송 실패: {response.status_code}, {response.body}"
            )
    except Exception as e:
        logger.error(f"Slack 메시지 전송 중 오류 발생: {str(e)}")


def get_portone_access_token():
    """
    포트원 API 토큰 발급 요청 함수.

    :return: 액세스 토큰 문자열
    :raises: 요청 실패 시 예외 발생
    """
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
    포트원 결제 검증 함수.

    :param imp_uid: 포트원의 결제 고유 ID
    :param access_token: 포트원 API 액세스 토큰
    :return: 결제 정보(JSON) 또는 None
    """
    url = f"https://api.iamport.kr/payments/{imp_uid}"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    print(f"🔍 요청 URL: {url}")
    print(f"🔍 응답 코드: {response.status_code}")
    print(f"🔍 응답 본문: {response.json()}")

    if response.status_code == 200:
        return response.json().get("response")

    return None


def format_card_number(raw_card_number):
    """
    카드 번호를 4자리 단위로 '-'를 추가하고 앞 4자리만 표시, 나머지는 '*'로 마스킹.

    :param raw_card_number: 원본 카드 번호
    :return: 마스킹된 카드 번호 문자열
    """
    if not raw_card_number or len(raw_card_number) < 4:
        return "카드 정보 없음"

    # 숫자만 필터링
    digits_only = [char if char.isdigit() else "*" for char in raw_card_number]

    # 앞 4자리 노출, 나머지는 '*'
    masked_digits = digits_only[:4] + [
        "*" if char.isdigit() else char for char in digits_only[4:]
    ]

    # 4자리마다 '-' 추가하여 포맷팅
    formatted_card_number = "-".join(
        ["".join(masked_digits[i : i + 4]) for i in range(0, len(masked_digits), 4)]
    )

    return formatted_card_number


def get_card_info(user):
    """
    사용자의 BillingKey를 기반으로 포트원에서 카드 정보를 조회하는 함수.

    :param user: 유저 객체
    :return: 카드 정보 딕셔너리 (card_name, card_number)
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

        # 카드 번호 마스킹 처리
        raw_card_number = card_data.get("card_number", "카드 정보 없음")
        formatted_card_number = format_card_number(raw_card_number)

        return {
            "card_name": card_data.get("card_name", "Unknown Bank"),
            "card_number": formatted_card_number,
        }

    except requests.exceptions.RequestException as e:
        return {"card_name": "Unknown Bank", "card_number": "카드 정보 조회 실패"}
    
    
# 예약 결제 test
def schedule_payments_for_user(user):
    """
    사용자의 정기 결제를 매일 자동으로 예약하는 함수.
    """

    billing_key = BillingKey.objects.filter(user=user, is_active=True).first()
    if not billing_key:
        raise ValidationError("활성화된 BillingKey가 없습니다.")

    access_token = get_portone_access_token()

    last_scheduled_payment = (
        PaymentHistory.objects.filter(user=user, status="scheduled")
        .order_by("-scheduled_at")
        .first()
    )

    if last_scheduled_payment:
        start_date = last_scheduled_payment.scheduled_at + relativedelta(months=1)
    else:
        start_date = timezone.now() + relativedelta(months=1)

    schedules = []

    for i in range(12):  # 1년 동안 예약
        schedule_date = start_date + relativedelta(months=i)
        merchant_uid = f"{billing_key.merchant_uid}_{i+2}"

        PaymentHistory.objects.create(
            user=user,
            billing_key=billing_key,
            imp_uid=f"scheduled_{merchant_uid}",
            merchant_uid=merchant_uid,
            merchant_name=f"{billing_key.plan} 구독 결제 (예약)",
            amount=billing_key.amount,
            status="scheduled",
            scheduled_at=schedule_date,
            created_at=timezone.now(),
        )

        schedule = {
            "merchant_uid": merchant_uid,
            "schedule_at": int(schedule_date.timestamp()),
            "amount": float(billing_key.amount),
            "name": f"{billing_key.plan} 구독 결제 (예약)",
            "buyer_email": user.email,
            "buyer_name": user.name,
            "buyer_tel": user.phone,
        }
        schedules.append(schedule)

    schedule_url = "https://api.iamport.kr/subscribe/payments/schedule"
    schedule_data = {"customer_uid": billing_key.customer_uid, "schedules": schedules}

    schedule_response = requests.post(
        schedule_url, json=schedule_data, headers={"Authorization": access_token}
    )
    schedule_result = schedule_response.json()
    #print(f"📥 스케줄 응답 데이터: {schedule_result}")

    if schedule_result.get("code") != 0:
        raise ValidationError(f"스케줄 등록 실패: {schedule_result.get('message')}")

