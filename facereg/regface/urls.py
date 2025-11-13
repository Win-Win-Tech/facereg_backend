from django.urls import path

from .views import (
    AttendanceSummaryExportView,
    AttendanceSummaryView,
    EmployeeDetailView,
    EmployeeListView,
    FaceAttendanceView,
    GeneratePayrollView,
    LocationDetailView,
    LocationListCreateView,
    LoginView,
    LogoutView,
    MonthlyAttendanceStatusExportView,
    MonthlyAttendanceStatusView,
    PayrollExportView,
    RegisterEmployeeView,
    UserDetailView,
    UserListCreateView,
)

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("locations/", LocationListCreateView.as_view(), name="locations"),
    path("locations/<uuid:pk>/", LocationDetailView.as_view(), name="location-detail"),
    path("users/", UserListCreateView.as_view(), name="users"),
    path("users/<int:pk>/", UserDetailView.as_view(), name="user-detail"),
    path("employees/", EmployeeListView.as_view(), name="employees"),
    path("employees/<int:pk>/", EmployeeDetailView.as_view(), name="employee-detail"),
    path("attendance/", FaceAttendanceView.as_view(), name="attendance"),
    path("register/", RegisterEmployeeView.as_view(), name="register"),
    path(
        "attendance-summary/",
        AttendanceSummaryView.as_view(),
        name="attendance-summary",
    ),
    path(
        "attendance-summary/export/",
        AttendanceSummaryExportView.as_view(),
        name="attendance-summary-export",
    ),
    path(
        "monthly-attendance-status/",
        MonthlyAttendanceStatusView.as_view(),
        name="monthly-attendance-status",
    ),
    path(
        "monthly-attendance/export/",
        MonthlyAttendanceStatusExportView.as_view(),
        name="monthly-attendance-export",
    ),
    path("generate-payroll/", GeneratePayrollView.as_view(), name="generate-payroll"),
    path("payroll/export/", PayrollExportView.as_view(), name="payroll-export"),
]
