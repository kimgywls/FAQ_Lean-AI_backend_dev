# corp_views.py
# 기업 및 부서 관리
from ..authentication import CorpUserJWTAuthentication
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action
import logging
from ..models import Corp, Corp_Department
from ..serializers import (
    CorpUserSerializer, 
    CorpSerializer, 
    CorpRegisterSerializer,
    CorpDepartmentSerializer
)

# 디버깅을 위한 로거 설정
logger = logging.getLogger('faq')


# 기업
class CorpViewSet(ViewSet):

    """
    기업 관련 CRUD를 처리하는 ViewSet
    """
    def list(self, request):
        """
        모든 기업 출력 API
        """
        corp_list = Corp.objects.all()
        serializer = CorpSerializer(corp_list, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        """
        선택한 기업 정보 출력 API
        """
        try:
            corp = Corp.objects.get(corp_id=pk)
            serializer = CorpSerializer(corp)
            return Response(serializer.data)
        except Corp.DoesNotExist:
            return Response({"error": "해당 기업을 찾을 수 없습니다."}, status=404)
        
        

    def create(self, request):
        """
        기업 생성 API
        """
        logger.debug("Received request data: %s", request.data)
        logger.debug("Received files: %s", request.FILES)

        logo_file = request.FILES.get('logo', None)
        serializer = CorpRegisterSerializer(data=request.data)

        if serializer.is_valid():
            logger.debug("Serializer validated data: %s", serializer.validated_data)
            try:
                corp_instance = serializer.save()

                if logo_file:
                    logger.debug("Saving logo file: %s", logo_file.name)
                    corp_instance.logo = logo_file
                    corp_instance.save()

                logger.debug("Corp instance created: %s", corp_instance)
                return Response(
                    {
                        "status": "success",
                        "message": "기업 정보가 성공적으로 등록되었습니다.",
                        "data": CorpSerializer(corp_instance).data,
                    },
                    status=201,
                )
            except Exception as e:
                logger.error("Error while creating corp instance: %s", str(e))
                return Response(
                    {"status": "error", "message": "기업 생성 중 오류가 발생했습니다."},
                    status=500,
                )
        else:
            logger.debug("Serializer validation failed: %s", serializer.errors)
            return Response({"status": "error", "errors": serializer.errors}, status=400)


    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def detail_by_slug(self, request):
        """
        특정 기업(slug 기반) 출력 API
        """
        slug = request.data.get('slug')
        if not slug:
            return Response({"error": "slug가 필요합니다."}, status=400)

        try:
            corp = Corp.objects.get(slug=slug)
            serializer = CorpSerializer(corp)
            return Response(serializer.data)
        except Corp.DoesNotExist:
            return Response({"error": "해당 slug의 공공기관을 찾을 수 없습니다."}, status=404)
        

    @action(detail=False, methods=['post'], authentication_classes=[CorpUserJWTAuthentication], permission_classes=[IsAuthenticated])
    def user_info(self, request):
        """
        공공기관 및 사용자 정보 출력 API
        """
        corp_user = request.user
        if not corp_user:
            return Response({"error": "가입된 사용자가 아닙니다."}, status=404)

        corp = corp_user.corp
        if not corp:
            return Response({"error": "공공기관 정보가 없습니다."}, status=404)

        department = corp_user.department
        department_data = {
            "department_id": department.department_id if department else "",
            "department_name": department.department_name if department else ""
        }

        user_data = CorpUserSerializer(corp_user).data
        corp_data = CorpSerializer(corp).data

        return Response({
            "user": user_data,
            "corp": corp_data,
            "department": department_data,
        })


# 부서
class DepartmentViewSet(ViewSet):
    authentication_classes = [CorpUserJWTAuthentication]
    
    # 공공기관 부서 목록 조회 (POST 요청)
    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def list_departments(self, request):
        try:
            slug = request.data.get('slug')
            corp_id = request.data.get('CorpID')

            if not slug and not corp_id:
                return Response({'error': 'slug 또는 corpID 중 하나를 제공해야 합니다.'}, status=400)

            if corp_id:
                departments = list(
                    Corp_Department.objects.filter(corp_id=corp_id)
                    .values_list('department_name', flat=True)
                    .distinct()
                )
            elif slug:
                corp = Corp.objects.filter(slug=slug).first()
                if not corp:
                    return Response({'error': '해당 slug에 일치하는 Public이 없습니다.'}, status=404)

                departments = list(
                    Corp_Department.objects.filter(corp=corp)
                    .values_list('department_name', flat=True)
                    .distinct()
                )

            if '기타' not in departments:
                departments.append('기타')

            if departments:
                return Response({'departments': departments}, status=200)
            else:
                return Response({'message': '해당 corp_id 또는 slug에 대한 부서가 없습니다.'}, status=404)

        except Exception as e:
            return Response({'error': str(e)}, status=500)

    # 부서 생성 (POST 요청)
    def create(self, request):
        department_name = request.data.get('department_name')
        corp_id = request.data.get('corp_id')

        if not department_name or not corp_id:
            return Response(
                {"error": "부서 이름과 기업 ID는 필수입니다."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            corp = Corp.objects.get(corp_id=corp_id)
        except Corp.DoesNotExist:
            return Response(
                {"error": "유효하지 않은 공공기관 ID입니다."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        department = Corp_Department.objects.create(department_name=department_name, corp=corp)
        serializer = CorpDepartmentSerializer(department)
        return Response(serializer.data, status=status.HTTP_201_CREATED)



    # 사용자 부서 이동 (PUT 요청)
    def update(self, request, pk=None):
        user = request.user
        department_name = request.data.get("department_name")
        corp_id = request.data.get("corp_id")

        if not department_name or not corp_id:
            return Response(
                {"error": "부서와 기업 ID는 필수 항목입니다."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            department = Corp_Department.objects.get(department_name=department_name, corp_id=corp_id)

            if user.department == department:
                return Response(
                    {"error": "현재 선택된 부서와 동일합니다."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            user.department = department
            user.save()
            return Response({"message": "부서가 성공적으로 변경되었습니다."}, status=status.HTTP_200_OK)

        except Corp_Department.DoesNotExist:
            return Response(
                {"error": "해당 부서는 존재하지 않습니다."},
                status=status.HTTP_404_NOT_FOUND
            )

        except Exception as e:
            return Response(
                {"error": f"예상치 못한 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


