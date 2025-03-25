# auth_views.py
# 로그인, 회원가입, 비밀번호 재설정, 계정 비활성화
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import transaction
from django.conf import settings
from django.utils import timezone 
from ..authentication import CorpUserJWTAuthentication
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
import random, logging, requests
from send_sms import send_aligo_sms
from ..models import Corp_User, Corp, Corp_Department, Corp_ServiceRequest
from ..serializers import (
    CorpUserSerializer,
    CorpUsernameCheckSerializer, 
    CorpPasswordCheckSerializer,
)

# 디버깅을 위한 로거 설정
logger = logging.getLogger('faq')


# User Management APIs
# 회원가입 API
class SignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        # 사용자 정보 받아오기
        user_data = {
            'username': request.data.get('username'),
            'password': request.data.get('password'),
            'name': request.data.get('name'),
            'dob': request.data.get('dob'),
            'phone': request.data.get('phone'),
            'email': request.data.get('email') if request.data.get('email') else None,
            'marketing': request.data.get('marketing'),
        }

        corp_id = request.data.get('corp_id')
        department_name = request.data.get('department')
        user_data['department'] = department_name
        
        # 기관 ID가 없는 경우 오류 반환
        if not corp_id:
            return Response({'success': False, 'message': '기업 ID가 제공되지 않았습니다.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # 기관 조회
            corporation = Corp.objects.get(corp_id=corp_id)
            user_data['corp'] = corporation.corp_id  
            #logger.debug(f"공공기관 조회 성공: {corp}")

            # 사용자 생성과 기관 및 부서 연결을 트랜잭션으로 처리
            with transaction.atomic():
                # department 이름으로 부서 조회 또는 생성
                department, created = Corp_Department.objects.get_or_create(
                    department_name=department_name,
                    corp=corporation  # 이 부분에서 corporation을 corp 필드로 지정
                )
                #logger.debug(f"부서 생성/조회 성공: {department}, 생성 여부: {created}")

                # 사용자 생성
                user_serializer = CorpUserSerializer(data=user_data)
                
                # 사용자 데이터 검증
                if not user_serializer.is_valid():
                    #logger.debug(f"회원가입 유효성 검사 실패: {user_serializer.errors}")
                    return Response({
                        'success': False, 
                        'message': '회원가입에 실패했습니다.', 
                        'errors': user_serializer.errors
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                # 사용자 저장
                user = user_serializer.save()

                # 생성한 사용자에 기관 및 부서 할당
                user.corp = corporation
                user.department = department  # department_id 저장
                user.save()

                return Response({
                    'success': True, 
                    'message': '사용자와 기업 및 부서가 성공적으로 연결되었습니다.'
                }, status=status.HTTP_201_CREATED)

        except Corp.DoesNotExist:
            logger.error("해당 ID의 기업이 존재하지 않습니다.")
            return Response({'success': False, 'message': '해당 ID의 기업이 존재하지 않습니다.'}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"서버 오류 발생: {str(e)}")
            return Response({
                'success': False, 
                'message': '서버 오류가 발생했습니다. 다시 시도해주세요.',
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# 로그인 API 뷰
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        captcha_token  = request.data.get("captcha")
        
        # CAPTCHA 검증
        captcha_valid, score = self.verify_captcha(captcha_token)
        if not captcha_valid:
            return Response({"error": "CAPTCHA 검증 실패"}, status=status.HTTP_400_BAD_REQUEST)
        
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
            user = Corp_User.objects.get(username=username)
            
            if check_password(password, user.password):
                # Refresh 및 Access 토큰 생성
                refresh = RefreshToken.for_user(user)
                access_token = str(refresh.access_token)
                
                # 사용자의 corp_id 반환
                if user.corp:
                    corp_id = user.corp.corp_id
                else:
                    return Response({"error": "기업이 없습니다."}, status=status.HTTP_404_NOT_FOUND)
                
                # 토큰 정보 출력
                #logger.debug(f"Generated Access Token for Corp_User ID {user.user_id}: {access_token}")
                
                return Response({'access': access_token, 'corp_id': corp_id})
            else:
                return Response({"error": "아이디 또는 비밀번호가 일치하지 않습니다.\n다시 시도해 주세요."}, status=status.HTTP_401_UNAUTHORIZED)

        except Corp_User.DoesNotExist:
            return Response(
                {"error": "입력하신 아이디로 가입된 계정이 없습니다.\n회원가입 후 로그인해 주세요."},
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
# 사용자명 중복 확인 API
class UsernameCheckView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CorpUsernameCheckSerializer(data=request.data)
        #logger.debug(f"Received data for username check: {request.data}")
        if serializer.is_valid():
            username = serializer.validated_data['username']
            #logger.debug(f"Validated username: {username}")

            if Corp_User.objects.filter(username=username, is_active=True).exists():
                return Response({'is_duplicate': True, 'message': '이미 사용 중인 사용자 아이디입니다.'}, status=status.HTTP_409_CONFLICT)
            
            return Response({'is_duplicate': False, 'message': '사용 가능한 사용자 아이디입니다.'}, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# 비밀번호 재설정 API
class PasswordResetView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        phone_number = request.data.get('phone')
        new_password = request.data.get('new_password')

        if not phone_number or not new_password:
            return Response({'success': False, 'message': '전화번호와 새 비밀번호를 입력해주세요.'}, status=status.HTTP_400_BAD_REQUEST)

        # 비밀번호 정규식 검증
        serializer = CorpPasswordCheckSerializer(data={'new_password': new_password})
        if not serializer.is_valid():
            return Response({'success': False, 'message': serializer.errors['new_password'][0]}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # 사용자 비밀번호 업데이트
            user = Corp_User.objects.get(phone=phone_number)
            user.password = make_password(new_password)
            user.save()

            return Response({'success': True, 'message': '비밀번호가 성공적으로 변경되었습니다.'}, status=status.HTTP_200_OK)
        except Corp_User.DoesNotExist:
            return Response({'success': False, 'message': '해당 전화번호로 등록된 사용자가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
        

# User Verification APIs
# 인증 코드 전송 API
class SendVerificationCodeView(APIView):
    permission_classes = [AllowAny]

    def generate_verification_code(self):
        # 6자리 인증 코드 생성
        return str(random.randint(100000, 999999))

    def post(self, request):
        # 요청 데이터 가져오기
        user_id = request.data.get('user_id')
        phone_number = request.data.get('phone')
        code_type = request.data.get('type')

        #logger.debug(f"phone_number: {phone_number}, code_type: {code_type}, user_id: {user_id}")

        # 필수 정보가 없으면 오류 반환
        if not phone_number or not code_type or (code_type not in ['findID', 'signup', 'complaint'] and not user_id):
            return Response({'success': False, 'message': '필수 정보를 입력해주세요.'}, status=status.HTTP_400_BAD_REQUEST)

        # 인증 코드 유형별 처리
        if code_type == 'findID':
            # 전화번호로 사용자 확인
            try:
                user = Corp_User.objects.get(phone=phone_number)
            except Corp_User.DoesNotExist:
                return Response({'success': False, 'message': '해당 전화번호로 등록된 사용자가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

        elif code_type == 'findPW':
            # 사용자 ID와 전화번호로 사용자 확인
            try:
                user = Corp_User.objects.get(username=user_id, phone=phone_number)
            except Corp_User.DoesNotExist:
                return Response({'success': False, 'message': '아이디 또는 전화번호가 잘못되었습니다.'}, status=status.HTTP_404_NOT_FOUND)

        elif code_type == 'mypage':
            # mypage에서 전화번호가 이미 등록된 번호인지 확인
            try:
                user = Corp_User.objects.get(username=user_id)
                if user.phone == phone_number:
                    return Response({'success': False, 'message': '이미 등록된 핸드폰 번호입니다.'}, status=status.HTTP_400_BAD_REQUEST)
            except Corp_User.DoesNotExist:
                return Response({'success': False, 'message': '해당 ID로 등록된 사용자가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

        elif code_type == 'complaint':
            # 전화번호와 민원 번호로 민원 확인
            complaint_number = request.data.get('complaintNum')  # 추가된 필드
            if not complaint_number:
                return Response({'success': False, 'message': '문의 번호를 입력해주세요.'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                user = Corp_User.objects.get(phone=phone_number, complaint_number=complaint_number)
            except Corp_User.DoesNotExist:
                return Response({'success': False, 'message': '해당 정보로 접수된 문의글이 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

            
        else:
            # 회원가입 등 기타 경우: 전화번호 중복 확인
            if Corp_User.objects.filter(phone=phone_number, is_active=True).exists():
                return Response({'success': False, 'message': '이미 가입된 전화번호입니다.'}, status=status.HTTP_400_BAD_REQUEST)

        # 인증 코드 생성 및 캐시에 저장
        cache_key = f'{code_type}_verification_code_{phone_number}'
        verification_code = self.generate_verification_code()
        cache.set(cache_key, verification_code, timeout=300)  # 항상 새 값 저장
        
        logger.debug(f"New Verification Code Set: {verification_code}")

        # SMS 전송 API 호출
        sms_success = send_aligo_sms(
            receiver=phone_number,
            message=f"인증 번호는 [{verification_code}]입니다."
        )

        if sms_success:
            logger.info(f"인증 번호가 성공적으로 전송되었습니다.")
            return Response({'success': True, 'message': '인증 번호가 발송되었습니다.'})
        else:
            logger.error(f"{phone_number}로 인증 번호 발송 실패")
            return Response({'success': False, 'message': '인증 번호 발송에 실패했습니다.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        


# 인증 코드 검증 API
class VerifyCodeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        phone_number = request.data.get('phone')
        entered_code = request.data.get('code')
        code_type = request.data.get('type')
        user_id = request.data.get('user_id') 

        # 입력받은 데이터 로깅
        #logger.debug(f"Received Data - phone_number: {phone_number}, entered_code: {entered_code}, code_type: {code_type}, user_id: {user_id}")
        #logger.debug(f"Request Data: {request.data}")
        
        # 필수 정보 확인
        if not phone_number or not code_type or (code_type not in ['findID', 'signup', 'complaint'] and not user_id):
            #logger.debug("필수 정보 누락")
            return Response({'success': False, 'message': '필수 정보(전화번호, 인증 번호, 사용자 ID)를 입력해주세요.'}, status=status.HTTP_400_BAD_REQUEST)

        # 캐시에서 인증 코드 가져오기
        cache_key = f'{code_type}_verification_code_{phone_number}'
        saved_code = cache.get(cache_key)
        #logger.debug(f"Cache Key: {cache_key}, Saved Code: {saved_code}")
        #logger.debug(f"Entered Code: {entered_code}")

        # 인증 코드 일치 확인
        if saved_code and str(saved_code).strip() == str(entered_code).strip():
            #logger.debug("Verification successful.")
            # 유형별 처리
            if code_type == 'mypage':
                try:
                    user = Corp_User.objects.get(username=user_id)
                    user.phone = phone_number
                    user.save()
                    #logger.debug("전화번호 업데이트 완료")
                    return Response({'success': True, 'message': '인증이 완료되었으며, 전화번호가 업데이트되었습니다.'}, status=status.HTTP_200_OK)
                except Corp_User.DoesNotExist:
                    #logger.debug("ID에 해당하는 사용자 없음")
                    return Response({'success': False, 'message': '해당 ID로 등록된 사용자가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

            elif code_type in ['findID', 'findPW']:
                try:
                    user = Corp_User.objects.get(phone=phone_number)
                    #logger.debug("사용자 정보 반환")
                    return Response({
                        'success': True,
                        'message': '인증이 완료되었습니다.',
                        'user_id': user.username,
                        'date_joined': user.created_at.strftime('%Y.%m.%d')
                    }, status=status.HTTP_200_OK)
                except Corp_User.DoesNotExist:
                    #logger.debug("전화번호에 해당하는 사용자 없음")
                    return Response({'success': False, 'message': '해당 전화번호로 등록된 사용자가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
                
            elif code_type == 'complaint':
                # 전화번호와 민원 번호로 민원 확인
                complaint_number = request.data.get('complaintNum')  # 추가된 필드
                #logger.debug(f"complaintNum: {request.data.get('complaintNum')}")
                #logger.debug(f"complaint_number: {complaint_number}")


                if not complaint_number:
                    #logger.debug("Complaint number not provided.")
                    return Response({'success': False, 'message': '민원 번호를 입력해주세요.'}, status=status.HTTP_400_BAD_REQUEST)

                try:
                    user = Corp_User.objects.get(phone=phone_number, complaint_number=complaint_number)
                    #logger.debug(f"Complaint verification successful for complaint number: {complaint_number}")
                    return Response({'success': True, 'message': '민원이 확인되었습니다.', 'complaint': user.complaint_number}, status=status.HTTP_200_OK)
                except Corp_User.DoesNotExist:
                    #logger.debug(f"No complaint found for phone: {phone_number}, complaintNum: {complaint_number}")
                    return Response({'success': False, 'message': '해당 정보로 접수된 민원이 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
                
            elif code_type == 'signup':
                #logger.debug("회원가입 인증 성공")
                return Response({'success': True, 'message': '회원가입 인증이 완료되었습니다.'}, status=status.HTTP_200_OK)
            
        else:
            # 인증 실패 처리
            #logger.debug("인증 번호 불일치")
            return Response({'success': False, 'message': '인증 번호가 일치하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)


# 계정 비활성화
class DeactivateAccountView(APIView):
    """
    사용자를 탈퇴시키는 뷰. 
    사용자 계정을 비활성화하고 개인정보를 익명화 처리.
    """
    authentication_classes = [CorpUserJWTAuthentication]  # 커스텀 인증 클래스 사용
    permission_classes = [IsAuthenticated]  # 인증된 사용자만 접근 가능

    def post(self, request):
        """
        계정을 비활성화하고 익명화
        """
        user = request.user

        # 사용자 탈퇴(비활성화 + 익명화) 처리
        self.deactivate_and_anonymize_user(user)

        return Response({"message": "계정이 성공적으로 탈퇴되었습니다."}, status=status.HTTP_200_OK)

    def deactivate_and_anonymize_user(self, user):
        """
        사용자 탈퇴 시 개인정보를 익명화하고 계정을 비활성화.
        """
        # 사용자 정보 익명화
        user.username = f'deleted_user_{user.user_id}'  # 사용자 아이디를 익명화
        user.phone = f'010-0000-0000_{user.user_id}'  # 핸드폰 번호 삭제 또는 익명화
        user.email = f'deleted_{user.user_id}@example.com'  # 이메일을 익명화
        user.name = '탈퇴한 사용자'  # 이름 익명화

        # 사용자 비활성화
        user.is_active = False
        user.deactivated_at = timezone.now()  # 비활성화 시간 기록
        user.save()

        # 사용자와 관련된 ServiceRequest 데이터 익명화
        self.anonymize_ServiceRequests(user)

    
    def anonymize_ServiceRequests(self, user):
        """
        탈퇴한 사용자의 ServiceRequest 데이터를 익명화 처리.
        """
        ServiceRequests = Corp_ServiceRequest.objects.filter(user=user)
        for service_request in ServiceRequests:
            service_request.title = f'익명화된 제목_{service_request.id}'
            service_request.content = '익명화된 내용'
            service_request.file = None  # 파일 삭제
            service_request.save()

 