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
import base64
from .models import PayrollRecord

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

        employees = Employee.objects.only("id", "name", "face_encoding", "photo")
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

            photo_base64 = base64.b64encode(matched_employee.photo).decode("utf-8") if matched_employee.photo else None

            return Response({
                "status": f"{entry_type.capitalize()} successful",
                "message": message,
                "employee": matched_employee.name.strip(),
                "confidence": confidence,
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "photo": f"data:image/jpeg;base64,{photo_base64}" if photo_base64 else None
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
        image_bytes = image.read()
        encoding = get_face_encoding(image_bytes)

        if encoding is None:
            logger.info("No face detected during registration")
            return Response({"error": "No face detected"}, status=status.HTTP_400_BAD_REQUEST)

        name = request.data.get("name", "").strip()
        if not name:
            logger.warning("Missing name field during registration")
            return Response({"error": "Name is required"}, status=status.HTTP_400_BAD_REQUEST)

        employee = Employee.objects.create(
            name=name,
            face_encoding=encoding.tobytes(),
            photo=image_bytes
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

            # ✅ Calculate duration if both timestamps exist
            if checkin_time and checkout_time:
                duration_seconds = (checkout_time - checkin_time).total_seconds()
                hours = int(duration_seconds // 3600)
                minutes = int((duration_seconds % 3600) // 60)
                duration_str = f"{hours:02d}:{minutes:02d}"
            else:
                duration_str = None

            summary.append({
                "employee": emp.name,
                "date": today.strftime("%Y-%m-%d"),
                "checkin": checkin_time.strftime("%H:%M:%S") if checkin_time else None,
                "checkout": checkout_time.strftime("%H:%M:%S") if checkout_time else None,
                "duration": duration_str  # ✅ Added here
            })

        return Response(summary)


from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.timezone import localtime
from django.conf import settings
from datetime import date
from openpyxl import Workbook
import os

from .models import Employee, AttendanceLog
from django.db.models import Min, Max

class AttendanceSummaryExportView(APIView):
    def get(self, request):
        today = date.today()
        wb = Workbook()
        ws = wb.active
        ws.title = "Attendance Summary"

        # Header
        ws.append(["Employee", "Date", "Check-in", "Check-out", "Duration"])

        employees = Employee.objects.prefetch_related("attendancelog_set")
        for emp in employees:
            logs = emp.attendancelog_set.filter(timestamp__date=today)
            checkin_time = logs.filter(type="checkin").aggregate(Min("timestamp"))["timestamp__min"]
            checkout_time = logs.filter(type="checkout").aggregate(Max("timestamp"))["timestamp__max"]

            if checkin_time and checkout_time:
                duration_seconds = (checkout_time - checkin_time).total_seconds()
                hours = int(duration_seconds // 3600)
                minutes = int((duration_seconds % 3600) // 60)
                duration_str = f"{hours:02d}:{minutes:02d}"
            else:
                duration_str = ""

            ws.append([
                emp.name,
                today.strftime("%Y-%m-%d"),
                checkin_time.strftime("%H:%M:%S") if checkin_time else "",
                checkout_time.strftime("%H:%M:%S") if checkout_time else "",
                duration_str
            ])

        filename = f"attendance_summary_{today.strftime('%Y%m%d')}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        return Response({"file_url": file_url})


from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from calendar import monthrange
from datetime import datetime, timedelta
from django.utils.dateparse import parse_date
from django.utils.timezone import now
from collections import defaultdict
from .models import Employee, AttendanceLog

class MonthlyAttendanceStatusView(APIView):
    def get(self, request):
        # Get month from query param: format YYYY-MM
        month = request.query_params.get("month")
        if not month:
            return Response({"error": "Month is required in YYYY-MM format"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            year, month_num = map(int, month.split("-"))
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except Exception:
            return Response({"error": "Invalid month format"}, status=status.HTTP_400_BAD_REQUEST)

        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        employees = Employee.objects.all()
        logs = AttendanceLog.objects.filter(timestamp__date__range=(start_date, end_date))

        # Build attendance map: {(emp_id, date): "P"/"A"}
        attendance_map = defaultdict(lambda: defaultdict(lambda: "-"))
        for log in logs:
            key = (log.employee_id, log.timestamp.date())
            attendance_map[log.employee_id][log.timestamp.date()] = "P"

        summary = []
        for emp in employees:
            row = {"name": emp.name}
            for date in date_range:
                status_code = attendance_map[emp.id].get(date, "A" if emp.id in attendance_map else "-")
                row[date.strftime("%d-%b")] = status_code
            summary.append(row)

        return Response(summary)


from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.timezone import localtime
from django.conf import settings
from calendar import monthrange
from datetime import datetime, timedelta
from openpyxl import Workbook
import os
from .models import Employee, AttendanceLog
from decimal import Decimal

class MonthlyAttendanceStatusExportView(APIView):
    def get(self, request):
        month = request.query_params.get("month")
        if not month:
            return Response({"error": "Month is required in YYYY-MM format"}, status=400)

        try:
            year, month_num = map(int, month.split("-"))
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except Exception:
            return Response({"error": "Invalid month format"}, status=400)

        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        employees = Employee.objects.all()
        logs = AttendanceLog.objects.filter(timestamp__date__range=(start_date, end_date))

        # Build attendance map
        attendance_map = {}
        for log in logs:
            key = (log.employee_id, log.timestamp.date())
            attendance_map[key] = "P"

        # Create Excel
        wb = Workbook()
        ws = wb.active
        ws.title = f"Attendance {month}"

        # Header row
#       header = ["Name"] + [d.strftime("%d-%b") for d in date_range]
        header = ["Name"] + [d.strftime("%d-%b") for d in date_range] + ["Present", "Absent"]

        ws.append(header)

        for emp in employees:
            row = [emp.name]
            present_count = 0
            absent_count = 0

            for d in date_range:
                key = (emp.id, d)
                status = attendance_map.get(key, "A" if any(k[0] == emp.id for k in attendance_map) else "-")
                row.append(status)
                if status == "P":
                    present_count += 1
                elif status == "A":
                    absent_count += 1

            row.append(present_count)
            row.append(absent_count)
            ws.append(row)


        filename = f"monthly_attendance_{month}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        return Response({"file_url": file_url})

class GeneratePayrollView(APIView):
    def post(self, request):
        month = request.data.get("month")  # Format: YYYY-MM
        if not month:
            return Response({"error": "Month is required"}, status=400)

        year, month_num = map(int, month.split("-"))
        start_date = datetime(year, month_num, 1).date()
        end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()

        employees = Employee.objects.all()
        logs = AttendanceLog.objects.filter(timestamp__date__range=(start_date, end_date))

        attendance_map = {}
        for log in logs:
            key = (log.employee_id, log.timestamp.date())
            attendance_map[key] = "P"

        for emp in employees:
            present = sum(1 for day in range((end_date - start_date).days + 1)
                          if attendance_map.get((emp.id, start_date + timedelta(days=day))) == "P")
            absent = sum(1 for day in range((end_date - start_date).days + 1)
                         if attendance_map.get((emp.id, start_date + timedelta(days=day))) == "A")

            base_salary = emp.base_salary or 0
            deduction_per_day = emp.deduction_per_day or 0
            deductions = absent * deduction_per_day
            pf_deduction = (base_salary * Decimal("0.12")).quantize(Decimal("0.01"))
            esi_deduction = (base_salary * Decimal("0.0175")).quantize(Decimal("0.01"))
            net_pay = base_salary - deductions - pf_deduction - esi_deduction


            PayrollRecord.objects.create(
                employee=emp,
                month=month,
                present_days=present,
                absent_days=absent,
                base_salary=base_salary,
                deduction_per_day=deduction_per_day,
                deductions=deductions,
                pf_deduction=pf_deduction,
                esi_deduction=esi_deduction,
                net_pay=net_pay
            )

        return Response({"status": "Payroll generated for " + month})

class PayrollExportView(APIView):
    def get(self, request):
        month = request.query_params.get("month")
        if not month:
            return Response({"error": "Month is required in YYYY-MM format"}, status=400)

        records = PayrollRecord.objects.filter(month=month)
        if not records.exists():
            return Response({"error": "No payroll records found for this month"}, status=404)

        wb = Workbook()
        ws = wb.active
        ws.title = f"Payroll {month}"

        # Header
        ws.append([
            "Employee", "Present Days", "Absent Days", "Base Salary",
            "Deduction/Day", "Deductions", "PF", "ESI", "Net Pay"
        ])

        for record in records:
            ws.append([
                record.employee.name,
                record.present_days,
                record.absent_days,
                float(record.base_salary),
                float(record.deduction_per_day),
                float(record.deductions),
                float(record.pf_deduction),
                float(record.esi_deduction),
                float(record.net_pay),
            ])

        filename = f"payroll_{month}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        return Response({"file_url": file_url})


