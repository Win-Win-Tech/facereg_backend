import uuid
from django.db import models
from django.utils import timezone


class LeaveType(models.Model):
    """Master data for leave types (Sick Leave, Casual Leave, etc.)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey("regface.Location", on_delete=models.CASCADE, related_name="leave_types")
    leave_type_name = models.CharField(max_length=100)  # "Sick Leave", "Casual Leave"
    leave_code = models.CharField(max_length=20)  # "SL", "CL", "ML"
    is_paid = models.BooleanField(default=True)  # Paid leave = True, Unpaid = False
    max_days_per_year = models.IntegerField(null=True, blank=True)  # Annual quota (null = unlimited)
    carry_forward_allowed = models.BooleanField(default=False)  # Can carry to next year?
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="created_leave_types")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('location', 'leave_code')]
        ordering = ['leave_type_name']

    def __str__(self):
        return f"{self.leave_type_name} ({self.leave_code}) - {self.location.name}"


class Holiday(models.Model):
    """Common holidays (location-specific)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey("regface.Location", on_delete=models.CASCADE, related_name="holidays")
    holiday_name = models.CharField(max_length=200)  # "New Year", "Independence Day"
    holiday_date = models.DateField()
    holiday_type = models.CharField(max_length=50, choices=[
        ('NATIONAL', 'National'),
        ('LOCAL', 'Local'),
        ('RELIGIOUS', 'Religious'),
        ('OTHER', 'Other'),
    ], default='NATIONAL')
    is_recurring = models.BooleanField(default=False)  # Repeats every year?
    description = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="created_holidays")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('location', 'holiday_date', 'holiday_name')]
        ordering = ['holiday_date']
        indexes = [
            models.Index(fields=['location', 'holiday_date']),
        ]

    def __str__(self):
        return f"{self.holiday_name} - {self.location.name} ({self.holiday_date})"


class LocationWeekoff(models.Model):
    """Default weekoff pattern for entire location"""
    WEEKOFF_PATTERNS = [
        # Every day patterns
        ('SUNDAY', 'Sunday'),
        ('MONDAY', 'Monday'),
        ('TUESDAY', 'Tuesday'),
        ('WEDNESDAY', 'Wednesday'),
        ('THURSDAY', 'Thursday'),
        ('FRIDAY', 'Friday'),
        ('SATURDAY', 'Saturday'),
        # Odd occurrence patterns (1st, 3rd, 5th)
        ('ODD_SUNDAY', 'Odd Sundays (1st, 3rd, 5th)'),
        ('ODD_MONDAY', 'Odd Mondays (1st, 3rd, 5th)'),
        ('ODD_TUESDAY', 'Odd Tuesdays (1st, 3rd, 5th)'),
        ('ODD_WEDNESDAY', 'Odd Wednesdays (1st, 3rd, 5th)'),
        ('ODD_THURSDAY', 'Odd Thursdays (1st, 3rd, 5th)'),
        ('ODD_FRIDAY', 'Odd Fridays (1st, 3rd, 5th)'),
        ('ODD_SATURDAY', 'Odd Saturdays (1st, 3rd, 5th)'),
        # Even occurrence patterns (2nd, 4th)
        ('EVEN_SUNDAY', 'Even Sundays (2nd, 4th)'),
        ('EVEN_MONDAY', 'Even Mondays (2nd, 4th)'),
        ('EVEN_TUESDAY', 'Even Tuesdays (2nd, 4th)'),
        ('EVEN_WEDNESDAY', 'Even Wednesdays (2nd, 4th)'),
        ('EVEN_THURSDAY', 'Even Thursdays (2nd, 4th)'),
        ('EVEN_FRIDAY', 'Even Fridays (2nd, 4th)'),
        ('EVEN_SATURDAY', 'Even Saturdays (2nd, 4th)'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.OneToOneField("regface.Location", on_delete=models.CASCADE, related_name="location_weekoff")
    weekoff_patterns = models.JSONField(
        default=list,
        blank=True,
        help_text="Array of selected patterns, e.g., ['SUNDAY', 'ODD_SATURDAY']. Multiple patterns can be selected."
    )
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(default=timezone.now)
    effective_to = models.DateField(null=True, blank=True)  # null = ongoing
    created_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="created_location_weekoffs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        patterns_str = ', '.join(self.weekoff_patterns) if self.weekoff_patterns else 'None'
        return f"Weekoff Pattern - {self.location.name} ({patterns_str})"


class EmployeeWeekoff(models.Model):
    """Employee-specific weekoff pattern (overrides location default)"""
    WEEKOFF_PATTERNS = LocationWeekoff.WEEKOFF_PATTERNS  # Same patterns as location
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.OneToOneField("regface.Employee", on_delete=models.CASCADE, related_name="employee_weekoff")
    location = models.ForeignKey("regface.Location", on_delete=models.CASCADE, related_name="employee_weekoffs")
    weekoff_patterns = models.JSONField(
        default=list,
        blank=True,
        help_text="Array of selected patterns, e.g., ['SUNDAY', 'ODD_SATURDAY']. Multiple patterns can be selected."
    )
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(default=timezone.now)
    effective_to = models.DateField(null=True, blank=True)  # null = ongoing
    created_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="created_employee_weekoffs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        patterns_str = ', '.join(self.weekoff_patterns) if self.weekoff_patterns else 'None'
        return f"Weekoff Pattern - {self.employee.name} ({patterns_str})"


class LeaveRequest(models.Model):
    """Leave applications and approvals"""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey("regface.Employee", on_delete=models.CASCADE, related_name="leave_requests")
    location = models.ForeignKey("regface.Location", on_delete=models.CASCADE, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="leave_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.IntegerField()  # Calculated (excluding weekoffs/holidays)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    approved_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_leaves")
    approved_on = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    applied_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="applied_leaves")
    applied_on = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-applied_on']
        indexes = [
            models.Index(fields=['employee', 'start_date', 'end_date']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.start_date} to {self.end_date} ({self.status})"


class LeaveBalance(models.Model):
    """Track leave quota and usage"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey("regface.Employee", on_delete=models.CASCADE, related_name="leave_balances")
    location = models.ForeignKey("regface.Location", on_delete=models.CASCADE, related_name="leave_balances")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="leave_balances")
    year = models.IntegerField()  # 2024, 2025, etc.
    total_allocated = models.IntegerField()  # Annual quota
    used_days = models.IntegerField(default=0)
    pending_days = models.IntegerField(default=0)  # Days in pending requests
    carry_forward_from_previous = models.IntegerField(default=0)
    available_days = models.IntegerField()  # Calculated: total_allocated + carry_forward - used - pending
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('employee', 'leave_type', 'year')]
        ordering = ['-year', 'leave_type']
        indexes = [
            models.Index(fields=['employee', 'year']),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.leave_type.leave_code} ({self.year}): {self.available_days} available"


class ManualAttendance(models.Model):
    """Manual attendance override (admin marks present/absent)"""
    STATUS_CHOICES = [
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent'),
        ('HALF_DAY', 'Half Day'),
        ('HOLIDAY', 'Holiday'),
        ('WEEKOFF', 'Weekoff'),
        ('LEAVE', 'Leave'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey("regface.Employee", on_delete=models.CASCADE, related_name="manual_attendances")
    location = models.ForeignKey("regface.Location", on_delete=models.CASCADE, related_name="manual_attendances")
    attendance_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="manual_attendances")
    check_in_time = models.TimeField(null=True, blank=True)  # Manual check-in override
    check_out_time = models.TimeField(null=True, blank=True)  # Manual check-out override
    remarks = models.TextField(null=True, blank=True)
    marked_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="marked_manual_attendances")
    marked_on = models.DateTimeField(auto_now_add=True)
    is_override = models.BooleanField(default=True)  # Overrides face recognition

    class Meta:
        unique_together = [('employee', 'attendance_date')]
        ordering = ['-attendance_date']
        indexes = [
            models.Index(fields=['employee', 'attendance_date']),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.attendance_date} ({self.status})"
