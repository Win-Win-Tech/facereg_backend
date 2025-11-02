from django.urls import path
from .views import FaceAttendanceView, RegisterEmployeeView

urlpatterns = [
    path("attendance/", FaceAttendanceView.as_view(), name="attendance"),
    path("register/", RegisterEmployeeView.as_view(), name="register"),
]
