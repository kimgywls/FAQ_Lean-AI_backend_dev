import requests, traceback
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
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


class SubscriptionViewSet(viewsets.ModelViewSet):
    """
    Subscription(구독) 정보를 관리하는 ViewSet
    """
    serializer_class = SubscriptionSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """현재 로그인한 사용자의 구독 정보만 조회"""
        return Subscription.objects.filter(user=self.request.user)
    
    def create (self, request):
        """
        구독 신청 API
        - BillingKey 저장 및 갱신
        - 구독 정보 업데이트
        - 첫 결제 처리 및 이후 결제 스케줄링
        """
        user = request.user
        imp_uid = request.data.get("imp_uid")
        customer_uid = request.data.get("customer_uid")
        merchant_uid = request.data.get("merchant_uid")
        plan = request.data.get("plan")

        #print("🔹 [SubscriptionViewSet.subscribe] 요청 데이터:", request.data)

        # ✅ `plan`에 따른 가격 설정
        plan_prices = {
            "BASIC": 9900,
            "ENTERPRISE": 500000,
        }
        price = plan_prices.get(plan)

        if not all([imp_uid, customer_uid, merchant_uid, plan, price]):
            return Response(
                {"success": False, "message": "필수 요청 데이터가 누락되었습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                # ✅ BillingKey 저장 또는 업데이트
                billing_key, created = BillingKey.objects.update_or_create(
                    user=user,
                    defaults={
                        "customer_uid": customer_uid,
                        "merchant_uid": merchant_uid,
                        "imp_uid": imp_uid,
                        "plan": plan,
                        "amount": price,
                        "is_active": True,
                    },
                )
                billing_key.created_at = timezone.now()
                billing_key.save()

                user.billing_key = billing_key
                user.save(update_fields=["billing_key"])

                # ✅ 12개월 예약 결제 스케줄링 실행
                schedule_payments_for_user(user)

                # ✅ 다음 결제일 설정
                next_billing = (
                    PaymentHistory.objects.filter(
                        user=user, billing_key=billing_key, status="scheduled"
                    )
                    .order_by("scheduled_at")
                    .first()
                )
                next_billing_date = (
                    next_billing.scheduled_at.date()
                    if next_billing
                    else timezone.now().date() + relativedelta(months=1)
                )

                # ✅ 구독 정보 생성 또는 업데이트
                subscription, _ = Subscription.objects.update_or_create(
                    user=user,
                    defaults={
                        "plan": plan,
                        "is_active": True,
                        "next_billing_date": next_billing_date,
                        "billing_key": billing_key,
                    },
                )

                # ✅ 첫 번째 즉시 결제 내역 저장
                payment_history = PaymentHistory.objects.create(
                    user=user,
                    billing_key=billing_key,
                    imp_uid=imp_uid,
                    merchant_uid=merchant_uid,
                    merchant_name=f"{plan} 구독 결제",
                    amount=price,
                    status="paid",
                    scheduled_at=None,
                    created_at=timezone.now(),
                )

                return Response(
                    {
                        "success": True,
                        "message": "구독 신청이 성공적으로 완료되었습니다.",
                        "billing_key_data": BillingKeySerializer(billing_key).data,
                        "subscription_data": SubscriptionSerializer(subscription).data,
                        "payment_history_data": {
                            "merchant_name": payment_history.merchant_name,
                            "amount": str(payment_history.amount),
                            "status": payment_history.status,
                            "created_at": payment_history.created_at.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                        },
                    },
                    status=status.HTTP_201_CREATED,
                )

        except Exception as e:
            print(f"❌ [ERROR] 구독 신청 중 오류 발생: {e}")
            return Response(
                {"success": False, "message": f"오류 발생: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


    def retrieve(self, request, *args, **kwargs):
        """
        사용자의 구독 정보를 조회하는 API
        - 사용자의 구독 정보(Subscription)와 카드 정보(card_info)를 반환
        """
        try:
            subscription = (
                request.user.subscription
            )  # 현재 로그인한 사용자의 구독 정보 가져오기
            serializer = self.get_serializer(
                subscription
            )  # 구독 정보를 Serializer를 통해 변환
            card_info = get_card_info(request.user)  # 사용자의 카드 정보 가져오기
            return Response(
                {
                    "subscription": serializer.data,  # 구독 정보 응답 데이터
                    "card_info": card_info,  # 카드 정보 응답 데이터
                },
                status=status.HTTP_200_OK,
            )
        except (
            Subscription.DoesNotExist
        ):  # 사용자가 구독 정보를 가지고 있지 않은 경우 예외 처리
            return Response(
                {"error": "구독 정보 없음"}, status=status.HTTP_404_NOT_FOUND
            )

    def destroy(self, request, *args, **kwargs):
        """
        ✅ 사용자가 구독을 해지하면 BillingKey의 `deactivation_date`만 설정
        ✅ 실제 예약 결제 취소는 `deactivate_expired_billing_keys()`에서 처리
        """
        try:
            subscription = request.user.subscription

            if not subscription.is_active:
                return Response({"message": "이미 해지된 구독입니다."}, status=400)

            next_billing_date = subscription.next_billing_date
            billing_key = BillingKey.objects.filter(user=request.user, is_active=True).first()

            if billing_key:
                # ✅ BillingKey의 `deactivation_date`만 설정 (실제 취소는 이후 실행)
                billing_key.deactivation_date = next_billing_date
                billing_key.save()
                
            last_available_date = next_billing_date - timedelta(days=1)

            return Response({"message": f"구독이 해지되었습니다. \n {last_available_date}까지 이용 가능합니다."}, status=200)

        except Subscription.DoesNotExist:
            return Response({"error": "구독 정보 없음"}, status=404)
        
    @action(detail=False, methods=['post'])
    def restore(self, request):
        """
        구독을 복구하는 API (해지 취소)
        - BillingKey의 deactivation_date를 None으로 설정
        - 구독 상태 유지 (is_active 변경 없음)
        - 기존 결제 스케줄 유지
        """

        try:
            subscription = request.user.subscription
            billing_key = BillingKey.objects.filter(user=request.user).first()

            # 🔎 BillingKey 및 Subscription 상태 확인
            '''
            print(f"🔹 현재 구독 상태: is_active={subscription.is_active}")
            print(f"🔹 BillingKey 존재 여부: {billing_key is not None}")
            print(f"🔹 BillingKey 해지 예정일: {billing_key.deactivation_date}")
            '''

            if not billing_key:
                print("❌ [ERROR] BillingKey 없음")
                return Response(
                    {"error": "결제 수단 정보를 찾을 수 없습니다."}, 
                    status=status.HTTP_404_NOT_FOUND
                )

            # ✅ 해지 취소 가능 여부 체크 (billing_key.deactivation_date 값 확인)
            if billing_key.deactivation_date is None:
                return Response(
                    {"message": "이미 활성화된 구독이며, 해지 예정이 없습니다."}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            with transaction.atomic():
                # 🔄 BillingKey 복구 (해지 예약 취소)
                billing_key.deactivation_date = None
                billing_key.is_active = True
                billing_key.save()

                # 🔍 다음 결제일 유지 (해지 예약이 취소된 것이므로 변경 X)
                next_billing = (
                    PaymentHistory.objects.filter(
                        user=request.user,
                        billing_key=billing_key,
                        status="scheduled"
                    )
                    .order_by("scheduled_at")
                    .first()
                )

                next_billing_date = (
                    next_billing.scheduled_at.date()
                    if next_billing
                    else subscription.next_billing_date
                )

                # 🔄 구독 정보 업데이트 (is_active 변경 X, 기존 결제일 유지)
                subscription.next_billing_date = next_billing_date
                subscription.save()
                subscription.refresh_from_db()

                return Response({
                    "message": f"구독 해지가 취소되었습니다.",
                    "subscription": SubscriptionSerializer(subscription).data
                }, status=status.HTTP_200_OK)

        except Subscription.DoesNotExist:
            return Response(
                {"error": "구독 정보를 찾을 수 없습니다."}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"구독 해지 취소 중 오류가 발생했습니다: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


    @action(detail=False, methods=['post'])
    def update_billing_key(self, request):
        """
        사용자의 결제 카드(BillingKey)를 변경하는 API
        1. 기존 예약 결제를 취소
        2. 새로운 BillingKey를 저장
        3. 새로운 결제 스케줄을 등록
        """
        try:
            user = request.user  
            imp_uid = request.data.get("imp_uid")
            new_customer_uid = request.data.get("customer_uid")

            #print("🔍 [DEBUG] 요청 데이터:", request.data)

            if not new_customer_uid:
                print("❌ [ERROR] 새 결제 수단 정보가 없습니다.")
                return Response(
                    {"success": False, "message": "새 결제 수단 정보가 없습니다."},
                    status=400,
                )

            access_token = get_portone_access_token()

            # 카드 정보 검증
            verified_payment = verify_payment(imp_uid, access_token)
            if not verified_payment:
                return Response(
                    {"success": False, "message": "카드 검증에 실패했습니다."},
                    status=400,
                )

            # 기존 BillingKey 가져오기
            billing_key = get_object_or_404(BillingKey, user=user)

            success = False  # 트랜잭션 성공 여부를 추적하기 위한 플래그
            
            try:
                with transaction.atomic():
                    # ✅ 새로운 BillingKey 업데이트
                    old_customer_uid = billing_key.customer_uid  # 기존 UID 저장
                    billing_key.customer_uid = new_customer_uid  
                    billing_key.save()
                    
                    success = True  # 모든 작업이 성공적으로 완료됨

            except Exception as e:
                print(f"❌ [ERROR] 결제 취소 또는 스케줄링 중 오류 발생: {str(e)}")
                # 트랜잭션이 자동으로 롤백됨
                
                if not success:  # 트랜잭션이 실패한 경우에만 BillingKey 복구
                    try:
                        # 새로운 트랜잭션에서 BillingKey 복구
                        with transaction.atomic():
                            billing_key.refresh_from_db()  # 최신 데이터로 리프레시
                            billing_key.customer_uid = old_customer_uid
                            billing_key.save()
                    except Exception as recovery_error:
                        print(f"❌ [ERROR] BillingKey 복구 중 오류 발생: {str(recovery_error)}")
                
                raise e  # 원래 예외를 다시 발생시킴

            return Response(
                {
                    "success": True,
                    "message": "카드 정보가 성공적으로 변경되고, 새로운 결제 스케줄이 등록되었습니다.",
                },
                status=200,
            )

        except ValidationError as ve:
            print(f"❌ [ERROR] 데이터 검증 오류 발생: {str(ve)}")
            return Response({"success": False, "message": str(ve)}, status=400)

        except Exception as e:
            print(f"❌ [ERROR] 카드 변경 처리 중 오류 발생: {str(e)}")
            return Response(
                {
                    "success": False,
                    "message": f"카드 변경 처리 중 오류가 발생했습니다: {str(e)}",
                },
                status=500,
            )




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


