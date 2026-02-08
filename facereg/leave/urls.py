from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    # LeaveType
    LeaveTypeListCreateView,
    LeaveTypeDetailView,
    
    # Holiday
    HolidayListCreateView,
    HolidayBulkCreateView,
    HolidayGroupedDatesBulkCreateView,
    HolidayExcelUploadView,
    HolidayDetailView,
    
    # LocationWeekoff
    LocationWeekoffViewSet,
    
    # EmployeeWeekoff
    EmployeeWeekoffViewSet,
    
    # LeaveRequest
    LeaveRequestViewSet,
    LeaveRequestBulkCreateView,
    
    # LeaveBalance
    LeaveBalanceViewSet,
    
    # ManualAttendance
    ManualAttendanceViewSet,
    ManualAttendanceBulkCreateView,
    ManualAttendanceBulkUpdateView,
    ManualAttendanceBulkDeleteView,
    ManualAttendanceBulkMarkView,
)

app_name = 'leave'

# Router for ViewSets
router = DefaultRouter()
router.register(r'weekoffs/location', LocationWeekoffViewSet, basename='location-weekoff')
router.register(r'weekoffs/employee', EmployeeWeekoffViewSet, basename='employee-weekoff')
router.register(r'leave-requests', LeaveRequestViewSet, basename='leave-request')
router.register(r'leave-balances', LeaveBalanceViewSet, basename='leave-balance')
router.register(r'manual-attendance', ManualAttendanceViewSet, basename='manual-attendance')

urlpatterns = [
    # Include router URLs (automatically generates CRUD routes for ViewSets)
    path('', include(router.urls)),
    
    # LeaveType URLs
    path('leave-types/', LeaveTypeListCreateView.as_view(), name='leave-type-list-create'),
    path('leave-types/<uuid:pk>/', LeaveTypeDetailView.as_view(), name='leave-type-detail'),
    
    # LeaveRequest Bulk URLs
    path('leave-requests/bulk/', LeaveRequestBulkCreateView.as_view(), name='leave-request-bulk-create'),
    
    # Holiday URLs
    path('holidays/', HolidayListCreateView.as_view(), name='holiday-list-create'),
    path('holidays/bulk/', HolidayBulkCreateView.as_view(), name='holiday-bulk-create'),
    path('holidays/bulk/grouped-dates/', HolidayGroupedDatesBulkCreateView.as_view(), name='holiday-grouped-dates-bulk-create'),
    path('holidays/bulk/excel-upload/', HolidayExcelUploadView.as_view(), name='holiday-excel-upload'),
    path('holidays/<uuid:pk>/', HolidayDetailView.as_view(), name='holiday-detail'),
    
    # ManualAttendance Bulk URLs (keeping as separate views since they're not standard CRUD)
    path('manual-attendance/bulk/', ManualAttendanceBulkCreateView.as_view(), name='manual-attendance-bulk-create'),
    path('manual-attendance/bulk/update/', ManualAttendanceBulkUpdateView.as_view(), name='manual-attendance-bulk-update'),
    path('manual-attendance/bulk/delete/', ManualAttendanceBulkDeleteView.as_view(), name='manual-attendance-bulk-delete'),
    path('manual-attendance/bulk/mark/', ManualAttendanceBulkMarkView.as_view(), name='manual-attendance-bulk-mark'),
]

