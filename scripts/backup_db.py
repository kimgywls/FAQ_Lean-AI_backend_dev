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

# 데이터베이스 백업 경로 설정
if not os.path.exists(settings.BACKUP_DIR):
    os.makedirs(settings.BACKUP_DIR)

# 설정된 데이터베이스들을 순회하면서 백업
for db_alias, db_config in settings.DATABASES.items():
    if 'NAME' in db_config:  # 데이터베이스 경로 확인
        db_path = db_config['NAME']
        db_filename = os.path.basename(db_path)  # 예: faq.sqlite3 또는 faq_public.sqlite3
        backup_filename = f"{db_filename}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
        backup_file = os.path.join(settings.BACKUP_DIR, backup_filename)
        
        # 데이터베이스 파일 복사 (백업)
        shutil.copy(db_path, backup_file)
        print(f"백업이 완료되었습니다: {backup_file}")
