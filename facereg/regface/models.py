from django.db import models

class Employee(models.Model):
    name = models.CharField(max_length=100)
    face_encoding = models.BinaryField()

    def __str__(self):
        return self.name

class AttendanceLog(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)  # auto timestamp

    def __str__(self):
        return f"{self.employee.name} - {self.timestamp}"
