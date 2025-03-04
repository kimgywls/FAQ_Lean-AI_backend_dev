import os
import django

# Django 환경 설정 (Django 프로젝트의 settings.py를 로드)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "faq_backend.settings")  # 프로젝트명 변경 필요
django.setup()

from faq.models import Store
from django.utils.text import slugify

def duplicate_store_data():
    # store_id=11인 데이터 조회
    original_stores = Store.objects.filter(store_id=11)

    for store in original_stores:
        store.pk = None  # 기존 ID를 제거하여 새로운 객체로 만듦
        store.store_id = 27  # store_id 변경

        # store_name 중복 방지: 기존 store_name 뒤에 "-copy" 추가
        original_name = store.store_name
        new_name = f"{original_name}-copy"

        # 중복된 store_name이 있으면 숫자를 붙여 새로운 이름 생성
        counter = 1
        while Store.objects.filter(store_name=new_name).exists():
            new_name = f"{original_name}-copy-{counter}"
            counter += 1

        store.store_name = new_name  # 중복되지 않는 store_name 설정

        # slug 값 중복 방지
        original_slug = store.slug
        new_slug = f"{original_slug}-copy"

        counter = 1
        while Store.objects.filter(slug=new_slug).exists():
            new_slug = f"{original_slug}-copy-{counter}"
            counter += 1

        store.slug = new_slug  # 중복되지 않는 slug 설정
        store.save()  # 새 데이터 저장

    print("✅ store_id=11 데이터를 store_id=27로 변경하여 복사 완료!")

if __name__ == "__main__":
    duplicate_store_data()
