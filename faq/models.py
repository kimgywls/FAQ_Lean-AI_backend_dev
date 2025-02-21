from django.db import models
from django.utils.text import slugify
from django.conf import settings
import os, uuid, json
from datetime import date
from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager


# ✅ **파일 저장 경로 설정 함수**
def user_directory_path(instance, filename):
    return os.path.join(
        f"uploads/store_{instance.user.stores.first().store_id}", filename
    )


def profile_photo_upload_path(instance, filename):
    store_id = (
        instance.stores.first().store_id if instance.stores.exists() else "default"
    )
    return os.path.join(f"profile_photos/store_{store_id}", filename)


def banner_upload_path(instance, filename):
    return os.path.join(f"banners/store_{instance.store_id}", filename)


def menu_image_upload_path(instance, filename):
    return os.path.join(f"menu_images/store_{instance.store.store_id}", filename)


# ✅ **유저 모델**
class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("사용자 이름은 필수입니다.")
        user = self.model(username=username, **extra_fields)
        if password:  # 소셜 로그인 사용자의 경우 password 없이 생성 가능
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(username, password, **extra_fields)


class User(AbstractBaseUser):
    user_id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=20, blank=True, null=True)
    password = models.CharField(max_length=20, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    phone = models.CharField(max_length=20, unique=True)
    email = models.EmailField(max_length=30, blank=True, null=True)
    profile_photo = models.ImageField(
        upload_to=profile_photo_upload_path, blank=True, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    marketing = models.CharField(
        max_length=1, choices=[("Y", "Yes"), ("N", "No")], default="N"
    )
    push_token = models.CharField(max_length=255, null=True, blank=True)

    billing_key = models.OneToOneField(
        "BillingKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_billing_key",
    )

    is_deactivation_requested = models.BooleanField(default=False)  # 탈퇴 요청 여부
    deactivated_at = models.DateTimeField(null=True, blank=True)    # 탈퇴 완료된 시간간
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    def deactivate(self):
        self.is_active = False
        self.save()

    def __str__(self):
        return self.username


# ✅ **스토어 모델**
class Store(models.Model):
    STORE_CATEGORIES = [
        ("FOOD", "음식점"),
        ("RETAIL", "판매점"),
        ("UNMANNED", "무인매장"),
        ("PUBLIC", "공공기관"),
        ("OTHER", "기타"),
    ]

    store_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stores"
    )
    store_name = models.CharField(max_length=20, unique=True)
    store_address = models.TextField(blank=True, null=True)
    store_tel = models.TextField(blank=True, null=True)
    banner = models.ImageField(upload_to=banner_upload_path, blank=True, null=True)
    menu_price = models.TextField(blank=True, null=True)
    opening_hours = models.TextField(blank=True, null=True)
    qr_code = models.CharField(max_length=100, blank=True, null=True)
    agent_id = models.CharField(max_length=100, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    slug = models.SlugField(max_length=255, unique=True)
    store_category = models.CharField(
        max_length=50, choices=STORE_CATEGORIES, default="FOOD"
    )
    store_introduction = models.TextField(blank=True, null=True)
    store_information = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.store_name, allow_unicode=True)
            slug = base_slug
            counter = 1
            while Store.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug

        if isinstance(self.menu_price, list):
            self.menu_price = json.dumps(self.menu_price)
        super(Store, self).save(*args, **kwargs)

    def __str__(self):
        return self.store_name


class ServiceRequest(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ServiceRequests"
    )  # 요청을 보낸 사용자
    title = models.CharField(max_length=255, null=True, blank=True)
    content = models.TextField(null=True, blank=True)
    file = models.FileField(upload_to=user_directory_path, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def __str__(self):
        return self.title


class Menu(models.Model):
    SPICY_CATEGORIES = [
        ("0", "매운맛이 없는 음식"),
        ("1", "초보 (진라면 순한맛 맵기)"),
        ("2", "하수 (진라면 매운맛 맵기)"),
        ("3", "중수 (신라면 맵기)"),
        ("4", "고수 (불닭볶음면 맵기)"),
        ("5", "신 (핵불닭볶음면 맵기)"),
    ]

    store = models.ForeignKey(Store, related_name="menus", on_delete=models.CASCADE)
    menu_number = models.AutoField(
        primary_key=True
    )  # 기본 키로 전역적으로 유일한 값을 할당
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=100)
    image = models.ImageField(upload_to=menu_image_upload_path, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, blank=True, null=True)
    spicy = models.CharField(max_length=50, choices=SPICY_CATEGORIES, default="0")
    allergy = models.TextField(null=True, blank=True)
    menu_introduction = models.TextField(blank=True, null=True)
    origin = models.TextField(blank=True, null=True)


# ✅ **구독 모델**
class Subscription(models.Model):
    PLAN_CHOICES = [
        ("BASIC", "Basic Plan"),
        ("ENTERPRISE", "Enterprise Plan"),
    ]

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.CharField(
        max_length=20, choices=PLAN_CHOICES, default="BASIC"
    )  # 사용자의 구독 플랜을 관리
    is_active = models.BooleanField(default=True)  # 구독 상태
    next_billing_date = models.DateField(
        default=date.today() + relativedelta(months=1)
    )  # 다음 결제 날짜

    # BillingKey 모델을 참조하여 결제 정보 관리
    billing_key = models.OneToOneField(
        "BillingKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscription_billing",
    )

    def deactivate(self):
        """구독을 비활성화"""
        self.is_active = False
        self.save()

    def __str__(self):
        return f"{self.user.username} - {self.plan} ({'Active' if self.is_active else 'Inactive'})"


# ✅ **빌링키 모델**
class BillingKey(models.Model):
    user = models.OneToOneField(
        "User", on_delete=models.CASCADE, related_name="billing"
    )
    customer_uid = models.CharField(
        max_length=255, unique=True
    )  # PortOne에서 발급한 고유 사용자 결제 키
    imp_uid = models.CharField(
        max_length=255, blank=True, null=True
    )  # 포트원 결제 API에서 사용되는 결제 고유 ID
    merchant_uid = models.CharField(max_length=255, blank=True, null=True)

    plan = models.CharField(max_length=50)  # 현재 결제된 플랜 정보 저장
    amount = models.DecimalField(max_digits=10, decimal_places=2)  # 결제 금액
    created_at = models.DateTimeField(auto_now_add=True)
    subscription_cycle = models.PositiveIntegerField(default=1)  # 기본 1개월 단위 결제
    is_active = models.BooleanField(default=True)
    deactivation_date = models.DateField(null=True, blank=True)  # 비활성화 날짜

    def change_card(self, new_customer_uid):
        """카드 정보를 변경 (customer_uid 갱신)"""
        self.customer_uid = new_customer_uid
        self.save()

    def deactivate(self):
        """결제 비활성화"""
        self.is_active = False
        self.save()

    def __str__(self):
        return f"{self.user.username} - {self.customer_uid}"


# ✅ **결제 기록 모델**
class PaymentHistory(models.Model):
    user = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="payment_history"
    )
    billing_key = models.ForeignKey("BillingKey", on_delete=models.SET_NULL, null=True)
    imp_uid = models.CharField(max_length=255)
    merchant_uid = models.CharField(max_length=255, unique=True)
    merchant_name = models.CharField(max_length=255, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=50)
    created_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.imp_uid} ({self.status})"

    class Meta:
        ordering = ["-created_at"]


