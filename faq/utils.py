# utils.py
from slack_sdk.webhook import WebhookClient
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

