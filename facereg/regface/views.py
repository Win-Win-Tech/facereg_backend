from django.utils import timezone
from datetime import date
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Min, Max
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

        employees = Employee.objects.only("id", "name", "face_encoding")
        known_encodings = []
        employee_map = []

        for emp in employees:
            encoding = np.frombuffer(emp.face_encoding)
            known_encodings.append(encoding)
            employee_map.append(emp)

        matches = face_recognition.compare_faces(known_encodings, uploaded_encoding, tolerance=0.45)
        if True in matches:
            best_match_index = matches.index(True)
            matched_employee = employee_map[best_match_index]
            today = date.today()
            now = timezone.localtime()

            logs_today = AttendanceLog.objects.filter(employee=matched_employee, timestamp__date=today)
            has_checkin = any(log.type == "checkin" for log in logs_today)
            has_checkout = any(log.type == "checkout" for log in logs_today)

            if not has_checkin:
                entry_type = "checkin"
                message = f"Welcome, {matched_employee.name.strip()}! Your check-in has been recorded."
            elif not has_checkout:
                entry_type = "checkout"
                message = f"Good job today, {matched_employee.name.strip()}! Your check-out is complete."
            else:
                logger.info("Both checkin and checkout already marked for %s", matched_employee.name.strip())
                return Response({
                    "status": "Already marked",
                    "message": "You've already completed both check-in and check-out for today.",
                    "employee": matched_employee.name.strip(),
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S")
                }, status=status.HTTP_200_OK)

            AttendanceLog.objects.create(employee=matched_employee, type=entry_type)
            logger.info("%s marked for %s", entry_type.capitalize(), matched_employee.name.strip())

            confidence = round(1 - face_recognition.face_distance(
                [known_encodings[best_match_index]], uploaded_encoding
            )[0], 2)

            return Response({
                "status": f"{entry_type.capitalize()} successful",
                "message": message,
                "employee": matched_employee.name.strip(),
                "confidence": confidence,
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S")
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


class AttendanceSummaryView(APIView):
    def get(self, request):
        today = date.today()
        summary = []

        employees = Employee.objects.prefetch_related("attendancelog_set")
        for emp in employees:
            logs = emp.attendancelog_set.filter(timestamp__date=today)
            checkin_time = logs.filter(type="checkin").aggregate(Min("timestamp"))["timestamp__min"]
            checkout_time = logs.filter(type="checkout").aggregate(Max("timestamp"))["timestamp__max"]

            summary.append({
                "employee": emp.name,
                "date": today.strftime("%Y-%m-%d"),
                "checkin": checkin_time.strftime("%H:%M:%S") if checkin_time else None,
                "checkout": checkout_time.strftime("%H:%M:%S") if checkout_time else None,
            })

        return Response(summary)
