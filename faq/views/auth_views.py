# auth_views.py
# 로그인, 회원가입, 비밀번호 재설정, 계정 비활성화
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import transaction
from django.conf import settings
from django.utils.text import slugify
from django.db.utils import IntegrityError
from urllib.parse import quote
from django.utils import timezone
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
import random, logging, os, shutil, requests, re
from send_sms import send_aligo_sms
from ..models import User, Store, ServiceRequest, Menu, Subscription, PaymentHistory
from ..serializers import (
    UserSerializer,
    StoreSerializer,
    UsernameCheckSerializer,
    PasswordCheckSerializer,
)

# 디버깅을 위한 로거 설정
logger = logging.getLogger("faq")


# User Management APIs
# 회원가입 API
class SignupView(APIView):
    def post(self, request):
        # print(request.data)  # 요청 데이터 확인

        user_data = {
            "username": request.data.get("username"),
            "password": request.data.get("password"),
            "name": request.data.get("name"),
            "dob": request.data.get("dob"),
            "phone": request.data.get("phone"),
            "email": request.data.get("email") if request.data.get("email") else None,
            "marketing": request.data.get("marketing"),
        }
        store_data = {
            "store_category": request.data.get("store_category"),
            "store_name": request.data.get("store_name"),
            "store_address": request.data.get("store_address"),
            "slug": slugify(quote(request.data.get("store_name", ""))),
        }

        # print(user_data)

        # print(store_data)

        if (
            Store.objects.filter(store_name=store_data["store_name"]).exists()
            or Store.objects.filter(slug=store_data["slug"]).exists()
        ):
            return Response(
                {
                    "success": False,
                    "message": "이미 존재하는 스토어 이름 또는 슬러그입니다.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                user_serializer = UserSerializer(data=user_data)
                if not user_serializer.is_valid():
                    print(user_serializer.errors)  # 유효성 검사 오류 출력
                    return Response(
                        {
                            "success": False,
                            "message": "회원가입 실패",
                            "errors": user_serializer.errors,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                user = user_serializer.save()

                store_data["user"] = user.user_id
                store_serializer = StoreSerializer(data=store_data)
                if not store_serializer.is_valid():
                    print(store_serializer.errors)  # 유효성 검사 오류 출력
                    return Response(
                        {
                            "success": False,
                            "message": "스토어 생성 실패",
                            "errors": store_serializer.errors,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                store_serializer.save()
                return Response(
                    {"success": True, "message": "회원가입 성공"},
                    status=status.HTTP_201_CREATED,
                )

        except Exception as e:
            print(str(e))  # 예외 메시지 출력
            logger.error(f"회원가입 오류: {str(e)}")
            return Response(
                {"success": False, "message": "서버 오류 발생"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# 로그인 API
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")
        captcha_token = request.data.get("captcha")
        #test_mode = request.data.get("test_mode")

        # CAPTCHA 검증
        captcha_valid, score = self.verify_captcha(captcha_token)
        
        '''
        if test_mode:
            captcha_valid, score = True, 0.2
        else:
            captcha_valid, score = self.verify_captcha(captcha_token)
        '''
        
        if not captcha_valid:
            return Response(
                {"error": "CAPTCHA 검증 실패"}, status=status.HTTP_400_BAD_REQUEST
            )
        
        # reCAPTCHA v3 점수에 따른 액션
        if score < 0.3:  # 0.3 미만이면 로그인 차단
            return Response(
                {"error": "의심스러운 활동이 감지되었습니다.", "login_lock": True},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        if score < 0.5:  # 0.5 미만이면 reCAPTCHA v2 요청
            return Response(
                {"error": "의심스러운 활동이 감지되었습니다.", "require_captcha": True},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            user = User.objects.get(username=username)
            if check_password(password, user.password):
                refresh = RefreshToken.for_user(user)
                store = user.stores.first()

                # 사용자 데이터
                user_data = UserSerializer(user).data

                return Response(
                    {
                        "access": str(refresh.access_token),
                        "store_id": store.store_id if store else None,
                        "user_data": user_data,
                    }
                )

            return Response(
                {"error": "아이디 또는 비밀번호가 잘못되었습니다."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        except User.DoesNotExist:
            return Response(
                {
                    "error": "입력하신 아이디로 가입된 계정이 없습니다.\n회원가입 후 로그인해 주세요."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        except Exception as e:
            logger.error(f"로그인 오류: {str(e)}")
            return Response(
                {"error": "서버 오류 발생"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def verify_captcha(self, token):
        """
        CAPTCHA 검증 로직 (Google reCAPTCHA v3 사용)
        """
        url = "https://www.google.com/recaptcha/api/siteverify"
        data = {"secret": settings.RECAPTCHA_V3_SECRET_KEY, "response": token}
        response = requests.post(url, data=data).json()
        print(f"google capcha response : {response}")
        success = response.get("success", False)
        score = response.get("score", 0)

        # reCAPTCHA 점수 확인 로그 추가
        print(f"[reCAPTCHA] Success: {success}, Score: {score}")

        return success, score


# Other User APIs
# 아이디 중복 검사 API
class UsernameCheckView(APIView):
    def post(self, request):
        serializer = UsernameCheckSerializer(data=request.data)
        if serializer.is_valid():
            username = serializer.validated_data["username"]
            if User.objects.filter(username=username, is_active=True).exists():
                return Response(
                    {
                        "is_duplicate": True,
                        "message": "이미 사용 중인 사용자 아이디입니다.",
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(
                {"is_duplicate": False, "message": "사용 가능한 사용자 아이디입니다."},
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# 비밀번호 재설정 API
class PasswordResetView(APIView):
    def post(self, request):
        phone_number = request.data.get("phone")
        new_password = request.data.get("new_password")

        if not phone_number or not new_password:
            return Response(
                {"success": False, "message": "전화번호와 새 비밀번호를 입력해주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PasswordCheckSerializer(data={"new_password": new_password})
        if not serializer.is_valid():
            return Response(
                {"success": False, "message": serializer.errors["new_password"][0]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(phone=phone_number)
            user.password = make_password(new_password)
            user.save()
            return Response(
                {"success": True, "message": "비밀번호가 성공적으로 변경되었습니다."},
                status=status.HTTP_200_OK,
            )
        except User.DoesNotExist:
            return Response(
                {
                    "success": False,
                    "message": "해당 전화번호로 등록된 사용자가 없습니다.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )


# User Verification APIs
# 인증 코드 전송 API
class SendVerificationCodeView(APIView):
    
    def generate_verification_code(self):
        # 6자리 인증 코드 생성
        return str(random.randint(100000, 999999))

    def post(self, request):
        user_id = request.data.get("user_id")
        phone_number = request.data.get("phone")
        code_type = request.data.get("type")

        print(f"📌 [DEBUG] 받은 데이터 → user_id: {user_id}, phone: {phone_number}, type: {code_type}")

        # 필수 정보가 없으면 오류 반환
        if (
            not phone_number
            or not code_type
            or (code_type not in ["findID", "signup"] and not user_id)
        ):
            print(f"⚠ [ERROR] 필수 정보 누락")
            return Response(
                {"success": False, "message": "필수 정보를 입력해주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 인증 코드 유형별 처리
        if code_type == "findID":
            try:
                user = User.objects.get(phone=phone_number)
            except User.DoesNotExist:
                print(f"⚠ [ERROR] 해당 전화번호({phone_number})로 등록된 사용자 없음")
                return Response(
                    {
                        "success": False,
                        "message": "해당 전화번호로 등록된 사용자가 없습니다.",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

        elif code_type == "findPW":
            try:
                user = User.objects.get(username=user_id, phone=phone_number)
            except User.DoesNotExist:
                print(f"⚠ [ERROR] 아이디({user_id}) 또는 전화번호({phone_number}) 불일치")
                return Response(
                    {
                        "success": False,
                        "message": "아이디 또는 전화번호가 잘못되었습니다.",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

        elif code_type == "mypage":
            try:
                user = User.objects.get(username=user_id)
                if user.phone == phone_number:
                    print(f"⚠ [ERROR] {user_id}의 기존 번호({phone_number})와 동일")
                    return Response(
                        {"success": False, "message": "이미 등록된 핸드폰 번호입니다."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except User.DoesNotExist:
                print(f"⚠ [ERROR] 해당 ID({user_id})로 등록된 사용자 없음")
                return Response(
                    {
                        "success": False,
                        "message": "해당 ID로 등록된 사용자가 없습니다.",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

        else:
            # 회원가입 등 기타 경우: 전화번호 중복 확인
            if User.objects.filter(phone=phone_number, is_active=True).exists():
                print(f"⚠ [ERROR] {phone_number}는 이미 가입된 전화번호")
                return Response(
                    {"success": False, "message": "이미 가입된 전화번호입니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 인증 코드 생성 및 캐시에 저장
        verification_code = self.generate_verification_code()
        cache_key = f"{code_type}_verification_code_{phone_number}"
        cache.set(cache_key, verification_code, timeout=300)

        print(f"✅ [DEBUG] 인증 코드 생성 완료 → {verification_code}")
        print(f"✅ [DEBUG] 캐시 저장 완료 → key: {cache_key}")

        # SMS 전송 API 호출
        sms_success = send_aligo_sms(
            receiver=phone_number, message=f"인증 번호는 [{verification_code}]입니다."
        )

        print(f"📡 [DEBUG] SMS 전송 API 결과 → {sms_success}")

        if sms_success:
            print(f"✅ [INFO] 인증 번호 전송 성공 → {phone_number}")
            return Response({"success": True, "message": "인증 번호가 발송되었습니다."})
        else:
            print(f"❌ [ERROR] {phone_number}로 인증 번호 발송 실패")
            return Response(
                {"success": False, "message": "인증 번호 발송에 실패했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            
            
            
# 인증 코드 검증 API
class VerifyCodeView(APIView):
    def post(self, request):
        phone_number = request.data.get("phone")
        entered_code = request.data.get("code")
        code_type = request.data.get("type")
        user_id = request.data.get("user_id")

        # 전송된 데이터 로깅
        # logger.debug(f"Received Data - phone_number: {phone_number}, entered_code: {entered_code}, code_type: {code_type}, user_id: {user_id}")

        if (
            not phone_number
            or not code_type
            or (code_type not in ["findID", "signup"] and not user_id)
        ):
            return Response(
                {
                    "success": False,
                    "message": "필수 정보(전화번호, 인증 번호, 사용자 ID)를 입력해주세요.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        cache_key = f"{code_type}_verification_code_{phone_number}"
        saved_code = cache.get(cache_key)

        if saved_code and saved_code == entered_code:
            if code_type == "mypage":
                try:
                    # user_id에 해당하는 사용자 검색
                    user = User.objects.get(username=user_id)
                    # 사용자의 전화번호를 입력받은 phone_number로 업데이트
                    user.phone = phone_number
                    user.save()

                    return Response(
                        {
                            "success": True,
                            "message": "인증이 완료되었으며, 전화번호가 업데이트되었습니다.",
                        },
                        status=status.HTTP_200_OK,
                    )
                except User.DoesNotExist:
                    return Response(
                        {
                            "success": False,
                            "message": "해당 ID로 등록된 사용자가 없습니다.",
                        },
                        status=status.HTTP_404_NOT_FOUND,
                    )

            elif code_type == "findID" or code_type == "findPW":
                try:
                    # 사용자 정보 반환
                    user = User.objects.get(phone=phone_number)
                    return Response(
                        {
                            "success": True,
                            "message": "인증이 완료되었습니다.",
                            "user_id": user.username,
                            "user_password": user.password,
                            "date_joined": user.created_at.strftime("%Y.%m.%d"),
                        },
                        status=status.HTTP_200_OK,
                    )
                except User.DoesNotExist:
                    return Response(
                        {
                            "success": False,
                            "message": "해당 전화번호로 등록된 사용자가 없습니다.",
                        },
                        status=status.HTTP_404_NOT_FOUND,
                    )
            else:
                return Response(
                    {"success": True, "message": "회원가입 인증이 완료되었습니다."},
                    status=status.HTTP_200_OK,
                )
        else:
            return Response(
                {"success": False, "message": "인증 번호가 일치하지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )


# Deactivate Account APIs
class DeactivateAccountView(APIView):
    """
    사용자를 탈퇴시키는 뷰.
    즉시 탈퇴하지 않고, 구독 해지가 완료될 때까지 기다린 후 탈퇴 진행
    개인정보를 익명화 처리.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        회원 탈퇴 요청 (즉시 탈퇴 X, 구독 해지가 완료된 후 탈퇴)
        """
        user = request.user

        # 이미 탈퇴 요청한 경우
        if user.is_deactivation_requested:
            return Response(
                {"message": "이미 탈퇴 요청이 접수되었습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 활성 구독이 있는지 확인
        active_subscription = Subscription.objects.filter(
            user=user, is_active=True
        ).first()

        if active_subscription:
            # ⛔ 구독 해지 신청이 되지 않은 경우 → 탈퇴 불가
            if not active_subscription.deactivation_date:
                return Response(
                    {"message": "구독 해지 신청 후 탈퇴 요청이 가능합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # ✅ 구독 해지 신청이 된 경우 → 탈퇴 요청 상태 저장 (즉시 탈퇴 X)
            user.is_deactivation_requested = True
            user.save(update_fields=["is_deactivation_requested"])
            return Response(
                {
                    "message": f"탈퇴 요청이 접수되었습니다. \n 구독 해지 후 자동 탈퇴됩니다."
                },
                status=status.HTTP_200_OK,
            )

        # ✅ 구독이 없거나, 이미 해지된 경우 → 즉시 탈퇴 가능
        self.deactivate_and_anonymize_user(user)
        return Response(
            {"message": "계정이 성공적으로 탈퇴되었습니다."},
            status=status.HTTP_200_OK,
        )

    def deactivate_and_anonymize_user(self, user):
        """
        사용자 탈퇴 시 개인정보를 익명화하고 계정을 비활성화.
        """

        # 사용자 정보 익명화
        user.username = f"deleted_user_{user.user_id}"  # 사용자 아이디를 익명화
        user.phone = f"000-0000-0000_{user.user_id}"  # 핸드폰 번호 삭제 또는 익명화
        user.email = f"deleted_{user.user_id}@example.com"  # 이메일을 익명화
        user.name = "탈퇴한 사용자"  # 이름 익명화

        # 사용자 비활성화
        user.is_active = False
        user.deactivated_at = timezone.now()  # 비활성화 시간 기록
        user.save()

        # 사용자 결제 내역 익명화
        self.anonymize_payment_history(user)

        # 사용자가 소유한 가게 및 관련된 데이터 익명화
        self.anonymize_stores(user)

        # 사용자와 관련된 ServiceRequest 데이터 익명화
        self.anonymize_ServiceRequests(user)

        # 사용자 폴더 삭제
        self.delete_user_folder(user)

    def anonymize_stores(self, user):
        """
        탈퇴한 사용자의 가게 데이터를 익명화 처리.
        """
        stores = Store.objects.filter(user=user)
        for store in stores:
            store.store_name = f"익명화된 가게_{store.store_id}"  # 가게 이름 익명화
            store.slug = f"deleted-store_{store.store_id}"  # 간단한 익명화 처리
            store.save()

            # 가게의 메뉴 익명화 처리
            menus = Menu.objects.filter(store=store)
            for menu in menus:
                menu.name = f"익명화된 메뉴_{menu.menu_number}"
                menu.price = 0  # 가격을 0으로 설정하여 의미가 없도록 처리
                menu.image = ""
                menu.save()

    def anonymize_ServiceRequests(self, user):
        """
        탈퇴한 사용자의 ServiceRequest 데이터를 익명화 처리.
        """
        ServiceRequests = ServiceRequest.objects.filter(user=user)
        for service_request in ServiceRequests:
            service_request.title = f"익명화된 제목_{service_request.id}"
            service_request.content = "익명화된 내용"
            service_request.file = None  # 파일 삭제
            service_request.save()

    def delete_user_folder(self, user):
        """
        탈퇴한 사용자의 파일이 저장된 폴더를 삭제.
        """

        stores = Store.objects.filter(user=user)
        for store in stores:

            # 업로드 폴더 경로
            store_uploads_folder_path = os.path.join(
                settings.MEDIA_ROOT, "uploads", f"store_{store.store_id}"
            )
            # 업로드 폴더 경로 존재하면 삭제
            if os.path.exists(store_uploads_folder_path):
                shutil.rmtree(store_uploads_folder_path)

            # 메뉴 이미지 폴더 경로
            store_menu_images_folder_path = os.path.join(
                settings.MEDIA_ROOT, "menu_images", f"store_{store.store_id}"
            )
            # 메뉴 이미지 폴더 경로 존재하면 삭제
            if os.path.exists(store_menu_images_folder_path):
                shutil.rmtree(store_menu_images_folder_path)

            # banner 폴더 경로
            store_banner_folder_path = os.path.join(
                settings.MEDIA_ROOT, "banner", f"store_{store.store_id}"
            )
            # banner 폴더 경로 존재하면 삭제
            if os.path.exists(store_banner_folder_path):
                shutil.rmtree(store_banner_folder_path)

            # profile 폴더 경로
            store_profile_folder_path = os.path.join(
                settings.MEDIA_ROOT, "profile", f"store_{store.store_id}"
            )
            # profile 폴더 경로 존재하면 삭제
            if os.path.exists(store_profile_folder_path):
                shutil.rmtree(store_profile_folder_path)

            # statistics 폴더 경로
            store_statistics_folder_path = os.path.join(
                settings.MEDIA_ROOT, "statistics", f"store_{store.store_id}"
            )
            # statistics 폴더 경로 존재하면 삭제
            if os.path.exists(store_statistics_folder_path):
                shutil.rmtree(store_statistics_folder_path)

            # QR 코드 파일 경로
            store_qrcodes_path = os.path.join(
                settings.MEDIA_ROOT, "qrcodes", f"qr_{store.store_id}.png"
            )
            # QR 코드 파일이 존재하면 삭제
            if os.path.exists(store_qrcodes_path):
                os.remove(store_qrcodes_path)

    def anonymize_payment_history(self, user):
        """
        탈퇴한 사용자의 결제 내역을 익명화 처리.
        """
        payments = PaymentHistory.objects.filter(user=user)
        for payment in payments:
            payment.imp_uid = f"deleted_{payment.id}_imp"
            payment.merchant_uid = f"deleted_{payment.id}_merchant"
            payment.merchant_name = "익명화된 결제 내역"
            payment.user = None  # 사용자를 NULL 처리하여 연결 해제
            payment.save()


# 소셜 로그인 API
class SocialSignupView(APIView):
    def post(self, request):
        """
        ✅ 소셜 로그인 후 회원가입을 처리하는 API
        """
        # print(request.data)

        # ✅ 사용자 데이터 변환
        user_data = {
            "username": request.data.get("username"),
            "email": request.data.get("email"),
            "name": request.data.get("name"),
            "dob": request.data.get("dob"),
            "phone": request.data.get("phone"),
            "password": None,  # 🔥 소셜 로그인 사용자는 비밀번호 없이 가입
            "billing_key": None,
        }

        # ✅ 스토어 데이터 변환
        store_data = {
            "store_category": request.data.get("store_category"),
            "store_name": request.data.get("store_name"),
            "store_address": request.data.get("store_address"),
            "slug": slugify(quote(request.data.get("store_name", ""))),
        }

        # print("=== [DEBUG] 변환된 사용자 데이터 ===")
        # print(user_data)

        # ✅ 스토어 중복 체크
        if Store.objects.filter(store_name=store_data["store_name"]).exists():
            return Response(
                {"success": False, "message": "이미 존재하는 스토어 이름입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                # ✅ 사용자 데이터 검증 및 저장
                user_serializer = UserSerializer(data=user_data)
                if not user_serializer.is_valid():
                    print(user_serializer.errors)
                    return Response(
                        {
                            "success": False,
                            "message": "회원가입 실패",
                            "errors": user_serializer.errors,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                user = user_serializer.save()

                # ✅ 스토어 데이터 검증 및 저장
                store_serializer = StoreSerializer(
                    data=store_data, context={"user": user}
                )

                if not store_serializer.is_valid():
                    print(store_serializer.errors)
                    return Response(
                        {
                            "success": False,
                            "message": "스토어 생성 실패",
                            "errors": store_serializer.errors,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                store = store_serializer.save(user=user)  # user 포함해서 저장됨

                return Response(
                    {
                        "success": True,
                        "message": "회원가입 성공",
                        "store_id": store.store_id,
                    },
                    status=status.HTTP_201_CREATED,
                )

        except Exception as e:
            print(str(e))
            logger.error(f"소셜 회원가입 오류: {str(e)}")
            return Response(
                {"success": False, "message": "서버 오류 발생"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class OAuthLoginAPIView(APIView):
    """
    SNS에서 발급받은 `code`를 이용해 Access Token을 요청
    """

    def post(self, request):
        provider = request.data.get("provider")  # 'google', 'kakao', 'naver'
        code = request.data.get("code")

        if not provider or not code:
            return Response(
                {"error": "provider와 code가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        oauth_settings = {
            "kakao": {
                "token_url": "https://kauth.kakao.com/oauth/token",
                "client_id": settings.SOCIAL_AUTH_KAKAO_KEY,
                "client_secret": settings.SOCIAL_AUTH_KAKAO_SECRET,
                "redirect_uri": settings.SOCIAL_AUTH_KAKAO_REDIRECT_URI,
            },
            "naver": {
                "token_url": "https://nid.naver.com/oauth2.0/token",
                "client_id": settings.SOCIAL_AUTH_NAVER_KEY,
                "client_secret": settings.SOCIAL_AUTH_NAVER_SECRET,
                "redirect_uri": settings.SOCIAL_AUTH_NAVER_REDIRECT_URI,
            },
        }

        if provider not in oauth_settings:
            return Response(
                {"error": "지원되지 않는 provider입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # ✅ Access Token 요청
            token_data = {
                "grant_type": "authorization_code",
                "client_id": oauth_settings[provider]["client_id"],
                "client_secret": oauth_settings[provider]["client_secret"],
                "redirect_uri": oauth_settings[provider]["redirect_uri"],
                "code": code,
            }
            token_response = requests.post(
                oauth_settings[provider]["token_url"], data=token_data
            )
            token_json = token_response.json()

            # print(f"✅ [OAuthLoginAPIView] token_response: {token_response.status_code}, {token_json}")

            if "access_token" not in token_json:
                return Response(
                    {"error": "OAuth 토큰 요청 실패", "details": token_json},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            access_token = token_json["access_token"]

            # ✅ 사용자 정보 가져오기
            user_info = self.get_user_info(provider, access_token)
            if not user_info:
                return Response(
                    {"error": "사용자 정보를 가져오지 못했습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # ✅ phone 정규화 (네이버: mobile, 카카오: phone_number)
            def normalize_phone(phone):
                if not phone:
                    return None
                # 모든 숫자만 추출 (공백, 하이픈, 기타 문자는 제거)
                digits = re.sub(r"\D", "", phone)

                # 만약 국가 코드 '82'로 시작하고 총 자리수가 11자리 이상이면 '82' 제거
                if digits.startswith("82") and len(digits) > 10:
                    digits = digits[2:]

                # 만약 10자리라면 앞에 '0' 붙이기
                if len(digits) == 10:
                    digits = "0" + digits

                return digits

            user_info["phone"] = normalize_phone(user_info.get("phone", ""))

            # 생년월일 정규화
            def normalize_dob(birthyear, birthday):
                if not birthyear or not birthday:
                    return None  # 생년월일이 없는 경우 None 반환
                return f"{birthyear}-{birthday}"  # YYYY-MM-DD 형태

            user_info["dob"] = normalize_dob(
                user_info.get("birthyear"), user_info.get("birthday")
            )

            # ✅ 중복 사용자 체크
            try:
                user = User.objects.get(phone=user_info["phone"])
                social_signup = not user.stores.exists()

                # ✅ 사용자의 첫 번째 store 정보 가져오기
                store = user.stores.first()
                store_id = store.store_id if store else None

                return Response(
                    {
                        "access_token": access_token,
                        "social_signup": social_signup,
                        "user_data": {
                            "username": user.username,
                            "email": user.email,
                            "name": user.name,
                            "dob": user.dob,
                            "phone": user.phone,
                            "billing_key": (
                                user.billing_key if user.billing_key else None
                            ),
                        },
                        "store_id": store_id,
                    },
                    status=status.HTTP_200_OK,
                )

            except User.DoesNotExist:
                return Response(
                    {
                        "access_token": access_token,
                        "social_signup": True,
                        "user_data": {
                            "username": f"{provider}_{user_info['id']}",
                            "email": user_info.get("email", ""),
                            "name": user_info.get("name", ""),
                            "dob": user_info.get("dob"),
                            "phone": user_info.get("phone", ""),
                        },
                    },
                    status=status.HTTP_200_OK,
                )

        except Exception as e:
            print(f"❌ [OAuthLoginAPIView] 서버 오류 발생: {str(e)}")
            logger.error(f"OAuthLoginAPIView 서버 오류: {str(e)}")
            return Response(
                {"error": "서버 내부 오류 발생", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def get_user_info(self, provider, access_token):
        """
        ✅ OAuth Provider 별 사용자 정보 가져오기
        """
        try:
            headers = {"Authorization": f"Bearer {access_token}"}

            if provider == "kakao":
                response = requests.get(
                    "https://kapi.kakao.com/v2/user/me", headers=headers
                )
                data = response.json()
                print(f"kakao response data: {data}")

                kakao_account = data.get("kakao_account", {})

                if not kakao_account.get("name"):
                    raise ValueError("이름 정보 제공에 동의해주세요.")

                if not kakao_account.get("phone_number"):
                    raise ValueError("휴대폰 번호 정보 제공에 동의해주세요.")

                return {
                    "id": data["id"],
                    "name": kakao_account["name"],
                    "phone": kakao_account["phone_number"],
                    "email": kakao_account["email"],
                }

            elif provider == "naver":
                response = requests.get(
                    "https://openapi.naver.com/v1/nid/me", headers=headers
                )
                data = response.json().get("response", {})
                # print(f"naver response data: {data}")

                if "id" not in data:
                    raise ValueError("네이버 사용자 정보가 유효하지 않습니다.")

                return {
                    "id": str(data["id"])[:10],
                    "email": data.get("email"),
                    "name": data.get("name"),
                    "birthyear": data.get("birthyear"),
                    "birthday": data.get("birthday"),
                    "phone": data.get("mobile"),
                }

        except Exception as e:
            print(f"❌ [OAuthLoginAPIView] 사용자 정보 요청 중 오류 발생: {str(e)}")
            return None  # 예외 발생 시 None 반환


class OAuthJWTTokenView(APIView):
    """
    ✅ 소셜 로그인 후 JWT 토큰으로 변환해주는 API
    """

    def post(self, request):
        print(f"request.data: {request.data}")

        try:
            # ✅ 요청 데이터 확인
            access_token = request.data.get("access_token")
            username = request.data.get("username")
            phone = request.data.get("phone")

            # print(f"✅ access_token: {access_token}")
            # print(f"✅ username: {username}")
            # print(f"✅ phone: {phone}")

            if not access_token or (not username and not phone):
                print("❌ [ERROR] 필수 파라미터 누락")
                return Response(
                    {"success": False, "message": "필수 파라미터가 누락되었습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # ✅ 사용자 조회 (username 또는 전화번호로)
            try:
                if username:
                    user = User.objects.get(username=username)
                else:
                    user = User.objects.get(phone=phone)

                # ✅ JWT 토큰 생성
                refresh = RefreshToken.for_user(user)

                # ✅ 응답 데이터 구성
                store = user.stores.first()  # 사용자의 첫 번째 스토어 가져오기

                response_data = {
                    "success": True,
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "user_data": {
                        "username": user.username,
                        "email": user.email,
                        "name": user.name,
                        "dob": user.dob,
                        "phone": user.phone,
                        "billing_key": (
                            user.billing_key if hasattr(user, "billing_key") else None
                        ),
                    },
                }

                # ✅ 스토어 정보 추가
                if store:
                    response_data["store_id"] = store.store_id
                    response_data["store_name"] = store.store_name

                # ✅ 구독 정보 추가
                if hasattr(user, "subscription") and user.subscription:
                    response_data["subscription"] = {
                        "is_active": user.subscription.is_active,
                        "expiry_date": (
                            user.subscription.expiry_date.isoformat()
                            if user.subscription.expiry_date
                            else None
                        ),
                        "plan": user.subscription.plan,
                    }
                else:
                    response_data["subscription"] = {"is_active": False}

                # print(f"✅ 최종 응답 데이터: {response_data}")
                return Response(response_data, status=status.HTTP_200_OK)

            except User.DoesNotExist:
                print("❌ [ERROR] 해당 사용자를 찾을 수 없음")
                return Response(
                    {"success": False, "message": "해당 사용자를 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        except Exception as e:
            print(f"❌ [ERROR] JWT 토큰 변환 중 오류 발생: {str(e)}")
            logger.error(f"JWT 토큰 변환 중 오류 발생: {str(e)}")
            return Response(
                {
                    "success": False,
                    "message": "서버 오류가 발생했습니다.",
                    "details": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
