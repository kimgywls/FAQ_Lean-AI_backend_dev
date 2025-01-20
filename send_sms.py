import requests
import logging
from django.conf import settings

logger = logging.getLogger('faq')

def send_aligo_sms(receiver, message):
    """
    알리고 SMS 발송 함수
    :param receiver: 수신자 전화번호
    :param message: 전송할 메시지 내용
    :return: API 호출 결과
    """

    data = {
        'key': settings.ALIGO_API_KEY,
        'user_id': settings.ALIGO_USER_ID,
        'sender': settings.ALIGO_SENDER,
        "receiver": receiver,
        "msg": message,
        "testmode_yn": "Y",  # 테스트 모드 활성화 시 "Y", 실제 발송 시 "N"
    }

    try:
        response = requests.post('https://apis.aligo.in/send/', data=data)
        response_data = response.json()
        if response_data.get('result_code') == "1":  # 성공 코드
            logger.info(f"SMS 발송 성공: {response_data}")
            return True
        else:
            logger.error(f"SMS 발송 실패: {response_data}")
            logger.error(f"SMS 발송 실패: {response_data}")
            logger.error(f"API 응답 내용: {response.text}")
            return False
    except Exception as e:
        logger.error(f"SMS 발송 중 오류 발생: {e}")
        return False
