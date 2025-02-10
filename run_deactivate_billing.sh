#!/bin/bash

# ✅ 로그 파일 설정
LOG_FILE="/home/hjkim0213/dev/FAQ_Lean-AI_backend_dev/logs/deactivate_billing.log"

# ✅ 가상 환경 활성화
source /home/hjkim0213/dev/FAQ_Lean-AI_backend_dev/venv/bin/activate

# ✅ Django 환경 변수 설정
export DJANGO_SETTINGS_MODULE=faq_backend.settings
export PYTHONPATH="/home/hjkim0213/dev/FAQ_Lean-AI_backend_dev"

# ✅ 실행 로그 기록
echo "[$(date)] 크론 실행 시작" >> "$LOG_FILE"

# ✅ BillingKey 비활성화 스크립트 실행
/home/hjkim0213/dev/FAQ_Lean-AI_backend_dev/venv/bin/python /home/hjkim0213/dev/FAQ_Lean-AI_backend_dev/faq/deactivate_billing.py >> "$LOG_FILE" 2>&1

# ✅ 크론 실행 완료 로그 기록
echo "[$(date)] 크론 실행 완료" >> "$LOG_FILE"
