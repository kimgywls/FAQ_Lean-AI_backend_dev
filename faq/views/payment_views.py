from django.conf import settings
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import timedelta
from django.utils.timezone import now
import requests
from ..models import PaymentHistory, SubscriptionPlan, BillingKey

# 포트원 API 토큰 발급
def get_portone_access_token():
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
    
    
# 포트원 결제 검증
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


class PaymentHistoryView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_payment_history(self, access_token, user):
        """
        포트원에서 결제 내역을 가져옵니다.
        """
        url = "https://api.iamport.kr/payments/status/all"
        headers = {"Authorization": f"Bearer {access_token}"}

        # 사용자 정보에 기반한 조건 추가 (필요 시)
        params = {
            "customer_uid": f"customer_{user.id}*",  # 사용자와 연관된 customer_uid 패턴
        }

        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            return response.json()['response']['list']
        return None

    def get(self, request):
        try:
            user = request.user
            # 포트원 API 토큰 가져오기
            access_token = get_portone_access_token()

            if not access_token:
                return Response({"success": False, "message": "포트원 인증 실패"}, status=401)

            # 포트원 결제 내역 가져오기
            payment_data = self.get_payment_history(access_token, user)

            if not payment_data:
                return Response({"success": False, "message": "결제 내역이 없습니다."}, status=404)

            # 결제 내역 저장
            for payment in payment_data:
                PaymentHistory.objects.update_or_create(
                    imp_uid=payment['imp_uid'],
                    defaults={
                        'user': user,
                        'billing_key': BillingKey.objects.filter(user=user).first(),
                        'amount': payment['amount'],
                        'status': payment['status'],
                        'created_at': payment['paid_at'],
                    }
                )

            return Response({"success": True, "message": "결제 내역이 저장되었습니다."}, status=200)

        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=500)
        


class CardInfoView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        customer_uid = user.billing_key  # 유저의 billing_key 사용

        if not customer_uid:
            return Response(
                {"error": "등록된 billing_key가 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # 포트원 토큰 가져오기
            access_token = get_portone_access_token()

            # billing_key에 연결된 카드 정보 조회
            card_response = requests.get(
                f"https://api.iamport.kr/subscribe/customers/{customer_uid}",
                headers={"Authorization": access_token},
            )
            card_response.raise_for_status()
            card_info = card_response.json()["response"]

            return Response(card_info, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            return Response(
                {"error": f"카드 정보를 가져오는 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


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
                {"error": "필수 요청 데이터가 누락되었습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            access_token = get_portone_access_token()
            # 포트원 결제 검증
            verified_payment = self.verify_payment(imp_uid, access_token)
            if not verified_payment or verified_payment['amount'] != user.get_subscription_price():
                return Response(
                    {"error": "결제 검증에 실패했습니다."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # BillingKey 생성 또는 업데이트
            billing_key, created = BillingKey.objects.update_or_create(
                user=user,
                defaults={
                    'customer_uid': customer_uid,
                    'imp_uid': imp_uid,
                    'plan': plan,
                    'amount': verified_payment['amount'],  # 포트원에서 검증된 금액 사용
                }
            )

            user.subscription_plan = plan
            user.save()

            return Response({"success": True, "message": "결제 정보가 저장되었습니다."}, status=201)

        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=500)




class BillingKeyChangeView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            user = request.user
            new_customer_uid = request.data.get('customer_uid')

            if not new_customer_uid:
                return Response({"success": False, "message": "필수 정보가 누락되었습니다."}, status=400)

            # BillingKey 업데이트
            billing_key, created = BillingKey.objects.update_or_create(
                user=user,
                defaults={'customer_uid': new_customer_uid}
            )

            # 로그 생성 (선택 사항)
            print(f"User {user.id} updated their billing key to {new_customer_uid}")

            return Response({"success": True, "message": "결제 수단이 성공적으로 변경되었습니다."}, status=200)

        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=500)



class CancelPaymentScheduleView(APIView):
    """
    결제 예약 취소를 처리하는 클래스
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        customer_uid = user.billing_key

        if not customer_uid:
            print("Debug: 사용자 결제 키가 없습니다.")
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # 포트원 토큰 가져오기
            access_token = get_portone_access_token()
            print(f"Debug: 발급된 access_token - {access_token}")

            # 예약 내역 확인
            scheduled_response = requests.get(
                f"https://api.iamport.kr/subscribe/payments/schedule/{customer_uid}",
                headers={"Authorization": access_token},
            )
            scheduled_data = scheduled_response.json()
            print(f"Debug: PortOne 예약 내역 조회 - {scheduled_data}")

            # 예약 내역이 없는 경우 처리
            if not scheduled_data["response"]:
                print("Debug: 예약된 결제가 존재하지 않습니다.")
                PaymentHistory.objects.filter(
                    customer_uid=customer_uid, status="scheduled"
                ).update(status="error_sync")

                return Response(
                    {"error": "예약된 결제가 존재하지 않습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 예약 결제 취소 요청
            cancel_schedule_response = requests.post(
                f"https://api.iamport.kr/subscribe/payments/unschedule",
                headers={"Authorization": access_token},
                json={
                    "customer_uid": customer_uid,
                },
            )
            print("Debug: 예약 결제 취소 요청 응답 코드 -", cancel_schedule_response.status_code)
            cancel_schedule_response.raise_for_status()

            cancel_data = cancel_schedule_response.json()
            print(f"Debug: 예약 결제 취소 응답 데이터 - {cancel_data}")

            if cancel_data["code"] != 0:
                print(f"Debug: PortOne API 오류 - {cancel_data['message']}")
                return Response(
                    {"error": cancel_data["message"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # PaymentHistory 업데이트
            PaymentHistory.objects.filter(customer_uid=customer_uid).update(
                status="canceled",
            )
            print("Debug: PaymentHistory에 'canceled' 상태로 데이터 업데이트 완료")

            return Response({"message": "정기 결제가 성공적으로 취소되었습니다."}, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            print(f"Debug: 포트원 API 통신 중 오류 발생 - {str(e)}")
            return Response(
                {"error": f"포트원 API 통신 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            print(f"Debug: 알 수 없는 오류 발생 - {str(e)}")
            return Response(
                {"error": f"알 수 없는 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BillingKeyDeleteView(APIView):
    """
    빌링키 삭제를 처리하는 클래스
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        customer_uid = user.billing_key

        if not customer_uid:
            print("Debug: 사용자 결제 키가 없습니다.")
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # 사용자 결제 키 삭제
            user.billing_key = None
            user.save()
            print("Debug: 사용자 결제 키 삭제 완료")

            return Response({"message": "사용자의 빌링키가 성공적으로 삭제되었습니다."}, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"Debug: 알 수 없는 오류 발생 - {str(e)}")
            return Response(
                {"error": f"알 수 없는 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


