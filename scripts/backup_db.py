import os
import sys
from pathlib import Path
import django
from datetime import datetime
import shutil

# 현재 스크립트의 디렉토리 기준으로 Django 프로젝트 루트 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# Django 설정 초기화
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'faq_backend.settings')  # 'faq_backend'는 프로젝트 이름
django.setup()

from django.conf import settings

# 데이터베이스 백업
if not os.path.exists(settings.BACKUP_DIR):
    os.makedirs(settings.BACKUP_DIR)

backup_file = os.path.join(settings.BACKUP_DIR, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3")
shutil.copy(settings.DATABASE_PATH, backup_file)
print(f"백업이 완료되었습니다: {backup_file}")
