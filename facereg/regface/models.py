from django.db import models
#from django.utils import timezone

class Employee(models.Model):
    name = models.CharField(max_length=100)
    face_encoding = models.BinaryField()
    photo = models.BinaryField(null=True, blank=True)
    base_salary = models.DecimalField(max_digits=10, decimal_places=2, default=30000)
    deduction_per_day = models.DecimalField(max_digits=10, decimal_places=2, default=500)

    def __str__(self):
        return self.name

class AttendanceLog(models.Model):
    CHECKIN = "checkin"
    CHECKOUT = "checkout"
    TYPE_CHOICES = [
        (CHECKIN, "Check-in"),
        (CHECKOUT, "Check-out"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=CHECKIN)  # ✅ Add default

    def __str__(self):
        return f"{self.employee.name} - {self.type} at {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
    
from django.db import models
from decimal import Decimal

class PayrollRecord(models.Model):
    employee = models.ForeignKey("Employee", on_delete=models.CASCADE)
    month = models.CharField(max_length=7)  # Format: YYYY-MM
    present_days = models.IntegerField(default=0)
    absent_days = models.IntegerField(default=0)
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    deduction_per_day = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pf_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    esi_deduction = models.DecimalField(max_digits=10, decimal_places=2)
    net_pay = models.DecimalField(max_digits=10, decimal_places=2)
    generated_on = models.DateTimeField(auto_now_add=True)


