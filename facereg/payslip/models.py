import uuid
from django.db import models


class PayslipTemplate(models.Model):
    """Payslip layout/design template - ONE per location"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.OneToOneField("regface.Location", on_delete=models.CASCADE, related_name="payslip_template")
    
    # Company Info
    company_logo = models.ImageField(upload_to='payslip_logos/', null=True, blank=True)
    company_name = models.CharField(max_length=200, null=True, blank=True)
    company_address = models.TextField(null=True, blank=True)
    company_email = models.EmailField(null=True, blank=True)
    company_phone = models.CharField(max_length=20, null=True, blank=True)
    company_gstin = models.CharField(max_length=15, null=True, blank=True)
    
    # Layout Settings
    header_text = models.CharField(max_length=200, null=True, blank=True)
    header_color = models.CharField(max_length=7, default='#1e40af')  # Hex color
    header_alignment = models.CharField(max_length=10, default='center')  # left/center/right
    footer_text = models.TextField(null=True, blank=True)
    footer_color = models.CharField(max_length=7, default='#64748b')
    
    # Layout Positions (JSON)
    layout_config = models.JSONField(default=dict, blank=True)  # {
    #     "earnings_position": "left",  # left/right
    #     "deductions_position": "right",
    #     "employee_info_position": "top",  # top/bottom
    #     "show_qr_code": True,
    #     "show_breakdown": True
    # }
    
    # PDF Settings
    page_size = models.CharField(max_length=10, default='A4')  # A4/A5
    orientation = models.CharField(max_length=10, default='portrait')  # portrait/landscape
    font_size = models.IntegerField(default=10)
    
    created_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="created_payslip_templates")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payslip Template - {self.location.name}"


class PayslipFieldConfig(models.Model):
    """Payslip field configuration template - MANY per location"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey("regface.Location", on_delete=models.CASCADE, related_name="payslip_field_configs")
    config_name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    created_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="created_payslip_field_configs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [('location', 'config_name')]
        ordering = ['config_name']

    def __str__(self):
        return f"{self.config_name} - {self.location.name}"


class PayslipField(models.Model):
    """Individual field (earning/deduction) within a field config"""
    FIELD_TYPES = [
        ('EARNING', 'Earning'),
        ('DEDUCTION', 'Deduction'),
        ('INFO', 'Information'),
    ]
    
    VALUE_TYPES = [
        ('PERCENTAGE', 'Percentage of Gross'),
        ('FIXED', 'Fixed Amount'),
        ('CALCULATION', 'Calculation Script'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    field_config = models.ForeignKey(PayslipFieldConfig, on_delete=models.CASCADE, related_name='fields')
    
    # Field Details
    field_name = models.CharField(max_length=100)  # Display name: "Basic Salary"
    field_code = models.CharField(max_length=50)  # Internal code: "BASIC"
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES)
    
    # Calculation
    value_type = models.CharField(max_length=20, choices=VALUE_TYPES)
    value = models.CharField(max_length=500)  # "60" or "2000" or "absent_days * deduction_per_day"
    
    # Display
    display_order = models.IntegerField()  # Priority/Order (1, 2, 3...)
    is_visible = models.BooleanField(default=True)
    default_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [('field_config', 'field_code')]
        ordering = ['display_order']

    def __str__(self):
        return f"{self.field_name} ({self.field_code}) - {self.field_config.config_name}"


class PayslipRecord(models.Model):
    """Generated payslip records"""
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('APPROVED', 'Approved'),
        ('PAID', 'Paid'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey("regface.Employee", on_delete=models.CASCADE, related_name="payslips")
    field_config = models.ForeignKey(PayslipFieldConfig, on_delete=models.PROTECT, related_name="payslips")
    template = models.ForeignKey(PayslipTemplate, on_delete=models.PROTECT, related_name="payslips")
    
    # Month
    month = models.CharField(max_length=7)  # Format: YYYY-MM
    
    # Attendance Data (Enhanced)
    present_days = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # Can be 28.5 for half-days
    absent_days = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # Can be 1.5 for half-days
    paid_leave_days = models.IntegerField(default=0)  # Approved paid leaves
    unpaid_leave_days = models.IntegerField(default=0)  # Approved unpaid leaves
    holiday_count = models.IntegerField(default=0)  # Location holidays
    weekoff_count = models.IntegerField(default=0)  # Weekoff days
    working_days = models.IntegerField(default=0)  # Total working days (excludes holidays/weekoffs)
    
    # Calculated Totals
    gross_salary = models.DecimalField(max_digits=10, decimal_places=2)
    total_earnings = models.DecimalField(max_digits=10, decimal_places=2)
    total_deductions = models.DecimalField(max_digits=10, decimal_places=2)
    net_pay = models.DecimalField(max_digits=10, decimal_places=2)
    
    # All Field Values (JSON)
    field_values = models.JSONField(default=dict)  # {"BASIC": 18000.00, "HRA": 7200.00, ...}
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    notes = models.TextField(null=True, blank=True)
    
    # Audit
    generated_on = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, related_name="generated_payslips")
    approved_on = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey("regface.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_payslips")
    
    # PDF File (optional - can generate on-demand)
    pdf_file = models.FileField(upload_to='payslips/', null=True, blank=True)

    class Meta:
        unique_together = [('employee', 'month')]
        ordering = ['-generated_on']

    def __str__(self):
        return f"Payslip {self.month} - {self.employee.name}"
