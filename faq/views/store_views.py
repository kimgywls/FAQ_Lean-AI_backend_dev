# store_views.py
# 매장 및 피드 관리
from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
import logging, os, uuid, json
from django.conf import settings
from django.shortcuts import get_object_or_404
from urllib.parse import unquote
from ..models import Store
from ..serializers import StoreSerializer

logger = logging.getLogger('faq')


class StoreViewSet(ViewSet):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    
    def list(self, request):
        """
        사용자가 가진 모든 매장을 반환하는 API
        """
        print(f"🔍 [DEBUG] 요청한 유저: {request.user}")
        
        if not request.user or request.user.is_anonymous:
            return Response({"error": "인증된 사용자만 접근할 수 있습니다."}, status=status.HTTP_401_UNAUTHORIZED)

        stores = Store.objects.filter(user=request.user)
        if not stores.exists():
            return Response({"error": "등록된 매장이 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        store_data = StoreSerializer(stores, many=True).data
        return Response({"stores": store_data}, status=status.HTTP_200_OK)
    

    def retrieve(self, request, pk=None):
        """
        단일 매장 정보 조회
        """
        print("🔹 [DEBUG] request.user:", request.user)
        try:
            store = Store.objects.get(store_id=pk, user=request.user)
            store_data = StoreSerializer(store).data
            return Response({'store': store_data}, status=status.HTTP_200_OK)
        except Store.DoesNotExist:
            return Response({'error': '해당 매장 정보를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
        

    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def detail_by_slug(self, request):
        """
        특정 매장(slug 기반) 출력 API
        """
        slug_param = request.query_params.get('slug')
        if not slug_param:
            return Response({"error": "slug(또는 이름) 파라미터가 필요합니다."}, status=400)
        
        # URL에 한글이나 공백이 있을 수 있으므로 디코딩
        decoded_param = unquote(slug_param)

        # 1) store_name으로 먼저 검색
        store = Store.objects.filter(store_name=decoded_param).first()
        # 2) 없으면 slug로 검색
        if not store:
            store = Store.objects.filter(slug=decoded_param).first()

        if not store:
            return Response({"error": "해당 매장을 찾을 수 없습니다."}, status=404)

        serializer = StoreSerializer(store)
        return Response(serializer.data, status=200)
        

    def update(self, request, pk=None):
        """
        매장 정보 수정
        """
        try:
            store = Store.objects.get(store_id=pk, user=request.user)  # pk를 store_id로 사용
        except Store.DoesNotExist:
            return Response({'error': '스토어를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data.copy()
        if 'banner' in data and data['banner'] == '':
            data['banner'] = None

        serializer = StoreSerializer(store, data=data, partial=True)
        if serializer.is_valid():
            store = serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    
    @action(detail=False, methods=['post'])
    def add(self, request):
        '''
        새 스토어 추가
        '''
        data = request.data
        
        # 필수 필드 확인
        required_fields = ["store_category", "store_name", "store_address"]
        for field in required_fields:
            if field not in data or not data[field].strip():
                return Response({"detail": f"{field} 필드는 필수입니다."}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            store = Store.objects.create(
                store_category=data["store_category"],
                store_name=data["store_name"],
                store_address=data["store_address"],
                user = request.user 
            )
            serializer = StoreSerializer(store)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    
    def destroy(self, request, pk=None):
        """
        특정 스토어 삭제 API
        DELETE /api/stores/delete/<store_id>/
        """
        store = get_object_or_404(Store, pk=pk)

        # 요청자가 해당 스토어의 소유자인지 확인 (예제: store.owner 필드 체크)
        if store.user != request.user:
            return Response(
                {"detail": "해당 스토어를 삭제할 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN
            )

        delete_reason = request.data.get("delete_reason", "사용자에 의한 삭제")

        # 삭제 수행 (소프트 삭제 적용 가능)
        store.delete()

        return Response(
            {"detail": f"스토어 '{store.store_name}'가 삭제되었습니다.", "delete_reason": delete_reason},
            status=status.HTTP_204_NO_CONTENT
        )




class FeedViewSet(ViewSet):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def list_images(self, request):
        """
        피드 이미지 목록 조회
        """
        # 디버깅: 요청 쿼리 파라미터 확인
        logger.debug(f"Received request query params: {request.query_params}")

        store_id = request.query_params.get('store_id')  # request.data -> request.query_params
        logger.debug(f"Parsed slug: {store_id}")

        try:
            if store_id:
                store = Store.objects.get(store_id=store_id)
                logger.debug(f"Store found by store_id. Store ID: {store_id}")
            else:
                logger.error("Either slug or store_id must be provided.")
                return Response({'error': 'store_id 중 하나가 필요합니다.'}, status=status.HTTP_400_BAD_REQUEST)

            feed_dir = os.path.join(settings.MEDIA_ROOT, f"uploads/store_{store_id}/feed")
            logger.debug(f"Feed directory path: {feed_dir}")

            if not os.path.exists(feed_dir):
                logger.info(f"Feed directory does not exist. Creating directory: {feed_dir}")
                os.makedirs(feed_dir, exist_ok=True)

            files = os.listdir(feed_dir)
            logger.debug(f"Files found in feed directory: {files}")

            image_files = [
                {
                    'id': os.path.splitext(file)[0].rsplit('_', 1)[-1],
                    'name': os.path.splitext(file)[0].rsplit('_', 1)[0],
                    'ext' : os.path.splitext(file)[1],
                    'path': os.path.join('uploads', f'store_{store_id}/feed', file).replace("\\", "/")
                }
                for file in files if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))
            ]
            logger.debug(f"Image files to return: {image_files}")

            return Response({'images': image_files}, status=status.HTTP_200_OK)
        except Store.DoesNotExist:
            logger.error("Store does not exist for provided slug or store_id.")
            return Response({'error': '스토어를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.exception(f"Unexpected error occurred: {e}")
            return Response({'error': '알 수 없는 오류가 발생했습니다.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        


    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def list_images_by_slug(self, request):
        """
        특정 매장(가게명 혹은 slug 기반)의 피드 출력 API
        - 예: /api/store/list_images_by_slug?slug=무물 떡볶이
        """
        slug_or_name = request.query_params.get('slug')
        if not slug_or_name:
            return Response({"error": "slug(또는 가게명) 파라미터가 필요합니다."}, status=400)

        # 한글 혹은 공백이 들어간 경우를 위해 디코딩
        decoded_param = unquote(slug_or_name)

        try:
            # 1) store_name으로 먼저 검색
            store = Store.objects.filter(store_name=decoded_param).first()
            # 2) 없으면 slug로 검색
            if not store:
                store = Store.objects.filter(slug=decoded_param).first()

            if not store:
                logger.error("Store does not exist for provided store_name or slug.")
                return Response({'error': '스토어를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

            store_id = store.store_id
            logger.debug(f"Store found. Store ID: {store_id}")

            feed_dir = os.path.join(settings.MEDIA_ROOT, f"uploads/store_{store_id}/feed")
            logger.debug(f"Feed directory path: {feed_dir}")

            # 폴더가 없으면 생성
            if not os.path.exists(feed_dir):
                logger.info(f"Feed directory does not exist. Creating directory: {feed_dir}")
                os.makedirs(feed_dir, exist_ok=True)

            files = os.listdir(feed_dir)
            logger.debug(f"Files found in feed directory: {files}")

            image_files = [
                {
                    'id': os.path.splitext(file)[0].rsplit('_', 1)[-1],
                    'name': os.path.splitext(file)[0].rsplit('_', 1)[0],
                    'ext': os.path.splitext(file)[1],
                    'path': os.path.join('uploads', f'store_{store_id}/feed', file).replace("\\", "/")
                }
                for file in files
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))
            ]
            logger.debug(f"Image files to return: {image_files}")

            return Response({'images': image_files}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(f"Unexpected error occurred: {e}")
            return Response({'error': '알 수 없는 오류가 발생했습니다.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

    @action(detail=False, methods=['post'])
    def upload_image(self, request):
        """
        피드 이미지 업로드
        """
        store_id = request.data.get('store_id')
        if not store_id:
            return Response({'error': 'store_id가 없습니다.'}, status=status.HTTP_400_BAD_REQUEST)

        upload_dir = os.path.join(settings.MEDIA_ROOT, f"uploads/store_{store_id}/feed")
        os.makedirs(upload_dir, exist_ok=True)

        file = request.FILES.get('file')
        if not file:
            return Response({'error': '파일이 없습니다.'}, status=status.HTTP_400_BAD_REQUEST)

        unique_filename = f"{os.path.splitext(file.name)[0]}_{uuid.uuid4()}{os.path.splitext(file.name)[1]}"
        file_path = os.path.join(upload_dir, unique_filename)

        with open(file_path, 'wb+') as destination:
            for chunk in file.chunks():
                destination.write(chunk)

        relative_path = os.path.relpath(file_path, settings.MEDIA_ROOT).replace("\\", "/")
        return Response({
            'success': True,
            'file_path': relative_path,
            'stored_name': unique_filename
        }, status=status.HTTP_201_CREATED)
    


    @action(detail=False, methods=['delete'])
    def delete_image(self, request):
        """
        피드 이미지 삭제
        """
        image_id = request.data.get('id')
        store_id = request.data.get('store_id')

        if not image_id or not store_id:
            return Response({'error': 'id와 store_id는 필수입니다.'}, status=status.HTTP_400_BAD_REQUEST)

        file_path = os.path.join(settings.MEDIA_ROOT, f"uploads/store_{store_id}/feed/{image_id}")
        if os.path.exists(file_path):
            os.remove(file_path)
            return Response({'success': True}, status=status.HTTP_200_OK)
        return Response({'error': '이미지를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
    


    @action(detail=False, methods=['put'])
    def rename_image(self, request):
        logger.debug(f"Received request data for rename_image: {request.data}")

        image_id = request.data.get('id')
        new_name = request.data.get('name')
        store_id = request.data.get('store_id')

        if not image_id or not new_name or not store_id:
            logger.error("Missing required parameters.")
            return Response({'error': 'id, name, store_id는 필수입니다.'}, status=status.HTTP_400_BAD_REQUEST)

        base_dir = os.path.join(settings.MEDIA_ROOT, f"uploads/store_{store_id}/feed")
        old_file_path = os.path.join(base_dir, image_id)
        if not os.path.exists(old_file_path):
            logger.error(f"File not found: {old_file_path}")
            return Response({'error': '파일을 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

        new_file_name = f"{new_name}_{uuid.uuid4()}{os.path.splitext(image_id)[1]}"
        new_file_path = os.path.join(base_dir, new_file_name)
        os.rename(old_file_path, new_file_path)

        logger.debug(f"File renamed to: {new_file_name}")
        return Response({'success': True, 'new_name': new_file_name}, status=status.HTTP_200_OK)
    
    
        

