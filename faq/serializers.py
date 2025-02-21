from rest_framework import serializers
from .models import User, Store, ServiceRequest, Menu, BillingKey, Subscription, PaymentHistory
from django.contrib.auth.hashers import make_password
from django.conf import settings
from datetime import date
import re
import logging

logger = logging.getLogger("faq")


# 파일 검증 유틸리티 함수
def validate_file(value, allowed_extensions, max_file_size, error_message_prefix):
    # 파일 확장자 확인
    extension = value.name.split(".")[-1].lower()
    if extension not in allowed_extensions:
        return (
            f"{error_message_prefix} 유효하지 않은 파일 형식입니다. "
            f".{', .'.join(allowed_extensions)} 파일만 허용됩니다."
        )

    # 파일 크기 확인
    if value.size > max_file_size:
        return f"{error_message_prefix} 파일 크기는 {max_file_size // (1000 * 1024 * 1024)}MB 이하이어야 합니다."

    return None  # 오류가 없는 경우


class BillingKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingKey
        fields = "__all__"


class SubscriptionSerializer(serializers.ModelSerializer):
    billing_key = BillingKeySerializer(read_only=True)  # BillingKey 데이터를 포함

    class Meta:
        model = Subscription
        fields = ["id", "plan", "is_active", "next_billing_date", "billing_key"]


class PaymentHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentHistory
        fields = "__all__"


# 유저 관련 시리얼라이저
class UserSerializer(serializers.ModelSerializer):
    billing_key = BillingKeySerializer(required=False, allow_null=True)
    dob = serializers.DateField(format="%Y-%m-%d", input_formats=["%Y-%m-%d", "%y%m%d"], required=False)

    class Meta:
        model = User
        fields = [
            "user_id",
            "username",
            "password",
            "name",
            "dob",
            "phone",
            "email",
            "profile_photo",
            "created_at",
            "marketing",
            "billing_key",
        ]
        extra_kwargs = {
            "password": {"write_only": True, "required": False, "allow_null": True},
            "email": {"required": False},  # 이메일은 필수가 아님
        }

    # 사용자명 검증 (정규식을 사용하여 소문자와 숫자만 허용)
    def validate_username(self, value):
        
        if value.startswith("naver_") or value.startswith("kakao_"):
            return value  # 소셜 로그인 사용자는 검증 제외
        
        if not re.match(r"^[a-z][a-z0-9]{3,11}$", value):  # 4~12자 제한
            raise serializers.ValidationError(
                "아이디는 영문 소문자로 시작하며, 영문 소문자와 숫자만을 포함한 4~12자여야 합니다."
            )
        return value

    # 비밀번호 검증 (길이와 다양한 문자 포함 여부)
    def validate_password(self, value):
        """
        비밀번호 검증 
        소셜 로그인일 경우 None 허용
        """
        if value is None:
            return ""  # 🔥 None이면 빈 문자열 반환 (오류 방지)
        
        if len(value) < 8 or len(value) > 20:
            raise serializers.ValidationError(
                "비밀번호는 8자에서 20자 사이여야 합니다."
            )

        # 대문자, 소문자, 숫자, 특수문자 중 2가지 이상 포함해야 함
        has_upper = re.search(r"[A-Z]", value) is not None
        has_lower = re.search(r"[a-z]", value) is not None
        has_digit = re.search(r"\d", value) is not None
        has_special = re.search(r"[!@#$%^&*]", value) is not None

        if sum([has_upper, has_lower, has_digit, has_special]) < 2:
            raise serializers.ValidationError(
                "비밀번호는 대문자, 소문자, 숫자, 특수문자 중 2가지 이상을 포함해야 합니다."
            )

        return value
    
    # 생년월일 YYYY-MM-DD
    def validate_dob(self, value):
        """
        `YYMMDD` 형식을 `YYYY-MM-DD`로 변환
        """
        if isinstance(value, str) and len(value) == 6:  # YYMMDD 형태인 경우
            try:
                year = int(value[:2])
                month = int(value[2:4])
                day = int(value[4:6])

                # 2000년대 출생인지 1900년대 출생인지 판단
                year += 2000 if year < 30 else 1900  # 30년 이전이면 2000년대, 이후면 1900년대

                return f"{year}-{month:02d}-{day:02d}"
            
            except ValueError:
                raise serializers.ValidationError("잘못된 생년월일 형식입니다. YYMMDD 또는 YYYY-MM-DD 형식이어야 합니다.")

        return value  # 이미 YYYY-MM-DD 형식이면 그대로 유지


    # 프로필 사진 검증 (파일 형식과 크기)
    def validate_profile_photo(self, value):
        error_message = validate_file(
            value, ["png", "jpg", "jpeg"], 1000 * 1024 * 1024, "프로필 사진"
        )
        if error_message:
            raise serializers.ValidationError(error_message)
        return value


    def create(self, validated_data):
        """
        사용자 생성: 비밀번호가 None이면 설정하지 않음
        """
        password = validated_data.pop("password", None)

        if password:
            validated_data["password"] = make_password(password)  # 비밀번호가 있으면 해시 처리
        else:
            validated_data["password"] = ""  # 🔥 None이 아닌 빈 문자열로 설정

        return super().create(validated_data)


# 스토어 관련 시리얼라이저
class StoreSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    
    class Meta:
        model = Store
        fields = "__all__"
        

    # 배너 이미지 검증 (빈 값은 허용하며, 파일 형식과 크기 검증)
    def validate_banner(self, value):
        if value in [None, ""]:
            return value

        error_message = validate_file(
            value, ["png", "jpg", "jpeg"], 1000 * 1024 * 1024, "배너 사진"
        )
        if error_message:
            raise serializers.ValidationError({"banner": error_message})
        return value

    def create(self, validated_data):
        user = self.context.get("user")  # 🔥 context에서 user 가져오기
        if not user:
            raise serializers.ValidationError({"user": "User가 제공되지 않았습니다."})  # 🚨 예외 처리

        validated_data["user"] = user  # 🔥 user 값을 validated_data에 추가
        return super().create(validated_data)
    

# 로그인 요청에 사용하는 시리얼라이저
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


# 사용자명 중복 확인 시리얼라이저
class UsernameCheckSerializer(serializers.Serializer):
    username = serializers.CharField()

    def validate_username(self, value):
        if not re.match(r"^[a-z][a-z0-9]{3,11}$", value):
            raise serializers.ValidationError(
                "아이디는 영문 소문자로 시작하며, 영문 소문자와 숫자만을 포함한 4~12자여야 합니다."
            )
        return value


# 비밀번호 변경 시 사용되는 시리얼라이저
class PasswordCheckSerializer(serializers.Serializer):
    new_password = serializers.CharField()

    # 새 비밀번호의 유효성 검사
    def validate_new_password(self, value):
        if len(value) < 8 or len(value) > 20:
            raise serializers.ValidationError(
                "비밀번호는 8자에서 20자 사이여야 합니다."
            )

        # 비밀번호는 대문자, 소문자, 숫자, 특수문자 중 2가지 이상 포함해야 함
        has_upper = re.search(r"[A-Z]", value) is not None
        has_lower = re.search(r"[a-z]", value) is not None
        has_digit = re.search(r"\d", value) is not None
        has_special = re.search(r"[!@#$%^&*]", value) is not None

        if sum([has_upper, has_lower, has_digit, has_special]) < 2:
            raise serializers.ValidationError(
                "비밀번호는 대문자, 소문자, 숫자, 특수문자 중 2가지 이상을 포함해야 합니다."
            )

        return value

# 서비스 요청 사항과 관련된 시리얼라이저
class RequestServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceRequest
        fields = ["id", "user", "title", "content", "file", "created_at"]

    # 데이터 검증
    def validate(self, data):
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        file = data.get("file", None)

        # 제목, 내용, 파일 중 하나라도 있어야 함
        if not title and not content and file is None:
            raise serializers.ValidationError("모두 빈칸일 수 없습니다.")

        return data

    # 업로드 파일에 대한 검증
    def validate_file(self, value):
        if value is None:
            return value  # 파일이 없으면 검증을 건너뜁니다.

        allowed_extensions = [
            "pdf",
            "docx",
            "doc",
            "txt",
            "xlsx",
            "xls",
            "csv",
            "hwp",
            "pptx",
            "ppt",
            "jpg",
            "jpeg",
            "png",
            "gif",
            "zip",
        ]
        max_file_size = 1000 * 1024 * 1024  # 1000MB

        # 압축 파일은 크기 제한을 다르게 설정
        if value.name.split(".")[-1].lower() in ["zip"]:
            max_file_size = 1000 * 1024 * 1024  # 1000MB

        # 파일 확장자 검사
        file_extension = value.name.split(".")[-1].lower()
        if file_extension not in allowed_extensions:
            raise serializers.ValidationError(
                f"허용되지 않는 파일 확장자입니다: {file_extension}. 허용된 확장자는 {', '.join(allowed_extensions)}입니다."
            )

        # 파일 크기 검사
        if value.size > max_file_size:
            raise serializers.ValidationError(
                f"파일 크기가 너무 큽니다. 최대 허용 크기는 {max_file_size // (1024 * 1024)}MB입니다."
            )

        return value


class MenuSerializer(serializers.ModelSerializer):
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    image = serializers.ImageField(required=False, allow_null=True, use_url=True)

    class Meta:
        model = Menu
        fields = [
            "menu_number",
            "name",
            "price",
            "category",
            "store",
            "image",
            "spicy",
            "allergy",
            "menu_introduction",
            "origin",
        ]

    def validate_image(self, value):
        if value is None:
            logger.debug("Image field is None")
            return None

        # 이미지 크기 제한 (예: 5MB 이하)
        if value.size > 5 * 1024 * 1024 * 1024:
            raise serializers.ValidationError(
                "이미지 파일 크기는 5MB를 초과할 수 없습니다."
            )

        # 지원하지 않는 형식 확인 (예: JPG, PNG만 허용)
        if not value.content_type in ["image/jpeg", "image/png"]:
            raise serializers.ValidationError(
                "JPEG 및 PNG 파일만 업로드할 수 있습니다."
            )

        logger.debug(f"Image field is valid with value: {value}")
        return value

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.image:
            # 이미지 URL에 MEDIA_URL을 추가하여 반환
            representation["image"] = f"{settings.MEDIA_URL}{instance.image}"
        return representation
