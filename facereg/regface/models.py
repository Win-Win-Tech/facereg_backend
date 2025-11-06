from django.db import models
#from django.utils import timezone

class Employee(models.Model):
    name = models.CharField(max_length=100)
    face_encoding = models.BinaryField()

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

    # class Meta:
    #     constraints = [
    #         models.UniqueConstraint(
    #             fields=["employee", "type"],
    #             condition=models.Q(timestamp__date=timezone.now().date()),
    #             name="unique_daily_checkin_checkout"
    #         )
    #     ]

    #def __str__(self):
    #    return f"{self.employee.name} - {self.timestamp}"

    def __str__(self):
        return f"{self.employee.name} - {self.type} at {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"