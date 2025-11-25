from rest_framework.routers import DefaultRouter
from .views import (
    LocationViewSet, UserViewSet, EmployeeViewSet, ShiftViewSet,
    SiteViewSet, AttendanceLogViewSet, PayrollRecordViewSet,
    AssignmentViewSet, UserSiteViewSet
)

router = DefaultRouter()
router.register(r"locations", LocationViewSet)
router.register(r"users", UserViewSet)
router.register(r"employees", EmployeeViewSet)
router.register(r"shifts", ShiftViewSet)
router.register(r"sites", SiteViewSet)
router.register(r"attendance-logs", AttendanceLogViewSet)
router.register(r"payroll-records", PayrollRecordViewSet)
router.register(r"assignments", AssignmentViewSet)
router.register(r"user-sites", UserSiteViewSet)

urlpatterns = router.urls
