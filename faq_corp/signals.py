from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now
from .models import Corp_User, Corp_ServiceRequest
from .utils import send_slack_notification  # Slack 알림 함수 import
import logging

logger = logging.getLogger('faq')

@receiver(post_save, sender=Corp_User)
def send_user_creation_notification(sender, instance, created, **kwargs):
    if created:
        message = f"corp - 새로운 사용자 {instance.username}가 가입했습니다!"
        send_slack_notification(message)

@receiver(post_save, sender=Corp_ServiceRequest)
def send_ServiceRequest_notification(sender, instance, created, **kwargs):
    if created:
        message = (
            f"🔔 *corp - 새로운 서비스 요청 알림!*\n"
            f"- *사용자*: {instance.user.username}\n"
            f"- *요청 제목*: {instance.title}\n"
            f"- *등록 시간*: {now().strftime('%Y-%m-%d %H:%M')}\n"
        )
        send_slack_notification(message)



