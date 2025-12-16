from django.contrib import admin
from .models import (
    LeaveType, Holiday, LocationWeekoff, EmployeeWeekoff,
    LeaveRequest, LeaveBalance, ManualAttendance
)


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('leave_type_name', 'leave_code', 'location', 'is_paid', 'max_days_per_year', 'is_active')
    list_filter = ('location', 'is_paid', 'is_active')
    search_fields = ('leave_type_name', 'leave_code', 'location__name')


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('holiday_name', 'holiday_date', 'location', 'holiday_type', 'is_recurring')
    list_filter = ('location', 'holiday_type', 'is_recurring', 'holiday_date')
    search_fields = ('holiday_name', 'location__name')
    date_hierarchy = 'holiday_date'


@admin.register(LocationWeekoff)
class LocationWeekoffAdmin(admin.ModelAdmin):
    list_display = ('location', 'get_weekoff_patterns', 'is_active', 'effective_from', 'effective_to')
    list_filter = ('is_active',)
    search_fields = ('location__name',)
    
    def get_weekoff_patterns(self, obj):
        return ', '.join(obj.weekoff_patterns) if obj.weekoff_patterns else 'None'
    get_weekoff_patterns.short_description = 'Weekoff Patterns'


@admin.register(EmployeeWeekoff)
class EmployeeWeekoffAdmin(admin.ModelAdmin):
    list_display = ('employee', 'location', 'get_weekoff_patterns', 'is_active', 'effective_from', 'effective_to')
    list_filter = ('is_active', 'location')
    search_fields = ('employee__name', 'location__name')
    
    def get_weekoff_patterns(self, obj):
        return ', '.join(obj.weekoff_patterns) if obj.weekoff_patterns else 'None'
    get_weekoff_patterns.short_description = 'Weekoff Patterns'


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'total_days', 'status', 'applied_on')
    list_filter = ('status', 'leave_type', 'location', 'applied_on')
    search_fields = ('employee__name', 'leave_type__leave_type_name')
    date_hierarchy = 'applied_on'


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'year', 'total_allocated', 'used_days', 'available_days')
    list_filter = ('year', 'leave_type', 'location')
    search_fields = ('employee__name', 'leave_type__leave_type_name')


@admin.register(ManualAttendance)
class ManualAttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'attendance_date', 'status', 'marked_by', 'marked_on')
    list_filter = ('status', 'attendance_date', 'location')
    search_fields = ('employee__name',)
    date_hierarchy = 'attendance_date'
