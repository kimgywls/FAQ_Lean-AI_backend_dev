import requests, traceback
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from urllib.parse import urlencode 
from rest_framework.views import APIView
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from dateutil.relativedelta import relativedelta
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from ..models import PaymentHistory, Subscription, BillingKey
from ..serializers import (
    BillingKeySerializer,
    SubscriptionSerializer,
    PaymentHistorySerializer,
)
from ..utils import (
    get_portone_access_token,
    verify_payment,
    get_card_info,
    schedule_payments_for_user,
)


KCP_BILLING_URL = "https://stg-spl.kcp.co.kr/gw/enc/v1/payment"


class KcpApprovalAPIView(APIView):
    """
    KCP 승인 처리 API (빌링키 발급)
    """

    def post(self, request):
        print("📌 [DEBUG] KCP 승인 처리 API 호출")

        approval_key = request.data.get("approval_key")
        order_no = request.data.get("order_no")

        if not approval_key or not order_no:
            print("❌ [ERROR] approval_key 또는 order_no 값이 없습니다.")
            return Response({"error": "approval_key 및 order_no 값이 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            "tran_cd": "00300001",  # ✅ KCP 배치키(빌링키) 발급 트랜잭션 코드
            "site_cd": settings.KCP_SITE_CD,
            "approval_key": approval_key,
            "order_no": order_no,
        }

        headers = {
            "Content-Type": "application/json"
        }

        print(f"📌 [DEBUG] KCP 승인 요청 데이터 (JSON): {payload}")

        try:
            response = requests.post(KCP_BILLING_URL, json=payload, headers=headers)
            print(f"📌 [DEBUG] KCP API 응답 코드: {response.status_code}")
            print(f"📌 [DEBUG] KCP API 응답 데이터: {response.text}")

            result = response.json()

            if result.get("res_cd") == "0000":
                print(f"✅ [SUCCESS] 빌링키 발급 완료 - Billing Key: {result.get('billing_key')}")
                return Response({"billing_key": result.get("billing_key")}, status=status.HTTP_200_OK)
            else:
                print(f"❌ [ERROR] 빌링키 발급 실패 - 응답 데이터: {result}")
                return Response({"error": "빌링키 발급 실패", "details": result}, status=status.HTTP_400_BAD_REQUEST)

        except requests.RequestException as e:
            print(f"❌ [ERROR] KCP 요청 실패: {str(e)}")
            return Response({"error": f"KCP 요청 실패: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            print(f"❌ [ERROR] 서버 내부 오류: {str(e)}")
            return Response({"error": f"서버 내부 오류: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class KcpPaymentAPIView(APIView):
    """
    KCP 최초 결제 및 빌링키 발급 API
    """

    def post(self, request):
        print("📌 [DEBUG] KCP 결제 API 요청 시작")

        try:
            site_cd = settings.KCP_TEST_SITE_CD
            tran_cd = "00300001"  # 배치키 요청 코드 (공식 문서 참조)
            kcp_cert_info = request.data.get("kcp_cert_info")
            enc_data = request.data.get("enc_data")
            enc_info = request.data.get("enc_info")

            print(f"📌 [DEBUG] site_cd: {site_cd}")
            print(f"📌 [DEBUG] tran_cd: {tran_cd}")

            if not (kcp_cert_info and enc_data and enc_info):
                print("❌ [ERROR] 인증 데이터가 부족합니다.")
                return Response({"error": "kcp_cert_info, enc_data, enc_info 값이 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

            # ✅ JSON으로 요청할 데이터 생성
            payload = {
                "tran_cd": tran_cd,
                "kcp_cert_info": kcp_cert_info,
                "site_cd": site_cd,
                "enc_data": enc_data,
                "enc_info": enc_info
            }

            headers = {
                "Content-Type": "application/json"
            }

            print(f"📌 [DEBUG] KCP API 요청 데이터 (JSON): {json.dumps(payload, indent=4, ensure_ascii=False)}")

            # ✅ JSON 형식으로 API 요청
            response = requests.post(KCP_BILLING_URL, json=payload, headers=headers)

            print(f"📌 [DEBUG] KCP API 응답 코드: {response.status_code}")
            print(f"📌 [DEBUG] KCP API 응답 데이터: {response.text}")

            try:
                result = response.json()
            except json.JSONDecodeError:
                print("❌ [ERROR] 응답이 JSON 형식이 아님!")
                return Response({"error": "KCP API 응답이 올바르지 않습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            if result.get("res_cd") == "0000":
                print(f"✅ [SUCCESS] 배치키 발급 완료 - Batch Key: {result.get('batch_key')}")
                return Response({"batch_key": result.get("batch_key")}, status=status.HTTP_200_OK)
            else:
                print(f"❌ [ERROR] 배치키 발급 실패 - 응답 데이터: {result}")
                return Response({"error": result.get("res_msg")}, status=status.HTTP_400_BAD_REQUEST)

        except requests.RequestException as e:
            print(f"❌ [ERROR] KCP 요청 실패: {str(e)}")
            return Response({"error": f"결제 요청 실패: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            print(f"❌ [ERROR] 서버 내부 오류: {str(e)}")
            return Response({"error": f"서버 내부 오류: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SubscriptionViewSet(viewsets.ViewSet):
    """
    KCP 정기 결제 API (ViewSet)
    """

    def create(self, request):
        site_cd = settings.KCP_SITE_CD
        order_no = request.data.get("order_no")
        billing_key = request.data.get("billing_key")
        amount = request.data.get("amount")

        if not order_no or not billing_key or not amount:
            return Response({"error": "필수 데이터가 누락되었습니다."}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            "site_cd": site_cd,
            "order_no": order_no,
            "billing_key": billing_key,
            "amount": amount,
            "currency": "KRW",
            "action": "pay"
        }

        try:
            response = requests.post(KCP_BILLING_URL, data=payload)
            result = response.json()

            if result.get("result") == "success":
                return Response({"success": True}, status=status.HTTP_200_OK)
            else:
                return Response({"error": "정기 결제 실패"}, status=status.HTTP_400_BAD_REQUEST)
        
        except requests.RequestException as e:
            return Response({"error": f"정기 결제 요청 실패: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PaymentHistoryView(APIView):
    """
    유저의 결제 내역을 조회하는 뷰
    """

    authentication_classes = [JWTAuthentication]  # JWT 인증을 사용하여 인증 설정
    permission_classes = [IsAuthenticated]  # 인증된 사용자만 접근 가능하도록 설정

    def get(self, request):
        """
        유저의 결제 내역을 조회하는 API
        - '결제완료' 상태의 내역을 최신순으로 조회
        - '결제예정' 상태의 내역 중 가장 가까운 결제 예정 내역을 조회
        - 두 데이터를 합쳐서 반환
        """
        user = request.user  # 현재 로그인한 사용자 정보 가져오기

        # "결제완료" 상태 내역 조회 (최신순 정렬)
        paid_histories = PaymentHistory.objects.filter(
            user=user, status="paid"
        ).order_by("-created_at")

        # "결제예정" 상태 내역 중 가장 가까운 결제 예정 내역 가져오기 (예정 날짜 기준 오름차순 정렬 후 첫 번째 데이터)
        upcoming_payment = (
            PaymentHistory.objects.filter(user=user, status="scheduled")
            .order_by("scheduled_at")
            .first()
        )

        # "결제완료" 내역을 직렬화하여 변환
        serialized_paid_histories = PaymentHistorySerializer(
            paid_histories, many=True
        ).data

        # "결제예정" 내역을 직렬화하여 변환 (데이터가 있을 경우만 처리)
        serialized_upcoming_payment = (
            PaymentHistorySerializer(upcoming_payment).data
            if upcoming_payment
            else None
        )

        # 응답 데이터 구성 (결제완료 내역 리스트에 결제예정 내역 추가)
        response_data = serialized_paid_histories  # 기존 결제 완료 내역 리스트
        if serialized_upcoming_payment:  # 결제 예정 내역이 존재하는 경우 추가
            response_data.append(serialized_upcoming_payment)

        return Response(
            {"success": True, "payment_data": response_data}, status=200
        )  # 응답 반환



class PaymentCompleteMobileView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        imp_uid = request.data.get("imp_uid")

        if not imp_uid:
            return Response(
                {"success": False, "message": "imp_uid가 전달되지 않았습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            access_token = get_portone_access_token()

            payment_data = verify_payment(imp_uid, access_token)

            # 테스트 모드 여부 확인
            is_test_mode = (
                payment_data.get("pg_provider") == "tosspayments"
                and payment_data.get("amount") == 0
            )

            # 테스트 모드 검증
            if is_test_mode and payment_data.get("status") != "paid":
                return Response(
                    {
                        "success": False,
                        "message": "테스트 모드에서 결제 상태가 유효하지 않습니다.",
                        "payment_data": payment_data,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 실제 결제 검증
            if not is_test_mode and not payment_data.get("success"):
                return Response(
                    {
                        "success": False,
                        "message": "결제 검증에 실패했습니다.",
                        "payment_data": payment_data,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # plan 결정
            merchant_uid = payment_data.get("merchant_uid", "")
            if "BASIC" in merchant_uid:
                plan = "BASIC"
                price = 9900
            elif "ENT" in merchant_uid:
                plan = "ENTERPRISE"
                price = 500000

            else:
                plan = "UNKNOWN"
                price = 0

            customer_uid = payment_data.get("customer_uid", "")

            return Response(
                {
                    "success": True,
                    "message": "결제가 성공적으로 완료되었습니다.",
                    "payment_data": {
                        "merchant_uid": merchant_uid,
                        "customer_uid": customer_uid,
                        "imp_uid": payment_data["imp_uid"],
                        "amount": payment_data["amount"],
                        "status": payment_data["status"],
                        "plan": plan,
                        "price": price,
                        "user_id": user.user_id,
                    },
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {
                    "success": False,
                    "message": f"결제 처리 중 오류가 발생했습니다: {str(e)}",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentChangeCompleteMobileView(APIView):
    """
    사용자의 카드 변경 완료를 처리하는 뷰
    - imp_uid를 이용하여 결제 정보를 조회하고 customer_uid를 찾아 프론트로 전달
    - 프론트에서 customer_uid를 받아 billing-key-change API 호출하여 BillingKey 변경
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        imp_uid = request.data.get("imp_uid")  # 프론트에서 전달한 imp_uid

        if not imp_uid:
            return Response(
                {"success": False, "message": "imp_uid가 전달되지 않았습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # 1️⃣ imp_uid로 PortOne API에서 결제 정보 조회
            access_token = get_portone_access_token()
            payment_url = f"https://api.iamport.kr/payments/{imp_uid}"
            headers = {"Authorization": f"Bearer {access_token}"}

            payment_response = requests.get(payment_url, headers=headers).json()

            if payment_response.get("code") != 0:
                return Response(
                    {
                        "success": False,
                        "message": "포트원 결제 정보를 가져오지 못했습니다.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payment_data = payment_response.get("response", {})
            customer_uid = payment_data.get(
                "customer_uid"
            )  # ✅ imp_uid로 customer_uid 가져오기

            if not customer_uid:
                return Response(
                    {"success": False, "message": "customer_uid가 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 2️⃣ 프론트엔드로 customer_uid 반환 (billing-key-change를 별도로 호출하도록)
            return Response(
                {
                    "success": True,
                    "message": "customer_uid 조회 성공",
                    "customer_uid": customer_uid,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {
                    "success": False,
                    "message": f"카드 변경 처리 중 오류가 발생했습니다: {str(e)}",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentWebhookView(APIView):
    """
    포트원의 웹훅을 처리하는 뷰
    """

    def post(self, request):
        try:
            # 요청 데이터 로깅
            #print(f"Webhook 요청 데이터: {request.data}")

            imp_uid = request.data.get("imp_uid")
            merchant_uid = request.data.get("merchant_uid")
            status_code = request.data.get("status")

            if not all([imp_uid, merchant_uid, status_code]):
                return Response(
                    {"success": False, "message": "필수 데이터 누락"}, status=400
                )

            # PaymentHistory 조회
            try:
                payment_history = PaymentHistory.objects.get(merchant_uid=merchant_uid)
            except PaymentHistory.DoesNotExist:
                return Response(
                    {"success": False, "message": "결제 이력을 찾을 수 없음"}, status=404
                )

            # 포트원 결제 검증
            access_token = get_portone_access_token()
            verified_payment = verify_payment(imp_uid, access_token)

            if not verified_payment:
                return Response(
                    {"success": False, "message": "결제 검증 실패"}, status=400
                )

            # ✅ 결제 상태 업데이트
            payment_history.status = status_code
            payment_history.imp_uid = imp_uid  # 실제 imp_uid 업데이트
            payment_history.created_at = timezone.now()  # 실제 created_at 업데이트
            payment_history.save()

            # 결제가 성공한 경우 추가 처리
            if status_code == "paid":
                billing_key = payment_history.billing_key
                billing_key.subscription_cycle += 1
                billing_key.save()

                # ✅ 구독 정보 가져오기
                subscription = Subscription.objects.filter(
                    user=payment_history.user, is_active=True
                ).first()

                if subscription:
                    # ✅ 가장 가까운 scheduled_at을 가져와 `next_billing_date` 설정
                    next_billing = PaymentHistory.objects.filter(
                        user=payment_history.user,
                        billing_key=billing_key,
                        status="scheduled"
                    ).order_by("scheduled_at").first()

                    subscription.next_billing_date = (
                        next_billing.scheduled_at.date() if next_billing
                        else timezone.now().date() + relativedelta(months=1)
                    )

                    subscription.save(update_fields=["next_billing_date"])
                    print(f"✅ 다음 결제일 업데이트 완료: {subscription.next_billing_date}")
                else:
                    print("⚠️ 구독 정보가 없음")

                # ✅ 스케줄이 2개월 이하로 남은 경우 다음 12개월 등록
                remaining_schedules = PaymentHistory.objects.filter(
                    user=payment_history.user,
                    status="scheduled",
                    scheduled_at__gte=timezone.now(),
                ).count()

                if remaining_schedules <= 2:
                    print("⚠️ 남은 스케줄이 2개 이하이므로 새 스케줄 등록을 시작합니다.")
                    schedule_payments_for_user(payment_history.user)

            return Response(
                {"success": True, "message": "결제 상태 업데이트 완료"}, status=200
            )

        except Exception as e:
            # 예외 발생 시 상세한 정보 로깅
            print(f"에러 발생: {str(e)}")
            print(traceback.format_exc())
            return Response({"success": False, "message": "서버 오류 발생"}, status=500)


