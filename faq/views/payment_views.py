# payment_views.py
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import datetime
from dateutil.relativedelta import relativedelta
import requests 
from ..models import PaymentHistory, SubscriptionPlan, BillingKey
from ..serializers import BillingKeySerializer
from ..utils import get_portone_access_token, verify_payment


class CardInfoView(APIView):
    """
    유저의 결제 정보(카드 정보)를 조회하는 뷰
    """
    authentication_classes = [JWTAuthentication]  # JWT 인증 사용
    permission_classes = [IsAuthenticated]  # 인증된 사용자만 접근 가능

    def get(self, request):
        user = request.user  # 현재 로그인한 사용자

        # BillingKey(결제 키) 조회
        billing_key = BillingKey.objects.filter(user=user, is_active=True).first()
        if not billing_key:
            # 결제 정보가 없는 경우
            return Response(
                {
                    "message": "결제 정보가 없습니다. 카드를 등록해주세요.",
                    "card_info": None,
                },
                status=status.HTTP_200_OK
            )

        try:
            # 포트원에서 카드 정보 조회
            access_token = get_portone_access_token()
            card_response = requests.get(
                f"https://api.iamport.kr/subscribe/customers/{billing_key.customer_uid}",
                headers={"Authorization": access_token},
            )
            card_response.raise_for_status()  # 에러 발생 시 예외 처리
            card_info = card_response.json()
            return Response(card_info, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            return Response(
                {"error": f"카드 정보를 가져오는 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentHistoryView(APIView):
    """
    유저의 결제 내역을 조회하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user  # 현재 로그인한 사용자

        # 유저의 결제 내역 조회
        payment_histories = PaymentHistory.objects.filter(user=user).order_by('-created_at')
        if not payment_histories.exists():
            # 결제 내역이 없는 경우
            return Response(
                {"success": True, "message": "결제 내역이 없습니다.", "payment_data": []}, 
                status=status.HTTP_200_OK
            )

        # 결제 내역 직렬화
        data = [
            {
                "merchant_uid": payment.merchant_uid,  # 결제 주문 번호
                "merchant_name": payment.merchant_name,  # 결제 주문 이름
                "amount": str(payment.amount),  # 결제 금액
                "status": payment.status,  # 결제 상태
                "created_at": payment.created_at,  # 생성 일자
                "scheduled_at": payment.scheduled_at,  # 스케줄된 결제 일자
            }
            for payment in payment_histories
        ]

        return Response({"success": True, "payment_data": data}, status=200)
    

class BillingKeySaveView(APIView):
    """
    결제 키(BillingKey)를 저장하고, 첫 결제를 처리하며, 정기 결제를 스케줄링하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        imp_uid = request.data.get('imp_uid')  # 포트원의 결제 고유 ID
        customer_uid = request.data.get('customer_uid')  # 고객 고유 ID
        merchant_uid = request.data.get('merchant_uid')  # 주문 번호
        plan = request.data.get('plan')  # 구독 플랜

        # 필수 데이터 확인
        if not all([imp_uid, customer_uid, merchant_uid, plan]):
            return Response(
                {"success": False, "message": "필수 요청 데이터가 누락되었습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 구독 플랜 유효성 검사
        subscription_plan = SubscriptionPlan.objects.filter(plan_type__iexact=plan).first()
        if not subscription_plan:
            return Response(
                {"success": False, "message": f"'{plan}'에 해당하는 구독 플랜이 존재하지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            access_token = get_portone_access_token()
            verified_payment = verify_payment(imp_uid, access_token)  # 결제 검증

            with transaction.atomic():  # 트랜잭션 시작
                # BillingKey 저장 또는 업데이트
                billing_key, created = BillingKey.objects.update_or_create(
                    user=user,
                    defaults={
                        'customer_uid': customer_uid,
                        'merchant_uid': merchant_uid,
                        'imp_uid': imp_uid,
                        'plan': subscription_plan.plan_type,
                        'amount': subscription_plan.price,
                        'is_active': True,
                    }
                )

                # 첫 결제 내역 추가
                PaymentHistory.objects.create(
                    user=user,
                    billing_key=billing_key,
                    merchant_uid=merchant_uid,
                    merchant_name=f"{subscription_plan.plan_type}_{billing_key.subscription_cycle}",
                    amount=subscription_plan.price,
                    status='paid',  # 첫 결제는 완료 상태
                )

                # 구독 횟수 증가
                billing_key.subscription_cycle += 1
                billing_key.save()

                # 정기 결제 스케줄링
                schedule_response = self.schedule_recurring_payment(billing_key, 12)
                if not schedule_response['success']:
                    raise ValidationError(schedule_response['error'])

                # 다음 달 결제 내역 추가
                if schedule_response.get("response"):
                    first_schedule = schedule_response["response"][0]
                    PaymentHistory.objects.create(
                        user=user,
                        billing_key=billing_key,
                        merchant_uid=first_schedule['merchant_uid'],
                        merchant_name=f"{subscription_plan.plan_type}_{billing_key.subscription_cycle}",
                        amount=first_schedule['amount'],
                        status='scheduled',  # 결제 예정 상태
                        scheduled_at=datetime.fromtimestamp(first_schedule['schedule_at']),
                    )

                # 사용자와 결제 키 업데이트
                user.subscription_plan = subscription_plan
                user.billing_key = billing_key
                user.save()

            billing_key_data = BillingKeySerializer(billing_key).data
            return Response({
                "success": True,
                "billing_key_data": billing_key_data,
                "message": "결제가 성공적으로 완료되었습니다."
            }, status=status.HTTP_201_CREATED)

        except ValidationError as ve:
            return Response({"success": False, "message": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except requests.exceptions.RequestException as re:
            return Response(
                {"success": False, "message": f"외부 결제 API와 통신 중 오류가 발생했습니다: {str(re)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )
        except Exception as e:
            return Response(
                {"success": False, "message": "시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def schedule_recurring_payment(self, billing_key, months):
        """
        정기 결제를 스케줄링하는 함수
        """
        try:
            access_token = get_portone_access_token()
            url = "https://api.iamport.kr/subscribe/payments/schedule"
            headers = {"Authorization": f"Bearer {access_token}"}

            schedules = []
            now = datetime.now()
            for i in range(1, months):  # 1개월 단위로 스케줄링
                schedule_date = now + relativedelta(months=i)
                schedules.append({
                    "imp_uid": billing_key.imp_uid,
                    "merchant_uid": f"scheduled_{billing_key.user.username}_{billing_key.merchant_uid}_{billing_key.subscription_cycle}",
                    "amount": float(billing_key.amount),
                    "schedule_at": int(schedule_date.timestamp()),
                    "name": f"{billing_key.plan} 정기 결제",
                    "buyer_email": billing_key.user.email,
                    "buyer_name": billing_key.user.name,
                    "buyer_tel": billing_key.user.phone,
                })

            data = {"customer_uid": billing_key.customer_uid, "schedules": schedules}
            response = requests.post(url, json=data, headers=headers)
            response_data = response.json()

            if response_data.get("code") == 0:
                return {"success": True, "response": response_data.get("response")}
            else:
                return {"success": False, "error": response_data.get("message", "정기 결제 스케줄링 실패")}

        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"포트원 API 통신 중 오류가 발생했습니다: {str(e)}"}



class BillingKeyChangeView(APIView):
    """
    유저의 결제 키(BillingKey)를 변경하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            user = request.user
            customer_uid = request.data.get('customer_uid')  # 새로운 고객 고유 ID

            if not customer_uid:
                # 고객 고유 ID가 없으면 에러 반환
                return Response({"success": False, "message": "등록된 결제 수단이 없습니다."}, status=400)

            # 포트원 API를 통해 새로운 결제 키 상태를 검증
            access_token = get_portone_access_token()
            verified_payment = verify_payment(customer_uid, access_token)
            if not verified_payment:
                return Response({"success": False, "message": "카드 검증에 실패했습니다."}, status=400)

            return Response({"success": True, "message": "카드 정보가 성공적으로 변경되었습니다."}, status=200)

        except Exception as e:
            # 예외 발생 시 에러 메시지 반환
            return Response({"success": False, "message": str(e)}, status=500)


class CancelPaymentScheduleView(APIView):
    """
    예약된 정기 결제 스케줄을 취소하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        billing_key = user.billing_key  # 유저의 현재 BillingKey

        if not billing_key:
            # 결제 키가 없으면 에러 반환
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            access_token = get_portone_access_token()

            # 예약된 결제 데이터 가져오기
            scheduled_data = PaymentHistory.objects.filter(user=user, status='scheduled').order_by('-created_at')
            if not scheduled_data.exists():
                # 예약된 결제가 없으면 성공 메시지 반환
                return Response(
                    {"message": "예약된 결제가 없으므로 추가 작업 없이 종료합니다."},
                    status=status.HTTP_200_OK,
                )

            # 포트원 API를 사용해 정기 결제 스케줄 취소 요청
            cancel_schedule_response = requests.post(
                f"https://api.iamport.kr/subscribe/payments/unschedule",
                headers={"Authorization": access_token},
                json={
                    "customer_uid": billing_key.customer_uid,  # 고객 고유 ID
                },
            )
            cancel_schedule_response.raise_for_status()  # HTTP 에러 발생 시 예외 처리
            cancel_data = cancel_schedule_response.json()

            if cancel_data["code"] != 0:
                # 포트원에서 오류 반환 시 에러 메시지 반환
                return Response(
                    {"error": cancel_data["message"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 데이터베이스에서 예약된 결제 상태를 'canceled'로 업데이트
            PaymentHistory.objects.filter(billing_key=billing_key, status='scheduled').update(
                status="canceled",
            )

            return Response({"message": "정기 결제가 성공적으로 취소되었습니다."}, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            # 네트워크 오류 처리
            return Response(
                {"error": f"포트원 API 통신 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            # 일반 예외 처리
            return Response(
                {"error": f"알 수 없는 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BillingKeyDeleteView(APIView):
    """
    유저의 결제 키(BillingKey)를 삭제하고 정기 결제를 취소하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        billing_key = user.billing_key  # 유저의 현재 BillingKey

        if not billing_key:
            # 결제 키가 없으면 에러 반환
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # BillingKey를 비활성화
            billing_key.deactivate()

            # 유저와 결제 키의 관계 해제
            user.billing_key = None
            user.subscription_plan = None
            user.save()

            return Response({"message": "정기 결제가 성공적으로 취소되었습니다."}, status=status.HTTP_200_OK)

        except Exception as e:
            # 예외 처리
            return Response(
                {"error": f"알 수 없는 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentWebhookView(APIView):
    """
    포트원의 웹훅을 처리하는 뷰
    """
    def post(self, request):
        try:
            # 웹훅에서 전달된 데이터 파싱
            imp_uid = request.data.get('imp_uid')  # 결제 고유 ID
            merchant_uid = request.data.get('merchant_uid')  # 주문 번호
            status_code = request.data.get('status')  # 결제 상태 ('paid', 'failed', 'canceled')

            # 필수 데이터 확인
            if not all([imp_uid, merchant_uid, status_code]):
                return Response(
                    {"success": False, "message": "필수 데이터가 누락되었습니다."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # PaymentHistory에서 주문 번호로 결제 내역 검색
            payment_history = PaymentHistory.objects.filter(merchant_uid=merchant_uid).first()
            if not payment_history:
                # 결제 내역이 없으면 에러 반환
                return Response(
                    {"success": False, "message": "해당 주문 번호에 대한 결제 내역이 없습니다."},
                    status=status.HTTP_404_NOT_FOUND
                )

            # 포트원 API를 통해 결제 검증
            access_token = get_portone_access_token()
            verified_payment = verify_payment(imp_uid, access_token)

            if not verified_payment:
                # 결제 검증 실패 시 에러 반환
                return Response(
                    {"success": False, "message": "결제 검증에 실패했습니다."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 결제 내역 상태 업데이트
            payment_history.status = status_code
            payment_history.save()

            # 결제가 성공한 경우, BillingKey의 구독 횟수 증가
            if status_code == 'paid':  
                billing_key = payment_history.billing_key
                billing_key.subscription_cycle += 1
                billing_key.save()

            return Response(
                {"success": True, "message": "결제 상태가 성공적으로 업데이트되었습니다."},
                status=status.HTTP_200_OK
            )

        except Exception as e:
            # 일반 예외 처리
            return Response(
                {"success": False, "message": "시스템 오류가 발생했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

