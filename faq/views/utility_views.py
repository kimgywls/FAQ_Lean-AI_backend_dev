# utility_views.py             
# QR 코드 생성,  통계 및 보고서 관련 처리, 기타 부가 기능
import logging, os, qrcode, zipfile
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from ..merged_csv import merge_csv_files
from ..excel_processor import process_excel_and_save_to_db
from ..analyze_utterances import get_most_common_utterances
from ..analyze_utterances import save_most_common_utterances_graph
from ..models import Store
from ..serializers import ( RequestServiceSerializer)

logger = logging.getLogger('faq')

# QR 코드 생성 하는 API
class GenerateQrCodeView(APIView):
    authentication_classes = [JWTAuthentication] 
    permission_classes = [IsAuthenticated]  # 인증된 사용자만 접근 가능

    def post(self, request):
        store_id = request.data.get('store_id')  # 요청에서 store_id 받기
        #logger.debug(f"Request data store_id: {store_id}")

        if not store_id:
            #logger.debug("No store_id provided in the request")
            return Response({'error': '스토어 ID가 필요합니다.'}, status=400)

        try:
            # 주어진 store_id로 스토어 정보 가져오기
            store = Store.objects.get(store_id=store_id, user=request.user)
            #logger.debug(f"Store found for store_id: {store_id}, user: {request.user.username}")
        except Store.DoesNotExist:
            #logger.debug(f"Store not found for store_id: {store_id}, user: {request.user.username}")
            return Response({'error': '스토어를 찾을 수 없습니다.'}, status=404)

        qr_url = f'https://mumulai.com/storeIntroduction/{store.slug}'
        #logger.debug(f"QR Content URL to encode: {qr_url}")

        try:
            # QR 코드 생성
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=4
            )
            qr.add_data(qr_url)
            qr.make(fit=True)

            # QR 코드 이미지 저장 경로 설정
            qr_filename = f'qr_{store_id}.png'
            qr_directory = os.path.join(settings.MEDIA_ROOT, 'qr_codes')
            qr_path = os.path.join(qr_directory, qr_filename)

            #logger.debug(f"QR code directory path: {qr_directory}")
            #logger.debug(f"QR code file path: {qr_path}")

            # 디렉토리가 없으면 생성
            if not os.path.exists(qr_directory):
                os.makedirs(qr_directory)
                #logger.debug(f"QR code directory created at: {qr_directory}")

            # QR 코드 이미지 저장
            img = qr.make_image(fill='black', back_color='white')
            img.save(qr_path)
            #logger.debug(f"QR code image saved at: {qr_path}")

            # 데이터베이스에 QR 코드 경로를 저장
            store.qr_code = f'{settings.MEDIA_URL}qr_codes/{qr_filename}'
            store.save()
            #logger.debug(f"Store updated with QR code path: {store.qr_code}")

            return Response({
                'message': 'QR 코드가 성공적으로 생성되었습니다.',
                'qr_code_url': request.build_absolute_uri(store.qr_code),  # 절대 URL 반환
                'qr_content_url': qr_url  # QR 코드에 인코딩된 실제 URL 반환
            }, status=201)
        except Exception as e:
            logger.error(f"Error while generating QR code: {e}")
            return Response({'error': '서버 내부 오류가 발생했습니다. 관리자에게 문의하세요.'}, status=500)


# QR 코드 이미지를 반환하는 API
class QrCodeImageView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            # 요청 데이터 로깅
            #logger.debug(f"Request data: {request.data}")
            #logger.debug(f"Request user: {request.user}")

            store_id = request.data.get('store_id')  # 요청에서 store_id 가져오기
            if not store_id:
                #logger.debug("store_id 요청에 없습니다.")
                return Response({'error': 'store_id 필요합니다.'}, status=400)

            # 사용자의 스토어 정보 가져오기
            store = Store.objects.get(store_id=store_id, user=request.user)
            #logger.debug(f"Public object found: {store}")

            if store.qr_code:
                store_name = store.store_name
                qr_code_path = store.qr_code.lstrip('/')  # 경로에서 앞의 '/' 제거
                #logger.debug(f"QR code path: {qr_code_path}")

                if qr_code_path.startswith('media/'):
                    qr_code_url = request.build_absolute_uri(f'/{qr_code_path}')
                else:
                    qr_code_url = request.build_absolute_uri(settings.MEDIA_URL + qr_code_path)

                qr_content_url = f'https://mumulai.com/storeIntroduction/{store.slug}'

                #logger.debug(f"QR code URL: {qr_code_url}, Content URL: {qr_content_url}")

                return Response({
                    'store_name': store_name,
                    'qr_code_image_url': qr_code_url,
                    'qr_content_url': qr_content_url
                }, status=200)
            else:
                #logger.debug("QR 코드가 없습니다.")
                return Response({'qr_code_image_url': None}, status=200)

        except Store.DoesNotExist:
            #logger.debug(f"store object not found for store_id: {store_id}, user: {request.user}")
            return Response({'error': 'store not found'}, status=404)
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)  # 예외 정보 전체 로깅
            return Response({'error': 'An unexpected error occurred.'}, status=500)
        

# FAQ 통계 API
class StatisticsView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]  # 인증된 사용자만 접근 가능

    def post(self, request, *args, **kwargs):
        try:
            # 사용자별 CSV 파일 폴더 경로 지정
            folder_path = f'conversation_history/{request.user.user_id}'

            # 사용자 폴더가 존재하는지 확인
            if not os.path.exists(folder_path):
                logger.debug(f"{folder_path} 경로가 존재하지 않습니다.")
                return Response({"status": "no folder", "message": "사용자 데이터 폴더가 존재하지 않습니다."})
            
            # CSV 파일 병합 함수 호출
            merged_file_path = merge_csv_files(folder_path)
            
            # 병합된 파일이 없으면 파일 없음 메시지 반환
            if not merged_file_path or not os.path.exists(merged_file_path):
                logger.debug("병합된 파일이 존재하지 않습니다.")
                return Response({"status": "no file", "message": "해당 파일이 존재하지 않습니다."}, status=status.HTTP_404_NOT_FOUND)
            
            # 가장 많이 언급된 user_utterances 가져오기
            most_common_utterances = get_most_common_utterances(merged_file_path)
            
            # 이미지 저장 경로 설정
            statistics_folder = f'media/statistics/{request.user.user_id}'
            os.makedirs(statistics_folder, exist_ok=True)  # 폴더 생성
            output_image_path = os.path.join(statistics_folder, 'most_common_utterances.png')
            
            # 그래프 이미지 생성 및 저장
            save_most_common_utterances_graph(most_common_utterances, output_image_path)
            
            # 이미지 URL을 응답에 포함
            response_data = {
                "status": "success",
                "data": most_common_utterances,
                "image_url": f"/media/statistics/{request.user.user_id}/most_common_utterances.png"
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            # 오류 메시지 로그 출력
            logger.error(f"오류 발생: {str(e)}")
            return Response({"status": "error", "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# 데이터 등록 API
class RegisterDataView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        files = request.FILES.getlist('files') if 'files' in request.FILES else []

        if not files:
            return Response({"error": "업로드할 파일을 선택해주세요."}, status=status.HTTP_400_BAD_REQUEST)

        results = []
        allowed_extensions = {
            "excel": [".xlsx", ".xls"],
            "image": [".png", ".jpg", ".jpeg"],
            "zip": [".zip"]
        }

        for file in files:
            try:
                # 파일을 임시 저장할 경로 설정
                temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_uploads')
                os.makedirs(temp_dir, exist_ok=True)
                file_path = os.path.join(temp_dir, file.name)

                # 파일 저장
                with open(file_path, 'wb') as temp_file:
                    for chunk in file.chunks():
                        temp_file.write(chunk)

                # Store ID 가져오기
                store_id = request.user.stores.first().store_id
                created_at = timezone.now().strftime('%Y-%m-%d %H:%M')

                # 파일 확장자 확인
                ext = os.path.splitext(file.name)[1].lower()

                if ext in allowed_extensions["excel"]:
                    # 엑셀 파일 처리
                    result = process_excel_and_save_to_db(file_path, store_id) or {}
                    results.append({"file": file.name, "status": "success", "message": "엑셀 파일이 성공적으로 처리되었습니다.", **result})

                elif ext in allowed_extensions["image"]:
                    # 이미지 파일 단순 저장
                    results.append({"file": file.name, "status": "success", "message": "이미지 파일이 성공적으로 업로드되었습니다."})

                elif ext in allowed_extensions["zip"]:
                    # ZIP 파일 처리
                    zip_extract_path = os.path.join(temp_dir, os.path.splitext(file.name)[0])
                    os.makedirs(zip_extract_path, exist_ok=True)

                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        zip_ref.extractall(zip_extract_path)

                    extracted_files = os.listdir(zip_extract_path)
                    results.append({"file": file.name, "status": "success", "message": f"ZIP 파일이 성공적으로 해제되었습니다. 포함된 파일: {', '.join(extracted_files)}"})

                else:
                    # 지원되지 않는 파일 형식
                    results.append({"file": file.name, "status": "error", "message": "지원되지 않는 파일 형식입니다."})

            except zipfile.BadZipFile:
                logger.error(f"파일 손상: {file.name}")
                results.append({"file": file.name, "status": "error", "message": "ZIP 파일이 손상되어 압축을 해제할 수 없습니다."})

            except Exception as e:
                logger.error(f"파일 처리 중 오류 발생: {file.name}, 오류: {e}")
                results.append({"file": file.name, "status": "error", "message": f"파일을 처리하는 동안 문제가 발생했습니다. \n 다시 시도하거나 관리자에게 문의하세요."})

        return Response({"results": results}, status=status.HTTP_200_OK)




# 사용자 서비스 요청 API
class RequestServiceView(APIView):
    # 이 뷰는 로그인된 사용자만 접근 가능하도록 설정
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        #logger.debug(f"Request data: {request.data}")
        #logger.debug(f"Request files: {request.FILES}")

        files = request.FILES.getlist('files') if 'files' in request.FILES else []

        # 제목, 내용 또는 파일 중 하나는 있어야 함
        if not request.data.get('title') and not request.data.get('content') and not files:
            return Response({"error": "제목, 내용 또는 파일 중 하나는 반드시 입력해야 합니다."}, status=status.HTTP_400_BAD_REQUEST)

        # 여러 파일을 처리하기 위한 빈 리스트 준비
        saved_data = []

        # 클라이언트에서 전달받은 데이터를 사용하여 'ServiceRequest' 객체를 생성
        if files:
            for file in files:
                data = {
                    'user': request.user.user_id,
                    'title': request.data.get('title', ''),
                    'content': request.data.get('content', ''),
                    'file': file  # 각각의 파일을 데이터에 추가
                }

                req_ser_serializer = RequestServiceSerializer(data=data)
                
                if req_ser_serializer.is_valid():
                    req_ser_serializer.save()
                    saved_data.append(req_ser_serializer.data)
                else:
                    #logger.debug(f"에러 메시지 : {req_ser_serializer.errors}")
                    return Response(req_ser_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        else:
            # 파일이 없을 경우, 제목과 내용만 처리
            data = {
                'user': request.user.user_id,
                'title': request.data.get('title', ''),
                'content': request.data.get('content', '')
            }

            req_ser_serializer = RequestServiceSerializer(data=data)

            if req_ser_serializer.is_valid():
                req_ser_serializer.save()
                saved_data.append(req_ser_serializer.data)
            else:
                #logger.debug(f"에러 메시지 : {req_ser_serializer.errors}")
                return Response(req_ser_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        return Response(saved_data, status=status.HTTP_201_CREATED)

