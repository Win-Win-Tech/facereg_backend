from django.db import models
#from django.utils import timezone

class Employee(models.Model):
    name = models.CharField(max_length=100)
    face_encoding = models.BinaryField()
    photo = models.BinaryField(null=True, blank=True)  # Store image blob

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