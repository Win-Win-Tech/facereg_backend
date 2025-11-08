from django.urls import path
from .views import FaceAttendanceView, RegisterEmployeeView, AttendanceSummaryView, AttendanceSummaryExportView, MonthlyAttendanceStatusView, MonthlyAttendanceStatusExportView, GeneratePayrollView, PayrollExportView

urlpatterns = [
    path("attendance/", FaceAttendanceView.as_view(), name="attendance"),
    path("register/", RegisterEmployeeView.as_view(), name="register"),
    path("attendance-summary/", AttendanceSummaryView.as_view(), name="attendance-summary"),
    path("attendance-summary/export/", AttendanceSummaryExportView.as_view(), name="attendance-summary-export"),
    path("monthly-attendance-status/", MonthlyAttendanceStatusView.as_view(), name="monthly-attendance-status"),
    path("monthly-attendance/export/", MonthlyAttendanceStatusExportView.as_view(), name="monthly-attendance-export"),
    path("generate-payroll/", GeneratePayrollView.as_view(), name="generate-payroll"),
    path("payroll/export/", PayrollExportView.as_view(), name="payroll-export"),
    ]
