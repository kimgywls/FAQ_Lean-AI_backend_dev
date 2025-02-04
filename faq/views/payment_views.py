# payment_views.py
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework import status, viewsets
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import datetime, timedelta, date
import requests 
from ..models import PaymentHistory, Subscription, BillingKey
from ..serializers import BillingKeySerializer, SubscriptionSerializer
from ..utils import get_portone_access_token, verify_payment, get_card_info, schedule_payments_for_user



class BillingKeySaveView(APIView):
    """
    ✅ 카드 등록 후 BillingKey 저장 & 구독 정보 업데이트 및 결제 내역 기록
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        imp_uid = request.data.get('imp_uid')
        customer_uid = request.data.get('customer_uid')
        merchant_uid = request.data.get('merchant_uid')
        plan = request.data.get('plan')
        price = request.data.get('price')

        if not all([imp_uid, customer_uid, merchant_uid, plan, price]):
            return Response({"success": False, "message": "필수 요청 데이터가 누락되었습니다."}, status=400)

        try:
            with transaction.atomic():
                billing_key, _ = BillingKey.objects.update_or_create(
                    user=user,
                    defaults={
                        'customer_uid': customer_uid,
                        'merchant_uid': merchant_uid,
                        'imp_uid': imp_uid,
                        'plan': plan,
                        'amount': price,
                        'is_active': True,
                    }
                )

                user.billing_key = billing_key
                user.save(update_fields=["billing_key"])

                subscription, _ = Subscription.objects.update_or_create(
                    user=user,
                    defaults={
                        "plan": plan,
                        "is_active": True,
                        "next_billing_date": date.today() + timedelta(days=30),
                        "billing_key": billing_key  # ✅ billing_key 추가
                    }
                )

                # ✅ 결제 내역 저장
                payment_history = PaymentHistory.objects.create(
                    user=user,
                    billing_key=billing_key,
                    imp_uid=imp_uid,
                    merchant_uid=merchant_uid,
                    merchant_name=f"{plan} 구독 결제",
                    amount=price,
                    status="결제 완료",  # ✅ 기본적으로 '결제 완료'로 저장
                    scheduled_at=None,  # ✅ 현재 등록 시점의 결제이므로 예약 없음
                )

                # 🚀 새로운 결제 스케줄 등록
                schedule_payments_for_user(user)

                return Response({
                    "success": True,
                    "message": "카드가 성공적으로 등록되었습니다.",
                    "billing_key_data": BillingKeySerializer(billing_key).data,
                    "subscription_data": SubscriptionSerializer(subscription).data,
                    "payment_history_data": {
                        "merchant_name": payment_history.merchant_name,
                        "amount": str(payment_history.amount),
                        "status": payment_history.status,
                        "created_at": payment_history.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    }
                }, status=201)

        except Exception as e:
            return Response({"success": False, "message": f"오류 발생: {str(e)}"}, status=500)




class SubscriptionViewSet(viewsets.ModelViewSet):
    """
    ✅ Subscription(구독) 정보를 관리하는 ViewSet
    """
    serializer_class = SubscriptionSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        try:
            subscription = request.user.subscription
            serializer = self.get_serializer(subscription)
            card_info = get_card_info(request.user)
            return Response(
                { 
                 "subscription": serializer.data,
                 "card_info": card_info
                 }, status=status.HTTP_200_OK)
        except Subscription.DoesNotExist:
            return Response({"error": "구독 정보 없음"}, status=status.HTTP_404_NOT_FOUND)

    def destroy(self, request, *args, **kwargs):
        try:
            subscription = request.user.subscription
            subscription.is_active = False
            subscription.save()
            return Response({"message": "구독이 해지되었습니다."}, status=status.HTTP_200_OK)
        except Subscription.DoesNotExist:
            return Response({"error": "구독 정보 없음"}, status=status.HTTP_404_NOT_FOUND)




class CardInfoView(APIView):
    """
    유저의 결제 정보(카드 정보)를 조회하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        card_info = get_card_info(user)

        return Response({
            "success": True,
            "card_info": card_info
        }, status=200)
    



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


class BillingKeyChangeView(APIView):
    """
    ✅ 유저의 결제 키(BillingKey)를 변경하는 뷰
    카드 변경 시:
    1. 기존 예약 결제 취소
    2. 새로운 BillingKey 업데이트
    3. 새로운 결제 스케줄 생성
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            user = request.user
            new_customer_uid = request.data.get('customer_uid')

            if not new_customer_uid:
                return Response({"success": False, "message": "새 결제 수단 정보가 없습니다."}, status=400)

            access_token = get_portone_access_token()

            # 카드 정보 검증
            verified_payment = verify_payment(new_customer_uid, access_token)
            if not verified_payment:
                return Response({"success": False, "message": "카드 검증에 실패했습니다."}, status=400)

            with transaction.atomic():
                # 기존 BillingKey 가져오기
                billing_key = get_object_or_404(BillingKey, user=user)

                # 1️⃣ 기존 예약된 결제 취소
                cancel_url = "https://api.iamport.kr/subscribe/payments/unschedule"
                requests.post(cancel_url, json={"customer_uid": billing_key.customer_uid}, headers={"Authorization": access_token})

                # 2️⃣ 새로운 BillingKey 업데이트
                billing_key.customer_uid = new_customer_uid
                billing_key.save()

                # 3️⃣ 새로운 결제 스케줄 등록 (리팩토링한 함수 사용)
                schedule_payments_for_user(user)

                print("✅ 카드 변경 및 새로운 결제 스케줄 완료")

            return Response(
                {"success": True, "message": "카드 정보가 성공적으로 변경되고, 새로운 결제 스케줄이 등록되었습니다."},
                status=200
            )

        except ValidationError as ve:
            return Response({"success": False, "message": str(ve)}, status=400)
        except Exception as e:
            return Response({"success": False, "message": f"카드 변경 처리 중 오류가 발생했습니다: {str(e)}"}, status=500)


class CancelPaymentScheduleView(APIView):
    """
    예약된 정기 결제 스케줄을 취소하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        billing_key = user.billing_key

        if not billing_key:
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            access_token = get_portone_access_token()
            scheduled_data = PaymentHistory.objects.filter(user=user, status='scheduled')

            if not scheduled_data.exists():
                return Response({"message": "예약된 결제가 없습니다."}, status=status.HTTP_200_OK)

            requests.post(
                "https://api.iamport.kr/subscribe/payments/unschedule",
                headers={"Authorization": access_token},
                json={"customer_uid": billing_key.customer_uid},
            )

            PaymentHistory.objects.filter(billing_key=billing_key, status='scheduled').update(status="canceled")

            return Response({"message": "정기 결제가 성공적으로 취소되었습니다."}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": f"오류 발생: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class BillingKeyDeleteView(APIView):
    """
    유저의 결제 키(BillingKey)를 삭제하고 정기 결제를 취소하는 뷰
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        billing_key = user.billing_key

        if not billing_key:
            return Response({"error": "결제 키가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # BillingKey 비활성화
                billing_key.deactivate()

                # 유저 정보 초기화
                user.billing_key = None
                user.subscription.is_active = False
                user.subscription.save()
                user.save()

            return Response({"message": "정기 결제가 취소되었습니다."}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": f"오류 발생: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PaymentCompleteView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        imp_uid = request.data.get("imp_uid")

        print(f"🔍 Received imp_uid: {imp_uid}")

        if not imp_uid:
            print("❌ imp_uid가 전달되지 않음")
            return Response({"success": False, "message": "imp_uid가 전달되지 않았습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            access_token = get_portone_access_token()
            print(f"✅ Access Token: {access_token}")

            payment_data = verify_payment(imp_uid, access_token)
            print(f"🔍 Payment Data: {payment_data}")

            # 테스트 모드 여부 확인
            is_test_mode = payment_data.get("pg_provider") == "tosspayments" and payment_data.get("amount") == 0
            print(f"✅ 테스트 모드: {is_test_mode}")

            # 테스트 모드 검증
            if is_test_mode and payment_data.get("status") != "paid":
                print("❌ 테스트 모드에서 결제 상태가 유효하지 않음")
                return Response(
                    {"success": False, "message": "테스트 모드에서 결제 상태가 유효하지 않습니다.", "payment_data": payment_data},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 실제 결제 검증
            if not is_test_mode and not payment_data.get("success"):
                print("❌ 결제 검증 실패")
                return Response(
                    {"success": False, "message": "결제 검증에 실패했습니다.", "payment_data": payment_data},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # plan 결정
            merchant_uid = payment_data.get("merchant_uid", "")
            if "BASIC" in merchant_uid:
                plan = "BASIC"
            elif "ENTERPRISE" in merchant_uid:
                plan = "ENTERPRISE"
            else:
                plan = "UNKNOWN"

            print(f"✅ Plan: {plan}, Merchant UID: {merchant_uid}")

            # customer_uid 로깅
            customer_uid = payment_data.get("customer_uid", "")
            print(f"✅ Customer UID: {customer_uid}")

            # 결제 내역 저장 전에 데이터 확인
            print("🔍 결제 내역 저장 시작")
            
            # PaymentHistory 모델에 맞게 필드 수정
            payment_history, created = PaymentHistory.objects.get_or_create(
                merchant_uid=merchant_uid,
                defaults={
                    'user': user,
                    'imp_uid': imp_uid,
                    'merchant_name': payment_data.get("name"),
                    'amount': payment_data["amount"],
                    'status': payment_data["status"],
                    'created_at': datetime.now()
                }
            )
            
            if not created:
                # 이미 존재하는 결제 내역이면 상태만 업데이트
                payment_history.status = payment_data["status"]
                payment_history.save()
                print("✅ 기존 결제 내역 업데이트 완료")
            else:
                print("✅ 새로운 결제 내역 저장 완료")

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
                        "user_id": user.user_id,
                    },
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            print(f"❌ 결제 처리 중 오류 발생: {e}")
            return Response({"success": False, "message": f"결제 처리 중 오류가 발생했습니다: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class PaymentChangeCompleteView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        imp_uid = request.data.get("imp_uid")

        print(f"🔍 Received imp_uid for card change: {imp_uid}")

        if not imp_uid:
            print("❌ imp_uid가 전달되지 않음")
            return Response(
                {"success": False, "message": "imp_uid가 전달되지 않았습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 결제 검증
            access_token = get_portone_access_token()
            payment_data = verify_payment(imp_uid, access_token)
            print(f"✅ Payment Data: {payment_data}")

            # 테스트 모드 여부 확인
            is_test_mode = payment_data.get("pg_provider") == "tosspayments" and payment_data.get("amount") == 0
            print(f"✅ 테스트 모드: {is_test_mode}")

            if (is_test_mode and payment_data.get("status") != "paid") or \
               (not is_test_mode and not payment_data.get("success")):
                print("❌ 카드 변경 검증 실패")
                return Response(
                    {"success": False, "message": "카드 변경 검증에 실패했습니다."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            with transaction.atomic():
                # 기존 BillingKey 찾기
                billing_key = BillingKey.objects.filter(user=user, is_active=True).first()
                if not billing_key:
                    raise ValidationError("활성화된 결제 키를 찾을 수 없습니다.")

                print("🔄 기존 빌링키 정보:", billing_key.customer_uid)

                # 예정된 결제 내역 찾기
                scheduled_payments = PaymentHistory.objects.filter(
                    user=user,
                    billing_key=billing_key,
                    status='scheduled'
                )
                print(f"✅ 예정된 결제 내역 수: {scheduled_payments.count()}")

                # 포트원 스케줄 취소
                old_customer_uid = billing_key.customer_uid
                try:
                    cancel_url = "https://api.iamport.kr/subscribe/payments/unschedule"
                    headers = {"Authorization": f"Bearer {access_token}"}
                    data = {
                        "customer_uid": old_customer_uid
                    }
                    cancel_response = requests.post(cancel_url, json=data, headers=headers)
                    print(f"✅ 기존 예약 결제 취소 응답: {cancel_response.json()}")
                except Exception as e:
                    print(f"⚠️ 기존 예약 결제 취소 중 오류 (무시하고 진행): {str(e)}")

                # BillingKey 업데이트
                billing_key.imp_uid = imp_uid
                billing_key.customer_uid = payment_data.get("customer_uid", billing_key.customer_uid)
                billing_key.merchant_uid = payment_data.get("merchant_uid", billing_key.merchant_uid)
                billing_key.save()
                print("✅ BillingKey 업데이트 완료")

                # 카드 변경 이력 저장
                PaymentHistory.objects.create(
                    user=user,
                    billing_key=billing_key,
                    imp_uid=imp_uid,
                    merchant_uid=payment_data["merchant_uid"],
                    merchant_name="카드 정보 변경",
                    amount=0,
                    status="card_changed",
                    created_at=datetime.now()
                )
                print("✅ 카드 변경 이력 저장 완료")

                # 새로운 스케줄 생성
                if scheduled_payments.exists():
                    print("🔄 새로운 결제 스케줄 생성 시작")
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
                            "buyer_tel": user.phone_number
                        }
                        schedules.append(schedule)

                    # 새로운 스케줄 등록
                    schedule_url = "https://api.iamport.kr/subscribe/payments/schedule"
                    schedule_data = {
                        "customer_uid": billing_key.customer_uid,
                        "schedules": schedules
                    }
                    
                    schedule_response = requests.post(
                        schedule_url, 
                        json=schedule_data,
                        headers=headers
                    )
                    schedule_result = schedule_response.json()
                    
                    if schedule_result.get("code") == 0:
                        print("✅ 새로운 스케줄 등록 성공")
                        # 예약된 결제 내역 업데이트
                        for i, new_schedule in enumerate(schedule_result.get("response", [])):
                            scheduled_payments[i].merchant_uid = new_schedule.get("merchant_uid")
                            scheduled_payments[i].billing_key = billing_key
                            scheduled_payments[i].save()
                    else:
                        raise ValidationError(f"새로운 스케줄 등록 실패: {schedule_result.get('message')}")

                print("✅ 모든 처리 완료")

            return Response(
                {
                    "success": True,
                    "message": "카드가 성공적으로 변경되었습니다.",
                },
                status=status.HTTP_200_OK
            )

        except ValidationError as ve:
            print(f"❌ Validation error: {str(ve)}")
            return Response(
                {"success": False, "message": str(ve)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            print(f"❌ 카드 변경 처리 중 오류 발생: {str(e)}")
            return Response(
                {"success": False, "message": f"카드 변경 처리 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


            
class PaymentWebhookView(APIView):
    """
    ✅ 포트원의 웹훅을 처리하는 뷰
    """
    def post(self, request):
        try:
            imp_uid = request.data.get('imp_uid')
            merchant_uid = request.data.get('merchant_uid')
            status_code = request.data.get('status')

            if not all([imp_uid, merchant_uid, status_code]):
                return Response({"success": False, "message": "필수 데이터 누락"}, status=400)

            payment_history = get_object_or_404(PaymentHistory, merchant_uid=merchant_uid)

            access_token = get_portone_access_token()
            verified_payment = verify_payment(imp_uid, access_token)

            if not verified_payment:
                return Response({"success": False, "message": "결제 검증 실패"}, status=400)

            payment_history.status = status_code
            payment_history.save()

            if status_code == 'paid':
                billing_key = payment_history.billing_key
                billing_key.subscription_cycle += 1
                billing_key.save()

            return Response({"success": True, "message": "결제 상태 업데이트 완료"}, status=200)

        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=500)



