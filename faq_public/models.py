from django.db import models, IntegrityError
from django.utils.text import slugify
from django.conf import settings
from django.utils import timezone
import os, logging
from datetime import date
from dateutil.relativedelta import relativedelta

# User 모델을 관리하는 매니저 클래스 및 커스텀 User 모델
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager

logger = logging.getLogger("faq")


def profile_photo_upload_path(instance, filename):
    public_id = instance.public.public_id if instance.public else "default"
    return os.path.join(f"profile_photos/public_{public_id}", filename)


def logo_upload_path(instance, filename):
    return os.path.join(f"banners/public_{instance.public_id}", filename)


# 경로 생성 함수를 정의
def user_directory_path(instance, filename):
    return os.path.join(f"uploads/public_{instance.user.public.public_id}", filename)


# 기존 UserManager, User, Store 모델은 그대로 유지
class PublicUserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("사용자 이름은 필수입니다.")
        user = self.model(username=username, **extra_fields)
        user.set_password(password)  # 비밀번호 해싱
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        return self.create_user(username, password, **extra_fields)


class Public_User(AbstractBaseUser):
    class Meta:
        app_label = "faq_public"  # 라우터가 이 모델을 faq_public DB에서 사용하도록 설정

    user_id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=20, blank=True, null=True)
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

    # 각 Public_User가 하나의 Public 기관에만 연결되도록 ForeignKey 필드 추가
    public = models.ForeignKey(
        "Public",
        on_delete=models.CASCADE,
        related_name="public_users",
        null=True,
        blank=True,
    )

    department = models.ForeignKey(
        "Public_Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="public_users",
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)

    objects = PublicUserManager()

    USERNAME_FIELD = "username"

    # 탈퇴 시 비활성화
    def deactivate(self):
        self.is_active = False
        self.save()

    def __str__(self):
        return self.username


class DeviceToken(models.Model):
    user = models.ForeignKey(Public_User, on_delete=models.CASCADE)
    device_id = models.CharField(max_length=255)
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Public(models.Model):
    public_id = models.AutoField(primary_key=True)
    public_name = models.CharField(max_length=20, unique=True)
    public_address = models.TextField(blank=True, null=True)
    public_tel = models.TextField(blank=True, null=True)
    logo = models.ImageField(upload_to=logo_upload_path, blank=True, null=True)
    opening_hours = models.TextField(blank=True, null=True)
    qr_code = models.CharField(max_length=100, blank=True, null=True)
    agent_id = models.CharField(max_length=100, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, blank=True, null=True)
    slug = models.SlugField(max_length=255, unique=True)
    billing_key = models.OneToOneField(
        "Public_BillingKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="public_billing_key",
    )

    class Meta:
        app_label = "faq_public"

    def save(self, *args, **kwargs):
        # 객체가 새로 생성될 때 slug를 생성
        if not self.slug:
            base_slug = slugify(self.public_name, allow_unicode=True)
            slug = base_slug
            counter = 1
            while Public.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug

        # '기타' 부서를 자동으로 추가
        is_new = self.pk is None
        super().save(*args, **kwargs)  # 먼저 Public 객체 저장

        if is_new:
            # '기타' 부서를 생성할 때 중복 확인을 강화
            try:
                Public_Department.objects.get_or_create(
                    department_name="기타", public=self
                )
            except IntegrityError:
                # 이미 같은 부서 이름과 public 조합이 존재하는 경우
                logger.debug(
                    f"'기타' 부서는 이미 {self.public_name} 공공기관에 존재합니다."
                )

    def __str__(self):
        return self.public_name


class Public_Department(models.Model):
    department_id = models.AutoField(primary_key=True)
    department_name = models.CharField(max_length=100)  # 부서명
    public = models.ForeignKey(
        "Public", on_delete=models.CASCADE, related_name="departments"
    )  # Public과의 관계

    class Meta:
        app_label = "faq_public"
        unique_together = (
            "department_name",
            "public",
        )  # department_name과 public의 조합이 고유해야 함

    def __str__(self):
        return f"{self.department_name} ({self.public.public_name})"


class Public_ServiceRequest(models.Model):
    user = models.ForeignKey(
        Public_User, on_delete=models.CASCADE, related_name="public_ServiceRequests"
    )  # 요청을 보낸 사용자
    title = models.CharField(max_length=255, null=True, blank=True)
    content = models.TextField(null=True, blank=True)
    file = models.FileField(upload_to=user_directory_path, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    class Meta:
        app_label = "faq_public"  # 라우터가 이 모델을 faq_public DB에서 사용하도록 설정

    def __str__(self):
        return self.title


class Public_Complaint(models.Model):
    STATUS_CHOICES = [("접수", "접수"), ("처리 중", "처리 중"), ("완료", "완료")]

    complaint_id = models.AutoField(primary_key=True)
    complaint_number = models.CharField(max_length=20, unique=True)
    public = models.ForeignKey(
        Public, on_delete=models.CASCADE, related_name="complaints"
    )
    department = models.ForeignKey(
        "Public_Department",
        on_delete=models.CASCADE,
        null=False,
        blank=False,
        related_name="complaints",
    )
    name = models.CharField(max_length=100)
    birth_date = models.CharField(max_length=6)  # YYMMDD 형식의 생년월일
    phone = models.CharField(max_length=15)
    email = models.EmailField()
    title = models.CharField(max_length=255)
    content = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="접수")
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    answer = models.TextField(blank=True, null=True)

    class Meta:
        app_label = "faq_public"

    def __str__(self):
        return f"{self.complaint_number} - {self.title}"

    def save(self, *args, **kwargs):
        if not self.complaint_number:
            today = timezone.now().strftime("%Y%m%d")
            last_complaint = (
                Public_Complaint.objects.filter(complaint_number__startswith=today)
                .order_by("-complaint_number")
                .first()
            )

            if last_complaint:
                last_number = int(last_complaint.complaint_number.split("-")[1])
                new_number = str(last_number + 1).zfill(3)
            else:
                new_number = "001"

            self.complaint_number = f"{today}-{new_number}"

        super().save(*args, **kwargs)


# ✅ **구독 모델**
class Public_Subscription(models.Model):
    PLAN_CHOICES = [
        ("BASIC", "Basic Plan"),
        ("ENTERPRISE", "Enterprise Plan"),
    ]

    public = models.OneToOneField(
        "Public", on_delete=models.CASCADE, related_name="public_subscription"
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
        "Public_BillingKey",
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
class Public_BillingKey(models.Model):
    public = models.OneToOneField(
        "Public", on_delete=models.CASCADE, related_name="public_billing"
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
class Public_PaymentHistory(models.Model):
    public = models.ForeignKey(
        "Public", on_delete=models.CASCADE, related_name="public_payment_history"
    )
    billing_key = models.ForeignKey("Public_BillingKey", on_delete=models.SET_NULL, null=True)
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
