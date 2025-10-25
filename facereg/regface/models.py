from django.db import models

class Employee(models.Model):
    name = models.CharField(max_length=100)
    face_encoding = models.BinaryField()  # Store serialized face encoding

class AttendanceLog(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)

# Create your models here.
