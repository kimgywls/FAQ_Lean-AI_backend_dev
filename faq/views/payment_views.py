# payment_views.py
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import timedelta, datetime
from dateutil.relativedelta import relativedelta
import requests 
from ..models import PaymentHistory, SubscriptionPlan, BillingKey
from ..serializers import BillingKeySerializer
from ..utils import get_portone_access_token, verify_payment


class CardInfoView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # BillingKey 조회
        billing_key = BillingKey.objects.filter(user=user, is_active=True).first()
        if not billing_key:
            # 카드 등록이 안 된 경우도 정상 응답으로 처리
            return Response(
                {
                    "message": "결제 정보가 없습니다. 카드를 등록해주세요.",
                    "card_info": None,
                },
                status=status.HTTP_200_OK  # 404 대신 200으로 반환
            )

        try:
            # 포트원 토큰 가져오기
            access_token = get_portone_access_token()
            
            # billing_key에 연결된 카드 정보 조회
            card_response = requests.get(
                f"https://api.iamport.kr/subscribe/customers/{billing_key.customer_uid}",
                headers={"Authorization": access_token},
            )
            card_response.raise_for_status()
            card_info = card_response.json()
            return Response(card_info, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            return Response(
                {"error": f"카드 정보를 가져오는 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )



class PaymentHistoryView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        저장된 결제 내역을 조회합니다.
        """
        user = request.user
        payment_histories = PaymentHistory.objects.filter(user=user).order_by('-created_at')

        print(f"payment_histories : {payment_histories}")

        if not payment_histories.exists():
            return Response(
                {"success": True, "message": "결제 내역이 없습니다.", "payment_data": []}, 
                status=status.HTTP_200_OK
            )

        # 결제 내역 직렬화
        data = [
            {
                "merchant_uid": payment.merchant_uid,
                "amount": str(payment.amount),
                "status": payment.status,
                "created_at": payment.created_at,
                "scheduled_at": payment.scheduled_at,
            }
            for payment in payment_histories
        ]
        print(f"data : {data}")

        return Response({"success": True, "payment_data": data}, status=200)
    

class BillingKeySaveView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        imp_uid = request.data.get('imp_uid')
        customer_uid = request.data.get('customer_uid')
        merchant_uid = request.data.get('merchant_uid')
        plan = request.data.get('plan')

        if not all([imp_uid, customer_uid, merchant_uid, plan]):
            return Response(
                {"success": False, "message": "필수 요청 데이터가 누락되었습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )

        subscription_plan = SubscriptionPlan.objects.filter(plan_type__iexact=plan).first()
        if not subscription_plan:
            return Response(
                {"success": False, "message": f"'{plan}'에 해당하는 구독 플랜이 존재하지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            access_token = get_portone_access_token()
            verified_payment = verify_payment(imp_uid, access_token)

            with transaction.atomic():
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

                # 첫 결제 기록 추가
                PaymentHistory.objects.create(
                    user=user,
                    billing_key=billing_key,
                    merchant_uid=merchant_uid,
                    amount=subscription_plan.price,
                    status='paid',  # 첫 결제는 완료 상태
                )
                
                # 결제가 성공한 경우 구독 횟수 증가
                billing_key.subscription_cycle += 1 
                billing_key.save()


                # 정기 결제 스케줄링 및 다음 결제 예정 내역 추가
                schedule_response = self.schedule_recurring_payment(billing_key, 12)
                if not schedule_response['success']:
                    raise ValidationError(schedule_response['error'])

                # 다음 달 결제 예정 내역 추가
                if schedule_response.get("response") and isinstance(schedule_response["response"], list):
                    first_schedule = schedule_response["response"][0]  # 리스트의 첫 번째 항목 사용
                    print(f"first_schedule : {first_schedule}")
                    PaymentHistory.objects.create(
                        user=user,
                        billing_key=billing_key,
                        merchant_uid=first_schedule['merchant_uid'],
                        amount=first_schedule['amount'],
                        status='scheduled',  # 결제 예정 상태
                        scheduled_at=datetime.fromtimestamp(first_schedule['schedule_at']),  # 타임스탬프를 datetime으로 변환
                    )

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
            return Response({
                "success": False,
                "message": f"외부 결제 API와 통신 중 오류가 발생했습니다: {str(re)}"
            }, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            return Response(
                {"success": False, "message": "시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


    def schedule_recurring_payment(self, billing_key, months):
        """
        정기 결제 스케줄을 설정합니다.
        """
        try:
            access_token = get_portone_access_token()
            url = "https://api.iamport.kr/subscribe/payments/schedule"
            headers = {"Authorization": f"Bearer {access_token}"}

            schedules = []
            now = datetime.now()
            for i in range(1, months):
                schedule_date = now + relativedelta(months=i)  # i개월 후의 날짜 계산
                print(f"schedule_date : {schedule_date}")
                schedules.append({
                    "imp_uid": billing_key.imp_uid,
                    "merchant_uid": f"scheduled_{billing_key.user.username}_{billing_key.merchant_uid}_{billing_key.subscription_cycle}",
                    "amount": float(billing_key.amount),
                    "schedule_at": int(schedule_date.timestamp()),  # UNIX 타임스탬프
                    "name": f"{billing_key.plan} 정기 결제",
                    "buyer_email": billing_key.user.email,
                    "buyer_name": billing_key.user.name,
                    "buyer_tel": billing_key.user.phone,
                })

            data = {"customer_uid": billing_key.customer_uid, "schedules": schedules}
            print("스케줄링 데이터 확인:", schedules)  # 디버깅용
            response = requests.post(url, json=data, headers=headers)
            response_data = response.json()

            if response_data.get("code") == 0:
                return {"success": True, "response": response_data.get("response")}
            else:
                return {"success": False, "error": response_data.get("message", "정기 결제 스케줄링 실패")}

        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"포트원 API 통신 중 오류가 발생했습니다: {str(e)}"}



  
class BillingKeyChangeView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            user = request.user
            customer_uid = request.data.get('customer_uid')

            if not customer_uid:
                return Response({"success": False, "message": "등록된 결제 수단이 없습니다."}, status=400)

            # 포트원 API를 통해 카드 등록 상태 검증
            access_token = get_portone_access_token()
            verified_payment = verify_payment(customer_uid, access_token)
            if not verified_payment:
                return Response({"success": False, "message": "카드 검증에 실패했습니다."}, status=400)

            return Response({"success": True, "message": "카드 정보가 성공적으로 변경되었습니다."}, status=200)

        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=500)



class CancelPaymentScheduleView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        billing_key = user.billing_key

        if not billing_key:
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            access_token = get_portone_access_token()

            # 예약된 결제 정보 가져오기
            scheduled_data = PaymentHistory.objects.filter(user=user, status='scheduled').order_by('-created_at')
            print(f"scheduled_data : {scheduled_data}")

            if not scheduled_data.exists():  # 예약된 결제가 없을 경우
                print("예약된 결제가 존재하지 않습니다.")
                return Response(
                    {"message": "예약된 결제가 없으므로 추가 작업 없이 종료합니다."},
                    status=status.HTTP_200_OK,
                )

            # 정기 결제 취소 요청
            cancel_schedule_response = requests.post(
                f"https://api.iamport.kr/subscribe/payments/unschedule",
                headers={"Authorization": access_token},
                json={
                    "customer_uid": billing_key.customer_uid,  # 문자열로 전달
                },
            )
            print(f"cancel_schedule_response : {cancel_schedule_response}")
            cancel_schedule_response.raise_for_status()

            cancel_data = cancel_schedule_response.json()
            print(f"cancel_data : {cancel_data}")
            if cancel_data["code"] != 0:
                return Response(
                    {"error": cancel_data["message"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # PaymentHistory 상태 업데이트
            PaymentHistory.objects.filter(billing_key=billing_key, status='scheduled').update(
                status="canceled",
            )

            return Response({"message": "정기 결제가 성공적으로 취소되었습니다."}, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            print("RequestException:", str(e))
            return Response(
                {"error": f"포트원 API 통신 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            print("Exception:", str(e))
            return Response(
                {"error": f"알 수 없는 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )



class BillingKeyDeleteView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        billing_key = user.billing_key

        if not billing_key:
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # BillingKey 비활성화
            billing_key.deactivate()

            # User의 billing_key 관계 해제
            user.billing_key = None
            user.subscription_plan = None
            user.save()

            return Response({"message": "정기 결제가 성공적으로 취소되었습니다."}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"알 수 없는 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )



class PaymentWebhookView(APIView):
    """
    포트원 웹훅 엔드포인트
    """
    def post(self, request):
        try:
            # 포트원에서 보낸 데이터 파싱
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
                return Response(
                    {"success": False, "message": "해당 주문 번호에 대한 결제 내역이 없습니다."},
                    status=status.HTTP_404_NOT_FOUND
                )

            # 포트원 API를 통해 결제 검증
            access_token = get_portone_access_token()
            verified_payment = verify_payment(imp_uid, access_token)

            if not verified_payment:
                return Response(
                    {"success": False, "message": "결제 검증에 실패했습니다."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 상태 업데이트
            payment_history.status = status_code
            payment_history.save()

             # 결제가 성공한 경우 구독 횟수 증가
            if status_code == 'paid':  
                billing_key = payment_history.billing_key
                billing_key.subscription_cycle += 1
                billing_key.save()

            return Response(
                {"success": True, "message": "결제 상태가 성공적으로 업데이트되었습니다."},
                status=status.HTTP_200_OK
            )

        except Exception as e:
            return Response(
                {"success": False, "message": "시스템 오류가 발생했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        


