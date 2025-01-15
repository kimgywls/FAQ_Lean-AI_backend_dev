# webhook/process_qa.py

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'faq_backend.settings')
django.setup()

from webhook.utils import load_qa_pairs_from_excel, process_qa_pairs, load_and_vectorize_documents

def main():
    agent_id = "a7078b63-d073-4ebb-a48a-60f9618d4eb3"  # 음식점 챗봇의 에이전트 ID로 변경하세요
    store_name = "계림원 구로구청점"
    # 엑셀 파일에서 질문-답변 쌍 로드
    qa_pairs = load_qa_pairs_from_excel('/home/lean-ai/FAQ_PJ/backend/rag_faq_files/QNA_계림원_구로구청_1차.xlsx')

    # 질문-답변 데이터를 저장
    process_qa_pairs(qa_pairs, agent_id, store_name)
    print(f"{store_name}의 QA 데이터가 성공적으로 저장되었습니다.")

    # 벡터스토어 생성
    vectorstore = load_and_vectorize_documents(agent_id)
    if vectorstore:
        print("벡터스토어가 성공적으로 생성되었습니다.")
    else:
        print("벡터스토어 생성에 실패했습니다.")

if __name__ == "__main__":
    main()
