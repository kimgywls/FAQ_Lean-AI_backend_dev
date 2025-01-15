# complaint_views.py
# 민원 등록, 조회, 상태 변경, 이관, 답변
from django.shortcuts import get_object_or_404 
from ..authentication import PublicUserJWTAuthentication
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action
import logging
from ..models import Public, Public_User, Public_Department, Public_Complaint
from ..serializers import (PublicComplaintSerializer)
from send_sms import send_aligo_sms

# 디버깅을 위한 로거 설정
logger = logging.getLogger('faq')


# Complaint Management APIs
class ComplaintViewSet(ViewSet):
    authentication_classes = [PublicUserJWTAuthentication] 
    permission_classes = [AllowAny]

    def list(self, request):
        """민원 출력 (부서 기반)"""

        # 권한 확인
        self.authentication_classes = [PublicUserJWTAuthentication]
        self.permission_classes = [IsAuthenticated]
        self.check_permissions(request)

        user = request.user

        public = user.public
        if not public:
            return Response({"error": "해당 사용자는 매장이 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        user_department = user.department
        if not user_department:
            return Response({"error": "사용자가 속한 부서가 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        complaints = Public_Complaint.objects.filter(
            public=public,
            department=user_department
        )
        serializer = PublicComplaintSerializer(complaints, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)



    def create(self, request):
        """민원 등록"""

        logger.debug(f"Received data: {request.data}")

        data = request.data.copy()
        slug = data.get('slug')

        if not slug:
            return Response({"status": "error", "message": "slug가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            public = Public.objects.get(slug=slug)
            data['public'] = public.public_id

            department_name = data.get('department')
            if department_name == '기타':
                department, _ = Public_Department.objects.get_or_create(department_name='기타', public=public)
                data['department'] = department.department_id
            elif department_name:
                department = get_object_or_404(Public_Department, department_name=department_name, public=public)
                data['department'] = department.department_id

            serializer = PublicComplaintSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            complaint = serializer.save()

            # SMS 알림 발송 로직
            complaint_number = complaint.complaint_number
            applicant_phone = complaint.phone
            
            department_users = Public_User.objects.filter(department=department)
            manager_phone = department_users.phone

            if applicant_phone:
                send_aligo_sms(
                    receiver=applicant_phone,
                    message=f"안녕하세요, 접수하신 민원의 접수번호는 [{complaint_number}]입니다."
                )

            if manager_phone:
                send_aligo_sms(
                    receiver=manager_phone,
                    message=f"[{department_name}] 부서에 새 민원이 접수되었습니다. 접수번호: [{complaint_number}]"
                )

            return Response({"status": "success", "message": "민원이 성공적으로 접수되었습니다."}, status=status.HTTP_201_CREATED)

        except Public.DoesNotExist:
            return Response({"status": "error", "message": "공공기관이 유효하지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)


    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def customer_view(self, request):
        """민원인 측 민원 조회"""
        complaint_number = request.data.get("complaint_number")
        phone = request.data.get("phone")

        if not complaint_number:
            return Response({"success": False, "message": "접수번호를 입력해 주세요."}, status=status.HTTP_400_BAD_REQUEST)

        complaint = get_object_or_404(Public_Complaint, complaint_number=complaint_number, phone=phone)

        return Response({
            "success": True,
            "complaint": {
                "complaint_number": complaint.complaint_number,
                "title": complaint.title,
                "name": complaint.name,
                "created_at": complaint.created_at.strftime("%Y-%m-%d"),
                "status": complaint.status,
                "content": complaint.content,
                "answer": complaint.answer
            }
        }, status=status.HTTP_200_OK)



    @action(detail=True, methods=['patch'], authentication_classes=[PublicUserJWTAuthentication], permission_classes=[IsAuthenticated])
    def update_status(self, request, pk=None):
        """민원 상태 변경"""
        complaint = get_object_or_404(Public_Complaint, pk=pk)
        new_status = request.data.get('status')

        if new_status not in dict(Public_Complaint.STATUS_CHOICES):
            return Response({"status": "error", "message": "유효하지 않은 상태입니다."}, status=status.HTTP_400_BAD_REQUEST)

        complaint.status = new_status
        complaint.save()

        if new_status == "완료":
            applicant_phone = complaint.phone  # 신청자 전화번호
            if applicant_phone:
                try:
                    send_aligo_sms(
                        receiver=applicant_phone,
                        message=f"안녕하세요, 접수하신 민원(접수번호: [{complaint.complaint_number}])이 처리 완료되었습니다. 감사합니다."
                    )
                    logger.info(f"민원 처리 완료 알림을 {applicant_phone}로 전송했습니다.")
                except Exception as e:
                    logger.error(f"민원 처리 완료 알림 전송 실패: {str(e)}")
                    return Response({"status": "error", "message": "SMS 전송 중 오류가 발생했습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"status": "success", "message": f"민원 상태가 '{new_status}'로 변경되었습니다."}, status=status.HTTP_200_OK)



    @action(detail=True, methods=['post'], authentication_classes=[PublicUserJWTAuthentication], permission_classes=[IsAuthenticated])
    def transfer(self, request, pk=None):
        """민원 부서 이관"""
        complaint = get_object_or_404(Public_Complaint, pk=pk)
        department_name = request.data.get('department')
        reason = request.data.get('reason')

        new_department = get_object_or_404(Public_Department, department_name=department_name, public=complaint.public)

        if complaint.department == new_department:
            return Response({"error": "현재 부서와 동일한 부서로 이관할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        complaint.department = new_department
        complaint.transfer_reason = reason
        complaint.save()

        # 새 부서의 담당자들에게 SMS 전송
        department_users = Public_User.objects.filter(department=new_department)
        manager_phones = [user.phone for user in department_users if user.phone]

        for phone in manager_phones:
            try:
                send_aligo_sms(
                    receiver=phone,
                    message=f"[{new_department.department_name}] 부서로 민원(접수번호: [{complaint.complaint_number}])이 이관되었습니다. 확인 바랍니다."
                )
                logger.info(f"민원 이관 알림을 {phone}로 전송했습니다.")
            except Exception as e:
                logger.error(f"민원 이관 알림 전송 실패 (전화번호: {phone}): {str(e)}")

        return Response({"success": True, "message": "민원이 성공적으로 이관되었습니다."}, status=status.HTTP_200_OK)



    @action(detail=True, methods=['post'], authentication_classes=[PublicUserJWTAuthentication], permission_classes=[IsAuthenticated])
    def answer(self, request, pk=None):
        """민원 답변 등록"""
        complaint = get_object_or_404(Public_Complaint, pk=pk)
        answer = request.data.get('answer')

        complaint.answer = answer
        complaint.save()

        complaint_number = complaint.complaint_number
        applicant_phone = complaint.phone
        if applicant_phone:
                try:
                    send_aligo_sms(
                        receiver=applicant_phone,
                        message=f"안녕하세요, 접수하신 민원(접수번호: [{complaint_number}])에 답변이 등록되었습니다. "
                    )
                    logger.info(f"민원 답변 알림을 {applicant_phone}로 전송했습니다.")
                except Exception as e:
                    logger.error(f"민원 답변 알림 전송 실패: {str(e)}")
                    return Response({"status": "error", "message": "SMS 전송 중 오류가 발생했습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"success": True, "message": "답변이 성공적으로 저장되었습니다."}, status=status.HTTP_200_OK)
