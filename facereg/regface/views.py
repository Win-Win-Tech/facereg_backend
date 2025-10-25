from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Employee, AttendanceLog
from .serializers import FaceUploadSerializer
from .face_utils import get_face_encoding
import numpy as np
import face_recognition
import logging

logger = logging.getLogger(__name__)

class FaceAttendanceView(APIView):
    def post(self, request):
        serializer = FaceUploadSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning("Invalid face upload data: %s", serializer.errors)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        image = serializer.validated_data['image']
        uploaded_encoding = get_face_encoding(image.read())

        if uploaded_encoding is None:
            logger.info("No face detected in uploaded image")
            return Response({"error": "No face detected"}, status=status.HTTP_400_BAD_REQUEST)

        employees = Employee.objects.all()
        known_encodings = [np.frombuffer(emp.face_encoding) for emp in employees]

        # Use face_distance for better accuracy
        distances = face_recognition.face_distance(known_encodings, uploaded_encoding)
        best_match_index = np.argmin(distances)
        best_distance = distances[best_match_index]

        # Set stricter threshold
        threshold = 0.45
        if best_distance < threshold:
#           matched_employee = employees[best_match_index]
            matched_employee = employees[int(best_match_index)]
            AttendanceLog.objects.create(employee=matched_employee)
            logger.info("Attendance marked for %s", matched_employee.name.strip())
            return Response({
                "status": "Attendance marked",
                "employee": matched_employee.name.strip(),
                "confidence": round(1 - best_distance, 2)
            }, status=status.HTTP_200_OK)

        logger.info("Face not recognized")
        return Response({"error": "Face not recognized"}, status=status.HTTP_404_NOT_FOUND)


class RegisterEmployeeView(APIView):
    def post(self, request):
        serializer = FaceUploadSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning("Invalid registration data: %s", serializer.errors)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        image = serializer.validated_data['image']
        encoding = get_face_encoding(image.read())
        if encoding is None:
            logger.info("No face detected during registration")
            return Response({"error": "No face detected"}, status=status.HTTP_400_BAD_REQUEST)

        name = request.data.get("name", "").strip()
        if not name:
            logger.warning("Missing name field during registration")
            return Response({"error": "Name is required"}, status=status.HTTP_400_BAD_REQUEST)

        employee = Employee.objects.create(
            name=name,
            face_encoding=encoding.tobytes()
        )
        logger.info("Employee registered: %s", name)
        return Response({
            "status": "Employee registered",
            "employee_id": employee.id,
            "name": employee.name
        }, status=status.HTTP_201_CREATED)
